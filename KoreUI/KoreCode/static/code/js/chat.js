// chat.js
//
// Chat panel for KoreCode.
import { extractAgentEnvelope } from './chat/agent-loop.js';
import { createThreadUI } from './chat/thread-ui.js';
import { createContinueModeController } from './chat/continue-mode.js';

const _STATE_KEY = 'korecode.chat-state';
const _WIDTH_KEY = 'korecode-chat-w';
const _WORKSPACE_CONTEXT_KEY = 'korecode.workspace-context';
const _MIN_PANEL_W = 260;
const _MAX_PANEL_W = 720;
const _DEFAULT_PANEL_W = 340;
const _WORKSPACE_THREAD_KEY = '__workspace__';
const _CONVERSATION_STORE_KEY = '__conversation__';

// In-memory chat thread store: path -> Array<{ role: 'user'|'assistant', text: string }>
const _threads = new Map();

function _saveState(open, mode, threads = {}, pendingRuns = {}, continueState = null, conversationExternalId = null) {
  try {
    localStorage.setItem(_STATE_KEY, JSON.stringify({ open, mode, threads, pendingRuns, continueState, conversationExternalId }));
  } catch (_) {}
}

function _loadState() {
  try {
    const raw = localStorage.getItem(_STATE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (_) {
    return null;
  }
}

function _loadWorkspaceContextEnabled() {
  try {
    const raw = localStorage.getItem(_WORKSPACE_CONTEXT_KEY);
    if (raw == null) return true;
    return raw !== '0';
  } catch (_) {
    return true;
  }
}

function _saveWorkspaceContextEnabled(value) {
  try {
    localStorage.setItem(_WORKSPACE_CONTEXT_KEY, value ? '1' : '0');
  } catch (_) {}
}

// SSE reader active during a streaming response.
let _activeReader = null;

/**
 * Initialise the chat panel.
 * @param {{
 *   getActiveTab:       () => { path: string } | null,
 *   getContinueContext: () => { path: string, text: string, suffix?: string, offset: number } | null,
 *   insertContinuation: (text: string, opts?: object) => { accept(): void, cancel(): void },
 * }} opts
 * @returns {{ onTabChange: (path: string | null) => void, onSelectionChange: (payload: any) => void }}
 */
export function initChat({
  getActiveTab,
  getContinueContext,
  insertContinuation,
  insertFromChat = null,
  getEditorSelection = null,
  getCursorInfo = null,
}) {
  const panel = document.getElementById('chat-panel');
  const splitter = document.getElementById('chat-splitter');
  const thread = document.getElementById('chat-thread');
  const input = document.getElementById('chat-input');
  const sendBtn = document.getElementById('btn-chat-send');
  const aiBtn = document.getElementById('btn-ai');
  const btnModeChat = document.getElementById('btn-mode-chat');
  const btnModeContinue = document.getElementById('btn-mode-continue');
  const btnModeExplain = document.getElementById('btn-mode-explain');
  const btnModeBugHunt = document.getElementById('btn-mode-bughunt');
  const btnModeRefactor = document.getElementById('btn-mode-refactor');
  const btnModeTests = document.getElementById('btn-mode-tests');
  const btnChatRetry = document.getElementById('btn-chat-retry');
  const btnChatClear = document.getElementById('btn-chat-clear');
  const btnChatStop = document.getElementById('btn-chat-stop');
  const btnWorkspaceContext = document.getElementById('btn-workspace-context');
  const linkedConversation = document.getElementById('chat-linked-conversation');
  const linkedConversationName = document.getElementById('chat-linked-conversation-name');
  const selectionChip = document.getElementById('chat-selection-chip');
  const selectionLabel = document.getElementById('chat-selection-label');
  const progressNote = document.getElementById('chat-progress-note');

  let _currentSelection = null;
  let _currentCursor = { line: 1, column: 1, offset: 0 };

  let _mode = 'chat';
  let _panelOpen = false;
  let _dragStartX = null;
  let _dragStartW = null;
  const _pendingRuns = new Map(); // path -> runId
  let _manualStopRequested = false;
  let _isGenerating = false;
  let _workspaceContextEnabled = _loadWorkspaceContextEnabled();
  let _continueControllerApi = { stateSnapshot: () => null };
  let _conversationExternalId = null;
  let _conversationTitle = null;
  let _historyIndex = -1;
  let _historyDraft = '';

  const _threadUI = createThreadUI({
    thread,
    insertFromChat,
    createEditProposal: async (edits) => {
      const resp = await fetch('/api/edit-proposals', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          edits,
          run_id: _pendingRuns.get(_CONVERSATION_STORE_KEY) || null,
          source: 'assistant',
          summary: 'Assistant structured edits',
        }),
      });
      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`);
      }
      return await resp.json();
    },
    applyEditProposal: async (proposalId) => {
      const resp = await fetch(`/api/edit-proposals/${encodeURIComponent(proposalId)}/apply`, {
        method: 'POST',
      });
      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`);
      }
      return await resp.json();
    },
    reloadTabs: (paths) => window.__kcReloadTabs?.(paths),
    saveTabs: (paths) => window.__kcSaveTabs?.(paths),
  });

  function _refreshCursorFromEditor() {
    const cursor = getCursorInfo?.();
    if (cursor && typeof cursor.line === 'number' && typeof cursor.column === 'number') {
      _currentCursor = {
        line: cursor.line,
        column: cursor.column,
        offset: typeof cursor.offset === 'number' ? cursor.offset : _currentCursor.offset,
      };
    }
  }

  function _refreshSelectionFromEditor() {
    _currentSelection = getEditorSelection?.() ?? null;
    _refreshCursorFromEditor();
    _updateSelectionChip();
  }

  function _updateSelectionChip() {
    if (!selectionChip) return;
    if (panel.hidden) {
      selectionChip.hidden = true;
      return;
    }
    const lines = _currentSelection ? _currentSelection.split('\n').length : 0;
    if (lines > 0) {
      const startLine = _currentCursor.line;
      const endLine   = startLine + lines - 1;
      selectionLabel.textContent = `[selected L${startLine}:L${endLine}]`;
    } else {
      selectionLabel.textContent = `[insert L${_currentCursor.line}:C${_currentCursor.column}]`;
    }
    selectionChip.hidden = false;
  }

  function _setConversationTitle(value) {
    const next = typeof value === 'string' && value.trim() ? value.trim() : null;
    _conversationTitle = next;
    if (!linkedConversation || !linkedConversationName) return;
    linkedConversation.hidden = !next;
    if (next) {
      linkedConversationName.textContent = next;
      linkedConversationName.title = next;
    } else {
      linkedConversationName.textContent = '';
      linkedConversationName.removeAttribute('title');
    }
  }

  function _userPromptHistory() {
    const messages = _threads.get(_CONVERSATION_STORE_KEY) ?? [];
    return messages
      .filter((msg) => msg?.role === 'user')
      .map((msg) => String(msg?.text || ''))
      .filter((text) => text.trim().length > 0);
  }

  function _resetPromptHistoryCursor() {
    _historyIndex = -1;
    _historyDraft = '';
  }

  function _setInputValue(text) {
    input.value = String(text || '');
    _autosize(input);
    const pos = input.value.length;
    input.setSelectionRange(pos, pos);
  }

  function _navigatePromptHistory(direction) {
    const history = _userPromptHistory();
    if (!history.length) return false;

    if (direction < 0) {
      if (_historyIndex === -1) {
        _historyDraft = input.value;
        _historyIndex = history.length - 1;
      } else if (_historyIndex > 0) {
        _historyIndex -= 1;
      } else {
        return false;
      }
      _setInputValue(history[_historyIndex] ?? '');
      return true;
    }

    if (_historyIndex === -1) {
      return false;
    }
    if (_historyIndex < history.length - 1) {
      _historyIndex += 1;
      _setInputValue(history[_historyIndex] ?? '');
      return true;
    }
    _historyIndex = -1;
    _setInputValue(_historyDraft);
    _historyDraft = '';
    return true;
  }

  function _shouldUseHistoryKey(event) {
    if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return false;
    if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return false;
    const start = input.selectionStart ?? 0;
    const end = input.selectionEnd ?? 0;
    if (start !== end) return false;
    const value = input.value;
    const before = value.slice(0, start);
    const after = value.slice(start);
    if (event.key === 'ArrowUp') {
      return !before.includes('\n');
    }
    return !after.includes('\n');
  }

  function _readSavedWidth() {
    try {
      const raw = localStorage.getItem(_WIDTH_KEY);
      if (!raw) return _DEFAULT_PANEL_W;
      const val = Number(raw);
      if (!Number.isFinite(val)) return _DEFAULT_PANEL_W;
      return Math.max(_MIN_PANEL_W, Math.min(_MAX_PANEL_W, val));
    } catch (_) {
      return _DEFAULT_PANEL_W;
    }
  }

  function _saveWidth(width) {
    try {
      localStorage.setItem(_WIDTH_KEY, String(width));
    } catch (_) {}
  }

  function _setPanelWidth(width) {
    const w = Math.max(_MIN_PANEL_W, Math.min(_MAX_PANEL_W, Math.round(width)));
    panel.style.width = `${w}px`;
    _saveWidth(w);
  }

  function _stopResizeDrag() {
    if (_dragStartX === null) return;
    _dragStartX = null;
    _dragStartW = null;
    splitter.classList.remove('is-dragging');
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
  }

  function _setPanelOpen(open) {
    _panelOpen = Boolean(open);
    panel.hidden = !_panelOpen;
    splitter.hidden = !_panelOpen;
    aiBtn.classList.toggle('is-active', _panelOpen);
    _save();
    _syncThinkingNote();
    _updateSelectionChip();
  }

  function _consumeManualStop() {
    if (!_manualStopRequested) return false;
    _manualStopRequested = false;
    return true;
  }

  function _renderWorkspaceContextToggle() {
    if (!btnWorkspaceContext) return;
    btnWorkspaceContext.textContent = _workspaceContextEnabled ? 'Workspace: On' : 'Workspace: Off';
    btnWorkspaceContext.setAttribute('aria-pressed', _workspaceContextEnabled ? 'true' : 'false');
    btnWorkspaceContext.classList.toggle('kcui-tag--accent', _workspaceContextEnabled);
    btnWorkspaceContext.classList.toggle('kcui-tag--dim', !_workspaceContextEnabled);
  }

  async function _rebuildWorkspaceMenu({ announcePath = null, quiet = false } = {}) {
    try {
      const resp = await fetch('/api/workspace-menu/rebuild', { method: 'POST' });
      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`);
      }
      const payload = await resp.json();
      if (announcePath) {
        const fileName = payload?.menu_file_name || 'KoreCodeWorkspace.md';
        _localAssistantMessage(
          announcePath,
          `Workspace menu refreshed: ${fileName} (${payload?.file_count ?? 0} files indexed).`
        );
      }
      return payload;
    } catch (err) {
      if (!quiet && announcePath) {
        _localAssistantMessage(announcePath, `Workspace menu refresh failed: ${_errorText(err)}`);
      }
      throw err;
    }
  }

  function _setGenerating(active) {
    _isGenerating = Boolean(active);
    sendBtn.disabled = _isGenerating;
    if (btnChatStop) {
      btnChatStop.hidden = !_isGenerating;
      btnChatStop.disabled = !_isGenerating;
    }
  }

  function _pendingToObject() {
    const out = {};
    for (const [path, runId] of _pendingRuns) out[path] = runId;
    return out;
  }

  function _save() {
    const threads = {};
    for (const [path, msgs] of _threads) threads[path] = msgs;
    _saveState(
      _panelOpen,
      _mode,
      threads,
      _pendingToObject(),
      _continueControllerApi.stateSnapshot(),
      _conversationExternalId,
    );
  }

  function _setPending(path, runId) {
    if (!path || !runId) return;
    _pendingRuns.set(_CONVERSATION_STORE_KEY, runId);
    _save();
    _syncThinkingNote();
  }

  function _clearPending(path) {
    if (!path || !_pendingRuns.has(_CONVERSATION_STORE_KEY)) return;
    _pendingRuns.delete(_CONVERSATION_STORE_KEY);
    _save();
    _syncThinkingNote();
  }

  function _syncThinkingNote() {
    if (!progressNote) return;
    if (panel.hidden) {
      progressNote.hidden = true;
      return;
    }
    const hasAnyPending = _pendingRuns.size > 0;
    const hasCurrentPending = _pendingRuns.has(_CONVERSATION_STORE_KEY);
    progressNote.hidden = !hasAnyPending;
    if (progressNote.hidden) return;
    progressNote.textContent = hasCurrentPending ? 'Generating...' : 'Generating... (in another tab)';
  }

  function _lastUserMessage(_path) {
    const messages = _threads.get(_CONVERSATION_STORE_KEY) ?? [];
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i]?.role === 'user' && typeof messages[i].text === 'string' && messages[i].text.trim()) {
        return messages[i].text;
      }
    }
    return null;
  }

  function _clearThread(path) {
    if (!path) return;
    _threads.delete(_CONVERSATION_STORE_KEY);
    _clearPending(path);
    _save();
    renderThread(_CONVERSATION_STORE_KEY);
  }

  async function _clearThreadRemote(path) {
    if (!path) return;
    try {
      const qs = new URLSearchParams({ path });
      if (_conversationExternalId) {
        qs.set('conversation_external_id', _conversationExternalId);
      }
      const resp = await fetch(`/api/chat/thread?${qs.toString()}`, { method: 'DELETE' });
      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`);
      }
      _conversationExternalId = null;
    } catch (err) {
      _localAssistantMessage(path, `Clear thread failed: ${_errorText(err)}`);
      return;
    }
    _clearThread(path);
  }

  function _localAssistantMessage(path, text) {
    if (!path || !text) return;
    _pushMessage(path, { role: 'assistant', text });
    renderThread(_CONVERSATION_STORE_KEY);
  }

  function _applyWorkspaceContext(enabled) {
    _workspaceContextEnabled = Boolean(enabled);
    _saveWorkspaceContextEnabled(_workspaceContextEnabled);
    _renderWorkspaceContextToggle();
  }

  async function _syncWorkspaceContextForConversation(enabled) {
    if (!_conversationExternalId) return;
    const resp = await fetch('/api/chat/workspace-context', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_external_id: _conversationExternalId,
        enabled: Boolean(enabled),
      }),
    });
    if (!resp.ok) {
      throw new Error(`${resp.status} ${resp.statusText}`);
    }
  }

  function _resetConversationState({ keepPanel = true } = {}) {
    _threads.delete(_CONVERSATION_STORE_KEY);
    _pendingRuns.delete(_CONVERSATION_STORE_KEY);
    _conversationExternalId = null;
    _setConversationTitle(null);
    _resetPromptHistoryCursor();
    _manualStopRequested = false;
    _continueControllerApi.resetState?.();
    _save();
    _syncThinkingNote();
    if (keepPanel) {
      renderThread(currentThreadPath());
    }
  }

  async function _applySlashActions(path, actions) {
    for (const action of Array.isArray(actions) ? actions : []) {
      const type = String(action?.type || '');
      if (type === 'clear_thread') {
        await _clearThreadRemote(path);
        continue;
      }
      if (type === 'retry_last_user_message') {
        const last = _lastUserMessage(path);
        if (last) {
          await _send(last, { appendUserMessage: false });
        } else {
          _localAssistantMessage(path, 'No previous user prompt to retry in this conversation.');
        }
        continue;
      }
      if (type === 'set_workspace_context') {
        const enabled = Boolean(action.enabled);
        _applyWorkspaceContext(enabled);
        await _syncWorkspaceContextForConversation(enabled);
        continue;
      }
      if (type === 'set_mode') {
        const mode = String(action.mode || 'chat');
        if (mode === 'continue') {
          setMode('continue');
          if (action.run_continue) {
            void _continueControllerApi.runContinue();
          }
        } else {
          setMode(mode);
        }
      }
    }
  }

  async function _runSlashCommand(path, text) {
    const resp = await fetch('/api/slash', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        current_mode: _mode,
        workspace_context_enabled: _workspaceContextEnabled,
        thread_path: path,
        has_last_user_message: Boolean(_lastUserMessage(path)),
      }),
    });
    if (!resp.ok) {
      throw new Error(`${resp.status} ${resp.statusText}`);
    }
    const payload = await resp.json();
    if (!payload?.handled) {
      return false;
    }
    await _applySlashActions(path, payload.actions);
    for (const message of Array.isArray(payload.messages) ? payload.messages : []) {
      const textOut = String(message?.text || '').trim();
      if (textOut) {
        _localAssistantMessage(path, textOut);
      }
    }
    return true;
  }

  function _errorText(err) {
    if (!err) return 'Unknown error';
    if (typeof err === 'string') return err;
    if (err.message) return err.message;
    return String(err);
  }

  function _isTransientStreamInterrupt(err) {
    const name = typeof err?.name === 'string' ? err.name : '';
    const msg = _errorText(err).toLowerCase();
    return (
      name === 'AbortError'
      || msg.includes('aborted')
      || msg.includes('failed to fetch')
      || msg.includes('illegal access')
      || msg.includes('access is denied')
      || msg.includes('permission denied')
      || msg.includes('securityerror')
      || msg.includes('networkerror')
    );
  }

  function _safeCancelReader(reader) {
    if (!reader) return;
    try {
      reader.cancel();
    } catch (_) {}
  }

  function currentPath() {
    return getActiveTab()?.path ?? null;
  }

  function currentThreadPath() {
    return currentPath() || _WORKSPACE_THREAD_KEY;
  }

  function _delay(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  function _isToolLoopAssistantText(text) {
    const envelope = extractAgentEnvelope(text);
    const requestedTools = Array.isArray(envelope?.tool_requests) ? envelope.tool_requests : [];
    return envelope?.kind === 'tool_requests' && envelope?.next === 'continue' && requestedTools.length > 0;
  }

  function _filterVisibleMessages(messages) {
    return (Array.isArray(messages) ? messages : []).filter((msg) => {
      if (String(msg?.sender_display || '') === '__korecode_internal__') return false;
      if (msg?.role !== 'assistant') return true;
      return !_isToolLoopAssistantText(String(msg?.text || ''));
    });
  }

  function _hydrateThread(path, payload, { render = true } = {}) {
    if (payload?.external_id) {
      _conversationExternalId = String(payload.external_id);
    }
    _setConversationTitle(payload?.title);
    const messages = _filterVisibleMessages(payload?.messages);
    if (messages.length) {
      _threads.set(_CONVERSATION_STORE_KEY, messages);
    } else {
      _threads.delete(_CONVERSATION_STORE_KEY);
    }
    if (payload?.pending_response) {
      const runId = String(payload?.run?.run_id || payload?.conversation_id || 'pending');
      _setPending(path, runId);
    }
    if (render) {
      renderThread(path);
    }
    return payload;
  }

  async function _fetchThread(path) {
    const qs = new URLSearchParams({ path });
    if (_conversationExternalId) {
      qs.set('conversation_external_id', _conversationExternalId);
    }
    qs.set('workspace_context_enabled', _workspaceContextEnabled ? 'true' : 'false');
    const resp = await fetch(`/api/chat/thread?${qs.toString()}`);
    if (!resp.ok) {
      throw new Error(`${resp.status} ${resp.statusText}`);
    }
    const payload = await resp.json();
    return _hydrateThread(path, payload, { render: false });
  }

  function _setConversationExternalId(value) {
    const next = typeof value === 'string' && value ? value : null;
    if (_conversationExternalId === next) return;
    _conversationExternalId = next;
    if (!next) {
      _setConversationTitle(null);
    }
    _save();
  }

  async function _fetchRun(runId) {
    const resp = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
    if (!resp.ok) {
      throw new Error(`${resp.status} ${resp.statusText}`);
    }
    return await resp.json();
  }

  async function _waitForRun(runId) {
    while (true) {
      if (_manualStopRequested) {
        throw new DOMException('Polling aborted', 'AbortError');
      }
      const run = await _fetchRun(runId);
      if (typeof run?.conversation_external_id === 'string' && run.conversation_external_id) {
        _setConversationExternalId(run.conversation_external_id);
      }
      const status = String(run?.status || '');
      if (status === 'completed' || status === 'failed') {
        return run;
      }
      await _delay(900);
    }
  }

  function _setModeButtons(mode) {
    const entries = [
      ['chat', btnModeChat],
      ['continue', btnModeContinue],
      ['explain', btnModeExplain],
      ['bughunt', btnModeBugHunt],
      ['refactor', btnModeRefactor],
      ['tests', btnModeTests],
    ];
    for (const [name, button] of entries) {
      if (!button) continue;
      button.classList.toggle('is-active', mode === name);
    }
  }

  function setMode(mode) {
    _mode = mode;
    _setModeButtons(mode);
    const composer = document.getElementById('chat-composer');
    if (mode === 'continue') {
      composer.hidden = true;
      _continueControllerApi.onTabChange(currentPath());
    } else {
      composer.hidden = false;
      _continueControllerApi.clearStatus?.();
    }
    _save();
  }

  function _scrollBottom() {
    _threadUI.scrollBottom();
  }

  function _appendThinking(label = 'Kore is thinking') {
    return _threadUI.appendThinking(label);
  }

  function _appendLivePre() {
    return _threadUI.appendLivePre();
  }

  function renderThread(path) {
    _threadUI.renderThread(path, path ? (_threads.get(_CONVERSATION_STORE_KEY) ?? []) : []);
  }

  _continueControllerApi = createContinueModeController({
    thread,
    btnModeContinue,
    getContinueContext,
    insertContinuation,
    getCurrentPath: currentPath,
    getConversationExternalId: () => _conversationExternalId,
    setConversationExternalId: (value) => _setConversationExternalId(value),
    startContinueRun: async (payload) => {
      const resp = await fetch('/api/chat/continue-runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...payload,
          conversation_external_id: payload?.conversation_external_id ?? _conversationExternalId,
          workspace_context_enabled: _workspaceContextEnabled,
        }),
      });
      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`);
      }
      return await resp.json();
    },
    waitForRun: _waitForRun,
    setMode,
    setGenerating: _setGenerating,
    save: _save,
    consumeManualStop: _consumeManualStop,
    isTransientStreamInterrupt: _isTransientStreamInterrupt,
    errorText: _errorText,
  });

  btnModeChat?.addEventListener('click', () => setMode('chat'));
  btnModeContinue?.addEventListener('click', () => {
    setMode('continue');
    void _continueControllerApi.runContinue();
  });
  btnModeExplain?.addEventListener('click', () => setMode('explain'));
  btnModeBugHunt?.addEventListener('click', () => setMode('bughunt'));
  btnModeRefactor?.addEventListener('click', () => setMode('refactor'));
  btnModeTests?.addEventListener('click', () => setMode('tests'));

  btnChatRetry?.addEventListener('click', () => {
    const path = currentThreadPath();
    if (!path || _activeReader) return;
    const last = _lastUserMessage(path);
    if (!last) {
      _localAssistantMessage(path, 'No previous user prompt to retry in this conversation.');
      return;
    }
    void _send(last, { appendUserMessage: false });
  });

  btnChatClear?.addEventListener('click', () => {
    const path = currentThreadPath();
    if (!path) return;
    void _clearThreadRemote(path);
  });

  btnChatStop?.addEventListener('click', () => {
    _manualStopRequested = true;
    if (_activeReader) {
      _safeCancelReader(_activeReader);
      _activeReader = null;
    }
  });

  btnWorkspaceContext?.addEventListener('click', () => {
    const nextCommand = _workspaceContextEnabled ? '/workspace off' : '/workspace on';
    void _runSlashCommand(currentThreadPath(), nextCommand).catch((err) => {
      _localAssistantMessage(currentThreadPath(), `Workspace command failed: ${_errorText(err)}`);
    });
  });

  aiBtn.addEventListener('click', () => {
    const nowOpen = panel.hidden;
    _setPanelOpen(nowOpen);
    if (nowOpen) {
      _refreshSelectionFromEditor();
      const path = currentThreadPath();
      renderThread(path);
      if (path) {
        void _fetchThread(path)
          .then((payload) => {
            _hydrateThread(path, payload);
            renderThread(path);
          })
          .catch(() => {});
      }
      void _resumePendingForPath(path);
      input.focus();
    } else {
      _stopResizeDrag();
      selectionChip && (selectionChip.hidden = true);
    }
  });

  splitter.addEventListener('mousedown', (e) => {
    if (panel.hidden) return;
    e.preventDefault();
    _dragStartX = e.clientX;
    _dragStartW = panel.getBoundingClientRect().width;
    splitter.classList.add('is-dragging');
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
  });

  document.addEventListener('mousemove', (e) => {
    if (_dragStartX === null) return;
    const delta = e.clientX - _dragStartX;
    _setPanelWidth(_dragStartW - delta);
  });

  document.addEventListener('mouseup', _stopResizeDrag);

  document.addEventListener('keydown', (e) => {
    if (e.altKey && e.key === 'a') {
      e.preventDefault();
      aiBtn.click();
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (document.hidden || panel.hidden) return;
    _syncThinkingNote();
    void _resumePendingForPath(currentThreadPath());
    void _continueControllerApi.resumeContinueIfNeeded();
  });

  window.addEventListener('focus', () => {
    if (panel.hidden) return;
    _syncThinkingNote();
    void _resumePendingForPath(currentThreadPath());
    void _continueControllerApi.resumeContinueIfNeeded();
  });

  input.addEventListener('keydown', (e) => {
    if (_shouldUseHistoryKey(e)) {
      const moved = _navigatePromptHistory(e.key === 'ArrowUp' ? -1 : 1);
      if (moved) {
        e.preventDefault();
        return;
      }
    }
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      void _send();
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void _send();
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!_isGenerating) return;
    e.preventDefault();
    _manualStopRequested = true;
    if (_activeReader) {
      _safeCancelReader(_activeReader);
      _activeReader = null;
    }
  });

  sendBtn.addEventListener('click', () => void _send());
  input.addEventListener('input', () => {
    if (_historyIndex === -1) {
      _historyDraft = input.value;
    }
    _autosize(input);
  });

  async function _resumePendingForPath(path) {
    if (!path || _activeReader) return;
    const pendingRunId = _pendingRuns.get(_CONVERSATION_STORE_KEY);
    if (!pendingRunId) return;

    const thinkingEl = _appendThinking('Waiting for agent...');
    _setGenerating(true);

    try {
      const run = await _waitForRun(pendingRunId);
      if (_conversationExternalId) {
        const payload = await _fetchThread(path);
        _hydrateThread(path, payload);
        renderThread(path);
      }
      if (String(run?.status || '') === 'failed') {
        const errors = Array.isArray(run?.errors) ? run.errors : [];
        const lastError = errors.length ? errors[errors.length - 1] : null;
        _localAssistantMessage(path, `Error: ${_errorText(lastError?.message || run?.output?.text || 'Agent run failed')}`);
      }
    } catch (err) {
      thinkingEl?.remove();
      if (_consumeManualStop()) {
        _localAssistantMessage(path, 'Stopped waiting locally. The agent may still finish in the background.');
        return;
      }
      if (_isTransientStreamInterrupt(err)) {
        return;
      }
      _pushMessage(path, { role: 'assistant', text: `Error: ${_errorText(err)}` });
      renderThread(path);
    } finally {
      _clearPending(path);
      thinkingEl?.remove();
      _setGenerating(false);
      _activeReader = null;
      _syncThinkingNote();
      _scrollBottom();
    }
  }

  async function _send(overrideText = null, opts = {}) {
    const appendUserMessage = opts.appendUserMessage !== false;
    _refreshSelectionFromEditor();
    const text            = (overrideText ?? input.value).trim();
    const activePath      = currentPath();
    const path            = activePath || _WORKSPACE_THREAD_KEY;
    const selectionForRun = _currentSelection;
    const cursorForRun    = { ..._currentCursor };
    if (!text || _activeReader) return;
    if (overrideText == null && text.startsWith('/')) {
      try {
        const handled = await _runSlashCommand(path, text);
        if (handled) {
          input.value = '';
          _autosize(input);
          return;
        }
      } catch (err) {
        _localAssistantMessage(path, `Slash command failed: ${_errorText(err)}`);
        input.value = '';
        _autosize(input);
        return;
      }
    }
    if (_pendingRuns.has(_CONVERSATION_STORE_KEY)) {
      void _resumePendingForPath(path);
      return;
    }

    if (overrideText == null) {
      input.value = '';
      _autosize(input);
    }
    _resetPromptHistoryCursor();
    _currentSelection = null;
    _updateSelectionChip();

    if (appendUserMessage) {
      _pushMessage(path, { role: 'user', text });
    }
    renderThread(path);

    const thinkingEl = _appendThinking();
    _manualStopRequested = false;
    _setGenerating(true);

    try {
      const startResp = await fetch('/api/chat/runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          thread_path:              path,
          active_path:              activePath || '.',
          user_text:                text,
          selection:                selectionForRun,
          cursor:                   cursorForRun,
          mode:                     _mode,
          conversation_external_id: _conversationExternalId,
          workspace_context_enabled: _workspaceContextEnabled,
        }),
      });
      if (!startResp.ok) {
        throw new Error(`${startResp.status} ${startResp.statusText}`);
      }

      const payload = await startResp.json();
      const run = payload?.run || payload;
      _setPending(path, run?.run_id);

      const completedRun = await _waitForRun(run?.run_id);
      _clearPending(path);
      if (_conversationExternalId) {
        const threadPayload = await _fetchThread(path);
        _hydrateThread(path, threadPayload);
        renderThread(path);
      }
      if (String(completedRun?.status || '') === 'failed') {
        const errors = Array.isArray(completedRun?.errors) ? completedRun.errors : [];
        const lastError = errors.length ? errors[errors.length - 1] : null;
        _localAssistantMessage(path, `Error: ${_errorText(lastError?.message || completedRun?.output?.text || 'Agent run failed')}`);
      }
    } catch (err) {
      thinkingEl?.remove();
      if (_consumeManualStop()) {
        _localAssistantMessage(path, 'Stopped waiting locally. The agent may still finish in the background.');
        return;
      }
      if (_isTransientStreamInterrupt(err)) {
        return;
      }
      _pushMessage(path, { role: 'assistant', text: `Error: ${_errorText(err)}` });
      renderThread(path);
    } finally {
      _clearPending(path);
      _setGenerating(false);
      _activeReader = null;
      _syncThinkingNote();
      _scrollBottom();
    }
  }

  function _pushMessage(path, msg) {
    if (!_threads.has(_CONVERSATION_STORE_KEY)) _threads.set(_CONVERSATION_STORE_KEY, []);
    _threads.get(_CONVERSATION_STORE_KEY).push(msg);
    _save();
  }

  function _autosize(el) {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 100)}px`;
  }

  _renderWorkspaceContextToggle();

  (function _restore() {
    _setPanelWidth(_readSavedWidth());
    splitter.hidden = true;

    const saved = _loadState();
    if (!saved) return;

    if (saved.threads) {
      const savedConversation = saved.threads[_CONVERSATION_STORE_KEY];
      if (Array.isArray(savedConversation) && savedConversation.length) {
        _threads.set(_CONVERSATION_STORE_KEY, savedConversation);
      } else {
        for (const msgs of Object.values(saved.threads)) {
          if (Array.isArray(msgs) && msgs.length) {
            _threads.set(_CONVERSATION_STORE_KEY, msgs);
            break;
          }
        }
      }
    }

    if (saved.pendingRuns && typeof saved.pendingRuns === 'object') {
      for (const runId of Object.values(saved.pendingRuns)) {
        if (typeof runId === 'string' && runId) {
          _pendingRuns.set(_CONVERSATION_STORE_KEY, runId);
          break;
        }
      }
    }

    if (saved.continueState && typeof saved.continueState === 'object') {
      _continueControllerApi.restoreState(saved.continueState);
    }
    if (typeof saved.conversationExternalId === 'string' && saved.conversationExternalId) {
      _conversationExternalId = saved.conversationExternalId;
    }

    _syncThinkingNote();

    if (saved.open) {
      _setPanelOpen(true);
    }

    if (['continue', 'chat', 'explain', 'bughunt', 'refactor', 'tests'].includes(saved.mode)) {
      setMode(saved.mode);
      if (saved.mode === 'continue' && _continueControllerApi.isInProgress()) {
        void _continueControllerApi.resumeContinueIfNeeded();
      }
    }
  })();

  return {
    async handleWorkspaceRootChanged() {
      _resetConversationState();
      if (!_workspaceContextEnabled) return;
      try {
        await _rebuildWorkspaceMenu({ quiet: true });
      } catch (_) {}
    },
    onTabChange(path) {
      if (panel.hidden) return;
      _refreshSelectionFromEditor();
      _syncThinkingNote();
      if (_mode !== 'continue') {
        const threadPath = path || _WORKSPACE_THREAD_KEY;
        renderThread(threadPath);
        if (threadPath) {
          void _fetchThread(threadPath)
            .then((payload) => {
              _hydrateThread(threadPath, payload);
              renderThread(threadPath);
            })
            .catch(() => {});
        }
        void _resumePendingForPath(threadPath);
      } else {
        _continueControllerApi.onTabChange(path);
      }
    },
    onSelectionChange(payload) {
      if (payload && typeof payload === 'object') {
        _currentSelection = payload.text ?? null;
        if (typeof payload.line === 'number' && typeof payload.column === 'number') {
          _currentCursor = {
            line: payload.line,
            column: payload.column,
            offset: typeof payload.offset === 'number' ? payload.offset : _currentCursor.offset,
          };
        }
      } else {
        _currentSelection = payload;
      }
      _updateSelectionChip();
    },
  };
}
