export function createContinueModeController({
  thread,
  btnModeContinue,
  getContinueContext,
  insertContinuation,
  agentBase,
  getCurrentPath,
  setMode,
  setGenerating,
  save,
  consumeManualStop,
  isTransientStreamInterrupt,
  errorText,
  safeCancelReader,
  setActiveReader,
}) {
  let continueStatus = null;
  let continueController = null;
  let continuePreviewPath = null;
  let continuePendingRunId = null;
  let continuePendingContext = null;
  let continueResumeInFlight = false;
  let continueInProgress = false;

  function stateSnapshot() {
    if (!continueInProgress && !continuePendingRunId) {
      return null;
    }
    return {
      inProgress: Boolean(continueInProgress),
      pendingRunId: continuePendingRunId ?? null,
      pendingContext: continuePendingContext ?? null,
      previewPath: continuePreviewPath ?? null,
    };
  }

  function restoreState(snapshot) {
    if (!snapshot || typeof snapshot !== 'object') return;
    continueInProgress = Boolean(snapshot.inProgress);
    continuePendingRunId = typeof snapshot.pendingRunId === 'string' && snapshot.pendingRunId ? snapshot.pendingRunId : null;
    continuePendingContext = snapshot.pendingContext && typeof snapshot.pendingContext === 'object' ? snapshot.pendingContext : null;
    continuePreviewPath = typeof snapshot.previewPath === 'string' && snapshot.previewPath ? snapshot.previewPath : null;
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
      el.className = 'continue-status continue-status--idle';
      el.textContent = 'Run /continue to generate from cursor.';
    } else if (state === 'thinking') {
      el.className = 'continue-status continue-status--thinking';
      el.innerHTML = 'Generating… <span class="chat-thinking-dots"><span>•</span><span>•</span><span>•</span></span>';
    } else if (state === 'preview') {
      el.className = 'continue-status continue-status--preview';
      el.innerHTML =
        '<button id="btn-continue-accept" class="continue-btn continue-btn--accept">Accept</button>' +
        '<button id="btn-continue-cancel" class="continue-btn continue-btn--dismiss">Dismiss</button>';
    } else if (state === 'accepted') {
      el.className = 'continue-status continue-status--accepted';
      el.textContent = `Accepted. ${extra ?? ''}`;
    } else if (state === 'cancelled') {
      el.className = 'continue-status continue-status--cancelled';
      el.textContent = 'Cancelled.';
    } else if (state === 'error') {
      el.className = 'continue-status continue-status--error';
      el.textContent = `Error: ${extra ?? 'unknown'}`;
    }
    thread.appendChild(el);
    continueStatus = el;
    thread.scrollTop = thread.scrollHeight;
  }

  function clearContinuePreviewState() {
    continueController = null;
    continuePreviewPath = null;
  }

  function clearContinuePendingRun() {
    continuePendingRunId = null;
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

  async function streamContinue(base, runId) {
    const response = await fetch(`${base}/runs/${encodeURIComponent(runId)}/stream`);
    if (!response.ok) throw new Error(`Stream ${response.status}`);

    const reader = response.body.getReader();
    setActiveReader(reader);
    const decoder = new TextDecoder();
    let buffer = '';
    let reply = '';

    try {
      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          let event;
          try { event = JSON.parse(raw); } catch { continue; }
          if (event.type === 'response') reply += event.response ?? '';
          else if (event.type === 'done') break outer;
        }
      }
    } catch (err) {
      if (!(reply.trim() && isTransientStreamInterrupt(err))) {
        throw err;
      }
    } finally {
      safeCancelReader(reader);
      setActiveReader(null);
    }
    return reply;
  }

  function wirePreviewController(insertion, insertionLineCount, continuePath, continueOffset) {
    continueInProgress = false;
    showContinueStatus('preview');
    continueController = insertContinuation(insertion, { path: continuePath, offset: continueOffset });
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

    const sessionId = 'kc_continue';
    const base = agentBase();

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
      const resp = await fetch(`${base}/sessions/${encodeURIComponent(sessionId)}/prompt`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt }),
      });
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);

      const { run_id } = await resp.json();
      continuePendingRunId = run_id;
      continuePendingContext = { path: ctx.path, offset: ctx.offset };
      save();

      const reply = await streamContinue(base, run_id);
      clearContinuePendingRun();

      if (!reply.trim()) {
        showContinueStatus('cancelled', '');
        showContinueStatus('error', 'Empty response.');
        return;
      }

      const insertion = reply.replace(/^\n/, '');
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
      setActiveReader(null);
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

    const base = agentBase();
    try {
      const reply = await streamContinue(base, continuePendingRunId);
      clearContinuePendingRun();
      if (!reply.trim()) {
        continueInProgress = false;
        showContinueStatus('error', 'Continue returned no content.');
        if (btnModeContinue) btnModeContinue.disabled = false;
        return;
      }

      const insertion = reply.replace(/^\n/, '');
      const insertionLineCount = insertion.split('\n').length;
      const continuePath = continuePendingContext?.path ?? getCurrentPath();
      const continueOffset = typeof continuePendingContext?.offset === 'number'
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
    runContinue,
    resumeContinueIfNeeded,
    onTabChange,
    clearStatus: clearContinueStatus,
    isInProgress: () => continueInProgress,
  };
}
