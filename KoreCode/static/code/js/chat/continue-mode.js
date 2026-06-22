export function createContinueModeController({
  thread,
  btnModeContinue,
  getContinueContext,
  insertContinuation,
  getCurrentPath,
  getConversationExternalId,
  fetchConversationThread,
  postConversationFollowup,
  setMode,
  setGenerating,
  save,
  consumeManualStop,
  isTransientStreamInterrupt,
  errorText,
}) {
  let continueStatus         = null;
  let continueController     = null;
  let continuePreviewPath    = null;
  let continuePendingRunId   = null;
  let continuePendingContext = null;
  let continueResumeInFlight = false;
  let continueInProgress     = false;

  function stateSnapshot() {
    if (!continueInProgress && !continuePendingRunId) {
      return null;
    }
    return {
      inProgress:     Boolean(continueInProgress),
      pendingRunId:   continuePendingRunId ?? null,
      pendingContext: continuePendingContext ?? null,
      previewPath:    continuePreviewPath ?? null,
    };
  }

  function restoreState(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') return;
    continueInProgress     = Boolean(snapshot.inProgress);
    continuePendingRunId   = typeof snapshot.pendingRunId === 'string' && snapshot.pendingRunId ? snapshot.pendingRunId : null;
    continuePendingContext = snapshot.pendingContext && typeof snapshot.pendingContext === 'object' ? snapshot.pendingContext : null;
    continuePreviewPath    = typeof snapshot.previewPath === 'string' && snapshot.previewPath ? snapshot.previewPath : null;
  }

  function resetState() {
    continueStatus?.remove();
    continueStatus         = null;
    continueController     = null;
    continuePreviewPath    = null;
    continuePendingRunId   = null;
    continuePendingContext = null;
    continueResumeInFlight = false;
    continueInProgress     = false;
    if (btnModeContinue) btnModeContinue.disabled = false;
    save();
  }

  function clearContinueStatus() {
    continueStatus?.remove();
    continueStatus = null;
  }

  function showContinueStatus(state, extra) {
    clearContinueStatus();
    const el = document.createElement('div');
    el.id = 'continue-status';
    if (state === 'idle') {
      el.className   = 'continue-status continue-status--idle';
      el.textContent = 'Run /continue to generate from cursor.';
    } else if (state === 'thinking') {
      el.className = 'continue-status continue-status--thinking';
      el.innerHTML = 'Generating... <span class="chat-thinking-dots"><span>&bull;</span><span>&bull;</span><span>&bull;</span></span>';
    } else if (state === 'preview') {
      el.className = 'continue-status continue-status--preview';
      el.innerHTML =
        '<button id="btn-continue-accept" class="continue-btn continue-btn--accept">Accept</button>' +
        '<button id="btn-continue-cancel" class="continue-btn continue-btn--dismiss">Dismiss</button>';
    } else if (state === 'accepted') {
      el.className   = 'continue-status continue-status--accepted';
      el.textContent = `Accepted. ${extra ?? ''}`;
    } else if (state === 'cancelled') {
      el.className   = 'continue-status continue-status--cancelled';
      el.textContent = 'Cancelled.';
    } else if (state === 'error') {
      el.className   = 'continue-status continue-status--error';
      el.textContent = `Error: ${extra ?? 'unknown'}`;
    }
    thread.appendChild(el);
    continueStatus    = el;
    thread.scrollTop  = thread.scrollHeight;
  }

  function clearContinuePreviewState() {
    continueController  = null;
    continuePreviewPath = null;
  }

  function clearContinuePendingRun() {
    continuePendingRunId   = null;
    continuePendingContext = null;
    continueResumeInFlight = false;
    save();
  }

  function bindContinuePreviewButtons() {
    const acceptBtn = document.getElementById('btn-continue-accept');
    const cancelBtn = document.getElementById('btn-continue-cancel');
    if (!continueController || !acceptBtn || !cancelBtn) {
      return;
    }
    acceptBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      continueController?.accept();
    });
    cancelBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      continueController?.cancel();
    });
  }

  async function waitForContinueReply() {
    while (true) {
      if (consumeManualStop()) {
        throw new DOMException('Polling aborted', 'AbortError');
      }
      const payload = await fetchConversationThread();
      if (!payload?.pending_response) {
        return String(payload?.last_assistant?.content || '');
      }
      await new Promise((resolve) => window.setTimeout(resolve, 900));
    }
  }

  function wirePreviewController(insertion, insertionLineCount, continuePath, continueOffset) {
    continueInProgress  = false;
    showContinueStatus('preview');
    continueController  = insertContinuation(insertion, { path: continuePath, offset: continueOffset });
    continuePreviewPath = continuePath;
    save();
    bindContinuePreviewButtons();

    const origAccept = continueController.accept.bind(continueController);
    const origCancel = continueController.cancel.bind(continueController);

    continueController.accept = () => {
      origAccept();
      continueInProgress = false;
      showContinueStatus('accepted', `${insertionLineCount} line(s) inserted.`);
      clearContinuePreviewState();
      save();
      if (btnModeContinue) btnModeContinue.disabled = false;
      setTimeout(() => setMode('chat'), 1500);
    };

    continueController.cancel = () => {
      origCancel();
      continueInProgress = false;
      showContinueStatus('cancelled');
      clearContinuePreviewState();
      save();
      if (btnModeContinue) btnModeContinue.disabled = false;
      setTimeout(() => setMode('chat'), 1500);
    };
  }

  async function runContinue() {
    const ctx = getContinueContext?.();
    if (!ctx) {
      showContinueStatus('error', 'No file open.');
      return;
    }

    showContinueStatus('thinking');
    continueInProgress = true;
    if (btnModeContinue) btnModeContinue.disabled = true;
    setGenerating(true);

    const systemNote = [
      'You are a code completion assistant.',
      'You will be given code before and after a cursor position.',
      'Reply with ONLY the code to insert at the cursor so that it fits naturally between the prefix and suffix.',
      'Do not repeat any of the provided code.',
      'Do not include markdown fences, explanations, or commentary.',
      'Output only the raw code to insert at the cursor.',
    ].join(' ');

    const prompt = ctx.suffix?.trim()
      ? `${systemNote}\n\n[CODE BEFORE CURSOR]\n\`\`\`\n${ctx.text}\n\`\`\`\n\n[CODE AFTER CURSOR]\n\`\`\`\n${ctx.suffix}\n\`\`\``
      : `${systemNote}\n\n\`\`\`\n${ctx.text}\n\`\`\``;

    try {
      const payload = await postConversationFollowup({
        prompt,
        visibleText: '',
        outboundSenderDisplay: '__korecode_internal__',
      });
      continuePendingRunId   = String(payload?.conversation_id || getConversationExternalId() || 'continue');
      continuePendingContext = { path: ctx.path, offset: ctx.offset };
      save();

      const reply = await waitForContinueReply();
      clearContinuePendingRun();

      if (!reply.trim()) {
        showContinueStatus('cancelled', '');
        showContinueStatus('error', 'Empty response.');
        return;
      }

      const insertion          = reply.replace(/^\n/, '');
      const insertionLineCount = insertion.split('\n').length;
      wirePreviewController(insertion, insertionLineCount, ctx.path, ctx.offset);
    } catch (err) {
      if (consumeManualStop()) {
        continueInProgress = false;
        clearContinuePendingRun();
        save();
        showContinueStatus('cancelled');
        if (btnModeContinue) btnModeContinue.disabled = false;
        return;
      }
      if (isTransientStreamInterrupt(err) && continuePendingRunId) {
        continueInProgress = true;
        save();
        showContinueStatus('thinking');
        return;
      }
      continueInProgress = false;
      clearContinuePendingRun();
      save();
      showContinueStatus('error', errorText(err));
      if (btnModeContinue) btnModeContinue.disabled = false;
    } finally {
      setGenerating(false);
    }
  }

  async function resumeContinueIfNeeded() {
    if (!continueInProgress || !continuePendingRunId || continueResumeInFlight) {
      return;
    }
    continueResumeInFlight = true;
    setGenerating(true);

    try {
      const reply = await waitForContinueReply();
      clearContinuePendingRun();
      if (!reply.trim()) {
        continueInProgress = false;
        showContinueStatus('error', 'Continue returned no content.');
        if (btnModeContinue) btnModeContinue.disabled = false;
        return;
      }

      const insertion          = reply.replace(/^\n/, '');
      const insertionLineCount = insertion.split('\n').length;
      const continuePath       = continuePendingContext?.path ?? getCurrentPath();
      const continueOffset     = typeof continuePendingContext?.offset === 'number'
        ? continuePendingContext.offset
        : undefined;
      wirePreviewController(insertion, insertionLineCount, continuePath, continueOffset);
    } catch (err) {
      if (consumeManualStop()) {
        continueInProgress = false;
        clearContinuePendingRun();
        save();
        showContinueStatus('cancelled');
        if (btnModeContinue) btnModeContinue.disabled = false;
        return;
      }
      if (isTransientStreamInterrupt(err)) {
        continueInProgress = true;
        save();
        showContinueStatus('thinking');
      } else {
        continueInProgress = false;
        clearContinuePendingRun();
        save();
        showContinueStatus('error', errorText(err));
        if (btnModeContinue) btnModeContinue.disabled = false;
      }
    } finally {
      continueResumeInFlight = false;
      setGenerating(false);
    }
  }

  function onTabChange(path) {
    if (continueInProgress) {
      showContinueStatus('thinking');
      void resumeContinueIfNeeded();
      return;
    }
    if (continueController && path && continuePreviewPath === path) {
      showContinueStatus('preview');
      bindContinuePreviewButtons();
      return;
    }
    if (continueController && continuePreviewPath && path !== continuePreviewPath) {
      showContinueStatus('error', 'Continue preview is attached to a different file tab. Return there to Accept or Dismiss.');
      return;
    }
    showContinueStatus('idle');
  }

  return {
    stateSnapshot,
    restoreState,
    resetState,
    runContinue,
    resumeContinueIfNeeded,
    onTabChange,
    clearStatus:   clearContinueStatus,
    isInProgress:  () => continueInProgress,
  };
}
