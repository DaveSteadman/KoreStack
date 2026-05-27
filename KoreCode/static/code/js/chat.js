// chat.js
//
// Chat panel for KoreCode.
import { buildPromptByMode, buildAgentToolFollowupPrompt } from './chat/prompting.js';
import { extractAgentEnvelope, executeAgentToolRequests } from './chat/agent-loop.js';
import { createThreadUI } from './chat/thread-ui.js';
import { createContinueModeController } from './chat/continue-mode.js';

const _STATE_KEY = 'korecode.chat-state';
const _WIDTH_KEY = 'korecode-chat-w';
const _WORKSPACE_CONTEXT_KEY = 'korecode.workspace-context';
const _MIN_PANEL_W = 260;
const _MAX_PANEL_W = 720;
const _DEFAULT_PANEL_W = 340;
const _MAX_AGENT_TOOL_TURNS = 3;
const _WORKSPACE_THREAD_KEY = '__workspace__';

// In-memory chat thread store: path -> Array<{ role: 'user'|'assistant', text: string }>
const _threads = new Map();

function _saveState(open, mode, pendingRuns = {}, continueState = null) {
  try {
    const threads = {};
    for (const [path, msgs] of _threads) threads[path] = msgs;
    localStorage.setItem(_STATE_KEY, JSON.stringify({ open, mode, threads, pendingRuns, continueState }));
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

  const _threadUI = createThreadUI({
    thread,
    insertFromChat,
    applyStructuredEdits: (edits) => window.__kcApplyStructuredEdits?.(edits),
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
    const cursorText = `cursor L${_currentCursor.line}, C${_currentCursor.column}`;
    if (lines > 0) {
      selectionLabel.textContent = `${lines} line${lines === 1 ? '' : 's'} selected - ${cursorText} - will be sent as context`;
    } else {
      selectionLabel.textContent = `0 lines selected - ${cursorText} - insert uses cursor position`;
    }
    selectionChip.hidden = false;
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
    _saveState(_panelOpen, _mode, _pendingToObject(), _continueControllerApi.stateSnapshot());
  }

  function _setPending(path, runId) {
    if (!path || !runId) return;
    _pendingRuns.set(path, runId);
    _save();
    _syncThinkingNote();
  }

  function _clearPending(path) {
    if (!path || !_pendingRuns.has(path)) return;
    _pendingRuns.delete(path);
    _save();
    _syncThinkingNote();
  }

  function _syncThinkingNote() {
    if (!progressNote) return;
    if (panel.hidden) {
      progressNote.hidden = true;
      return;
    }
    const path = currentPath();
    const hasAnyPending = _pendingRuns.size > 0;
    const hasCurrentPending = Boolean(path && _pendingRuns.has(path));
    progressNote.hidden = !hasAnyPending;
    if (progressNote.hidden) return;
    progressNote.textContent = hasCurrentPending ? 'Generating...' : 'Generating... (in another tab)';
  }

  function _lastUserMessage(path) {
    if (!path) return null;
    const messages = _threads.get(path) ?? [];
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i]?.role === 'user' && typeof messages[i].text === 'string' && messages[i].text.trim()) {
        return messages[i].text;
      }
    }
    return null;
  }

  function _clearThread(path) {
    if (!path) return;
    _threads.delete(path);
    _save();
    renderThread(path);
  }

  function _localAssistantMessage(path, text) {
    if (!path || !text) return;
    _pushMessage(path, { role: 'assistant', text });
    renderThread(path);
  }

  function _showChatNotice(text) {
    if (!text) return;
    const el = document.createElement('div');
    el.className = 'chat-thinking';
    el.textContent = text;
    thread.appendChild(el);
    _scrollBottom();
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

  function _cachedSuiteUrls() {
    try {
      const raw = localStorage.getItem('koresuite.urls');
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function _defaultAgentBase() {
    const host = window.location?.hostname || '127.0.0.1';
    return `http://${host}:8605`;
  }

  function agentBase() {
    const cached = _cachedSuiteUrls();
    return (
      window.__koreSuiteUrls?.koreagent
      || cached?.koreagent
      || _defaultAgentBase()
    ).replace(/\/$/, '');
  }

  function sessionForPath(path) {
    const sanitized = path.replace(/[^A-Za-z0-9_-]/g, '_').slice(-60);
    return `kc_${sanitized}`;
  }

  function currentPath() {
    return getActiveTab()?.path ?? null;
  }

  function currentThreadPath() {
    return currentPath() || _WORKSPACE_THREAD_KEY;
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
    _threadUI.renderThread(path, path ? (_threads.get(path) ?? []) : []);
  }

  _continueControllerApi = createContinueModeController({
    thread,
    btnModeContinue,
    getContinueContext,
    insertContinuation,
    agentBase,
    getCurrentPath: currentPath,
    setMode,
    setGenerating: _setGenerating,
    save: _save,
    consumeManualStop: _consumeManualStop,
    isTransientStreamInterrupt: _isTransientStreamInterrupt,
    errorText: _errorText,
    safeCancelReader: _safeCancelReader,
    setActiveReader: (reader) => {
      _activeReader = reader;
    },
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
    const path = currentPath();
    if (!path || _activeReader) return;
    const last = _lastUserMessage(path);
    if (!last) {
      _localAssistantMessage(path, 'No previous user prompt to retry for this file thread.');
      return;
    }
    void _send(last, { appendUserMessage: false });
  });

  btnChatClear?.addEventListener('click', () => {
    const path = currentPath();
    if (!path) return;
    _clearThread(path);
  });

  btnChatStop?.addEventListener('click', () => {
    if (!_activeReader) return;
    _manualStopRequested = true;
    _safeCancelReader(_activeReader);
    _activeReader = null;
  });

  btnWorkspaceContext?.addEventListener('click', () => {
    _workspaceContextEnabled = !_workspaceContextEnabled;
    _saveWorkspaceContextEnabled(_workspaceContextEnabled);
    _renderWorkspaceContextToggle();
  });

  aiBtn.addEventListener('click', () => {
    const nowOpen = panel.hidden;
    _setPanelOpen(nowOpen);
    if (nowOpen) {
      _refreshSelectionFromEditor();
      const path = currentPath();
      renderThread(path);
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
    void _resumePendingForPath(currentPath());
    void _continueControllerApi.resumeContinueIfNeeded();
  });

  window.addEventListener('focus', () => {
    if (panel.hidden) return;
    _syncThinkingNote();
    void _resumePendingForPath(currentPath());
    void _continueControllerApi.resumeContinueIfNeeded();
  });

  input.addEventListener('keydown', (e) => {
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
    if (!_isGenerating || !_activeReader) return;
    e.preventDefault();
    _manualStopRequested = true;
    _safeCancelReader(_activeReader);
    _activeReader = null;
  });

  sendBtn.addEventListener('click', () => void _send());

  async function _resumePendingForPath(path) {
    if (!path || _activeReader) return;
    const runId = _pendingRuns.get(path);
    if (!runId) return;

    const base = agentBase();
    const thinkingEl = _appendThinking('Resuming response...');
    _setGenerating(true);

    try {
      const reply = await _streamResponse(base, runId, thinkingEl);
      _clearPending(path);
      if (reply.trim()) {
        _pushMessage(path, { role: 'assistant', text: reply });
      }
      renderThread(path);
    } catch (err) {
      thinkingEl?.remove();
      if (_consumeManualStop()) {
        _clearPending(path);
        _localAssistantMessage(path, 'Generation stopped.');
        return;
      }
      const recovered = await _recoverAssistantFromHistory(path);
      if (recovered) {
        _clearPending(path);
        renderThread(path);
        return;
      }
      if (_isTransientStreamInterrupt(err)) {
        return;
      }
      _clearPending(path);
      _pushMessage(path, { role: 'assistant', text: `Error: ${_errorText(err)}` });
      renderThread(path);
    } finally {
      _setGenerating(false);
      _activeReader = null;
      _syncThinkingNote();
      _scrollBottom();
    }
  }

  async function _recoverAssistantFromHistory(path) {
    if (!path) return false;
    const base = agentBase();
    const sessionId = sessionForPath(path);
    try {
      const resp = await fetch(`${base}/sessions/${encodeURIComponent(sessionId)}/history`);
      if (!resp.ok) return false;
      const payload = await resp.json();
      const turns = Array.isArray(payload?.turns) ? payload.turns : [];
      let assistantText = null;
      for (let i = turns.length - 1; i >= 0; i -= 1) {
        const turn = turns[i];
        if (turn?.role === 'assistant' && typeof turn?.content === 'string' && turn.content.trim()) {
          assistantText = turn.content;
          break;
        }
      }
      if (!assistantText) return false;

      const msgs = _threads.get(path) ?? [];
      const lastAssistant = [...msgs].reverse().find((m) => m.role === 'assistant');
      if (lastAssistant?.text === assistantText) {
        return true;
      }
      _pushMessage(path, { role: 'assistant', text: assistantText });
      return true;
    } catch {
      return false;
    }
  }

  function _handleSlashCommand(path, text) {
    if (!text.startsWith('/')) return false;
    const normalized = text.slice(1).trim().replaceAll('\n', ' ').replaceAll('\t', ' ');
    const [cmdRaw, ...rest] = normalized.split(' ').filter(Boolean);
    const cmd = (cmdRaw || '').toLowerCase();
    const arg = rest.join(' ').toLowerCase();
    if (!cmd) return false;

    const modeNames = ['chat', 'continue', 'explain', 'bughunt', 'refactor', 'tests'];
    if (cmd === 'help') {
      _localAssistantMessage(path, 'Commands: /help, /clear, /retry, /workspace <on|off>, /mode <chat|continue|explain|bughunt|refactor|tests>, /chat, /continue, /explain, /bughunt, /refactor, /tests');
      return true;
    }
    if (cmd === 'clear') {
      _clearThread(path);
      return true;
    }
    if (cmd === 'retry') {
      const last = _lastUserMessage(path);
      if (last) void _send(last, { appendUserMessage: false });
      else _localAssistantMessage(path, 'No previous user prompt to retry for this file thread.');
      return true;
    }
    if (cmd === 'workspace') {
      if (arg === 'on' || arg === 'off') {
        _workspaceContextEnabled = arg === 'on';
        _saveWorkspaceContextEnabled(_workspaceContextEnabled);
        _renderWorkspaceContextToggle();
        _localAssistantMessage(path, `Workspace context ${_workspaceContextEnabled ? 'enabled' : 'disabled'}.`);
      } else {
        _localAssistantMessage(path, 'Use /workspace on or /workspace off');
      }
      return true;
    }
    if (cmd === 'mode') {
      if (modeNames.includes(arg)) {
        if (arg === 'continue') {
          setMode('continue');
          void _continueControllerApi.runContinue();
        } else {
          setMode(arg);
        }
      } else {
        _localAssistantMessage(path, 'Unknown mode. Use /mode chat|continue|explain|bughunt|refactor|tests');
      }
      return true;
    }
    if (modeNames.includes(cmd)) {
      if (cmd === 'continue') {
        setMode('continue');
        void _continueControllerApi.runContinue();
      } else {
        setMode(cmd);
      }
      return true;
    }
    _localAssistantMessage(path, `Unknown command: /${cmd}. Use /help.`);
    return true;
  }

  async function _send(overrideText = null, opts = {}) {
    const appendUserMessage = opts.appendUserMessage !== false;
    _refreshSelectionFromEditor();
    const text = (overrideText ?? input.value).trim();
    const activePath = currentPath();
    const path = activePath || _WORKSPACE_THREAD_KEY;
    if (!text || _activeReader) return;
    if (overrideText == null && _handleSlashCommand(path, text)) {
      input.value = '';
      _autosize(input);
      return;
    }
    if (_pendingRuns.has(path)) {
      void _resumePendingForPath(path);
      return;
    }

    const prompt = await buildPromptByMode({
      mode: _mode,
      userText: text,
      path: activePath || '.',
      selection: _currentSelection,
      cursor: _currentCursor,
      workspaceContextEnabled: _workspaceContextEnabled,
    });

    if (overrideText == null) {
      input.value = '';
      _autosize(input);
    }
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
      const sessionId = sessionForPath(path);
      const base = agentBase();

      const _startPromptRun = async (runPrompt) => {
        const resp = await fetch(`${base}/sessions/${encodeURIComponent(sessionId)}/prompt`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt: runPrompt }),
        });
        if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
        const payload = await resp.json();
        return payload.run_id;
      };

      let runId = await _startPromptRun(prompt);
      _setPending(path, runId);
      let finalReply = await _streamResponse(base, runId, thinkingEl);
      _clearPending(path);

      for (let turn = 0; turn < _MAX_AGENT_TOOL_TURNS; turn += 1) {
        const envelope = extractAgentEnvelope(finalReply);
        const requestedTools = Array.isArray(envelope?.tool_requests) ? envelope.tool_requests : [];
        const shouldContinue = envelope?.kind === 'tool_requests' && envelope?.next === 'continue' && requestedTools.length > 0;
        if (!shouldContinue) break;

        const toolResults = await executeAgentToolRequests({
          toolRequests: requestedTools,
          activePath,
          workspaceContextEnabled: _workspaceContextEnabled,
          errorText: _errorText,
        });
        const followupPrompt = buildAgentToolFollowupPrompt({
          mode: _mode,
          path: activePath || '.',
          userText: text,
          previousResponse: finalReply,
          toolResults,
        });

        const followThinking = _appendThinking(`Agent tool step ${turn + 1}...`);
        runId = await _startPromptRun(followupPrompt);
        _setPending(path, runId);
        finalReply = await _streamResponse(base, runId, followThinking);
        _clearPending(path);
      }

      _pushMessage(path, { role: 'assistant', text: finalReply });
      renderThread(path);
    } catch (err) {
      thinkingEl?.remove();
      if (_consumeManualStop()) {
        _clearPending(path);
        _localAssistantMessage(path, 'Generation stopped.');
        return;
      }
      const recovered = await _recoverAssistantFromHistory(path);
      if (recovered) {
        _clearPending(path);
        renderThread(path);
        return;
      }
      if (_isTransientStreamInterrupt(err)) {
        return;
      }
      _clearPending(path);
      _pushMessage(path, { role: 'assistant', text: `Error: ${_errorText(err)}` });
      renderThread(path);
    } finally {
      _setGenerating(false);
      _activeReader = null;
      _syncThinkingNote();
      _scrollBottom();
    }
  }

  async function _streamResponse(base, runId, thinkingEl) {
    const response = await fetch(`${base}/runs/${encodeURIComponent(runId)}/stream`);
    if (!response.ok) throw new Error(`Stream ${response.status}`);

    const reader = response.body.getReader();
    _activeReader = reader;
    const decoder = new TextDecoder();
    let buffer = '';
    let reply = '';

    thinkingEl?.remove();
    const liveEl = _appendLivePre();

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
          try {
            event = JSON.parse(raw);
          } catch {
            continue;
          }

          if (event.type === 'response') {
            reply += event.response ?? '';
            liveEl.textContent = reply;
            _scrollBottom();
          } else if (event.type === 'done') {
            break outer;
          }
        }
      }
    } catch (err) {
      if (!(reply.trim() && _isTransientStreamInterrupt(err))) {
        throw err;
      }
    } finally {
      _safeCancelReader(reader);
    }

    liveEl.closest('.chat-msg--assistant')?.remove();
    return reply;
  }

  function _pushMessage(path, msg) {
    if (!_threads.has(path)) _threads.set(path, []);
    _threads.get(path).push(msg);
    _save();
  }

  function _autosize(el) {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 100)}px`;
  }

  input.addEventListener('input', () => _autosize(input));
  _renderWorkspaceContextToggle();

  (function _restore() {
    _setPanelWidth(_readSavedWidth());
    splitter.hidden = true;

    const saved = _loadState();
    if (!saved) return;

    if (saved.threads) {
      for (const [path, msgs] of Object.entries(saved.threads)) {
        if (Array.isArray(msgs) && msgs.length) _threads.set(path, msgs);
      }
    }

    if (saved.pendingRuns && typeof saved.pendingRuns === 'object') {
      for (const [path, runId] of Object.entries(saved.pendingRuns)) {
        if (typeof path === 'string' && typeof runId === 'string' && runId) {
          _pendingRuns.set(path, runId);
        }
      }
    }

    if (saved.continueState && typeof saved.continueState === 'object') {
      _continueControllerApi.restoreState(saved.continueState);
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
    onTabChange(path) {
      if (panel.hidden) return;
      _refreshSelectionFromEditor();
      _syncThinkingNote();
      if (_mode !== 'continue') {
        renderThread(path);
        void _resumePendingForPath(path);
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
