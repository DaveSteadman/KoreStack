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
// In-memory chat state keyed by thread path.
const _threads = new Map();

function _workspaceStateKey(workspaceRoot) {
  return workspaceRoot ? `${_STATE_KEY}:${encodeURIComponent(workspaceRoot)}` : _STATE_KEY;
}

function _saveState(storageKey, open, mode, threads = {}, pendingRuns = {}, continueState = null, conversationExternalIds = {}, conversationTitles = {}, focus = false) {
  try {
    localStorage.setItem(storageKey, JSON.stringify({ open, mode, threads, pendingRuns, continueState, conversationExternalIds, conversationTitles, focus }));
  } catch (_) {}
}

function _loadState(storageKey) {
  try {
    const raw = localStorage.getItem(storageKey);
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
  getActiveWorkItemId = null,
  getWorkspaceRoot = () => '',
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
  const btnChatFocus = document.getElementById('btn-chat-focus');
  const codeMain = document.getElementById('code-main');
  const linkedConversation = document.getElementById('chat-linked-conversation');
  const linkedConversationName = document.getElementById('chat-linked-conversation-name');
  const executionContract = document.getElementById('chat-execution-contract');
  const executionContractName = document.getElementById('chat-execution-contract-name');
  const selectionChip = document.getElementById('chat-selection-chip');
  const selectionLabel = document.getElementById('chat-selection-label');
  const progressNote = document.getElementById('chat-progress-note');
  const slashSuggestionsEl = document.getElementById('chat-slash-suggestions');

  let _currentSelection = null;
  let _currentCursor = { line: 1, column: 1, offset: 0 };

  let _mode = 'chat';
  let _panelOpen = false;
  let _isFocused = false;
  let _executionContract = null;
  let _dragStartX = null;
  let _dragStartW = null;
  const _pendingRuns = new Map(); // path -> runId
  let _manualStopRequested = false;
  let _isGenerating = false;
  let _workspaceContextEnabled = _loadWorkspaceContextEnabled();
  let _continueControllerApi = { stateSnapshot: () => null };
  const _conversationExternalIds = new Map();
  const _conversationTitles = new Map();
  let _historyIndex = -1;
  let _historyDraft = '';
  let _slashSuggestions = [];
  let _slashSuggestionIndex = -1;
  let _slashSuggestSeq = 0;
  let _legacyState = null;
  const _reportedAppliedRuns = new Set();

  const _threadUI = createThreadUI({
    thread,
    insertFromChat,
    createEditProposal: async (edits) => {
      const resp = await fetch('/api/edit-proposals', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({
          edits,
          run_id: _pendingRuns.get(_threadKey(currentThreadPath())) || null,
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

  function _threadKey(path) {
    return path || _WORKSPACE_THREAD_KEY;
  }

  function _threadMessages(path) {
    return _threads.get(_threadKey(path)) ?? [];
  }

  function _hasThreadState(path) {
    return _threads.has(_threadKey(path));
  }

  function _conversationExternalIdForPath(path) {
    return _conversationExternalIds.get(_threadKey(path)) ?? null;
  }

  function _conversationTitleForPath(path) {
    return _conversationTitles.get(_threadKey(path)) ?? null;
  }

  function _conversationExternalIdsToObject() {
    const out = {};
    for (const [path, externalId] of _conversationExternalIds) out[path] = externalId;
    return out;
  }

  function _conversationTitlesToObject() {
    const out = {};
    for (const [path, title] of _conversationTitles) out[path] = title;
    return out;
  }

  function _shouldFetchRemoteThread(path) {
    return Boolean(_conversationExternalIdForPath(path) || !_hasThreadState(path));
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
    const path = currentThreadPath();
    const next = typeof value === 'string' && value.trim() ? value.trim() : null;
    const key = _threadKey(path);
    if (next) {
      _conversationTitles.set(key, next);
    } else {
      _conversationTitles.delete(key);
    }
    if (!linkedConversation || !linkedConversationName) return;
    linkedConversation.hidden = !next;
    if (next) {
      linkedConversationName.textContent = next;
      linkedConversationName.title = next;
    } else {
      linkedConversationName.textContent = '';
      linkedConversationName.removeAttribute('title');
    }
    _save();
  }

  function _setExecutionContract(run) {
    const contract = run?.context?.execution_contract;
    _executionContract = contract && typeof contract === 'object' ? contract : null;
    if (!executionContract || !executionContractName) return;
    executionContract.hidden = !_executionContract;
    if (_executionContract) {
      const label = String(_executionContract.label || _executionContract.id || 'Constrained task');
      const tools = Array.isArray(_executionContract.allowed_tools) ? _executionContract.allowed_tools.length : 0;
      executionContractName.textContent = `${label} · ${tools} tools`;
      executionContractName.title = Array.isArray(_executionContract.allowed_tools)
        ? _executionContract.allowed_tools.join(', ')
        : '';
    } else {
      executionContractName.textContent = '';
      executionContractName.removeAttribute('title');
    }
  }

  function _userPromptHistory() {
    const messages = _threadMessages(currentThreadPath());
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

  function _clearSlashSuggestions() {
    _slashSuggestions = [];
    _slashSuggestionIndex = -1;
    if (!slashSuggestionsEl) return;
    slashSuggestionsEl.hidden = true;
    slashSuggestionsEl.replaceChildren();
  }

  function _renderSlashSuggestions() {
    if (!slashSuggestionsEl) return;
    slashSuggestionsEl.replaceChildren();
    if (!_slashSuggestions.length) {
      slashSuggestionsEl.hidden = true;
      return;
    }
    _slashSuggestions.forEach((item, index) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `chat-slash-suggestion${index === _slashSuggestionIndex ? ' is-active' : ''}`;
      btn.innerHTML = `
        <span class="chat-slash-suggestion-label"></span>
        <span class="chat-slash-suggestion-description"></span>
      `;
      btn.querySelector('.chat-slash-suggestion-label').textContent = String(item?.label || item?.value || '');
      btn.querySelector('.chat-slash-suggestion-description').textContent = String(item?.description || '');
      btn.addEventListener('click', () => _applySlashSuggestion(index));
      slashSuggestionsEl.appendChild(btn);
    });
    slashSuggestionsEl.hidden = false;
  }

  function _setSlashSuggestions(items) {
    _slashSuggestions = Array.isArray(items) ? items : [];
    _slashSuggestionIndex = _slashSuggestions.length ? 0 : -1;
    _renderSlashSuggestions();
  }

  function _isSlashInput(text = input.value) {
    return String(text || '').trimStart().startsWith('/');
  }

  async function _refreshSlashSuggestions() {
    const currentValue = input.value;
    if (!_isSlashInput(currentValue)) {
      _clearSlashSuggestions();
      return;
    }
    const seq = ++_slashSuggestSeq;
    try {
      const items = await _fetchSlashCompletions(currentValue);
      if (seq !== _slashSuggestSeq) return;
      _setSlashSuggestions(items);
    } catch (_) {
      if (seq !== _slashSuggestSeq) return;
      _clearSlashSuggestions();
    }
  }

  function _applySlashSuggestion(index = _slashSuggestionIndex) {
    const item = _slashSuggestions[index];
    if (!item) return false;
    _setInputValue(String(item.value || ''));
    _clearSlashSuggestions();
    return true;
  }

  function _moveSlashSuggestion(direction) {
    if (!_slashSuggestions.length) return false;
    if (_slashSuggestionIndex < 0) {
      _slashSuggestionIndex = 0;
    } else {
      const total = _slashSuggestions.length;
      _slashSuggestionIndex = (_slashSuggestionIndex + direction + total) % total;
    }
    _renderSlashSuggestions();
    return true;
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
    if (!_panelOpen) {
      _setConversationFocus(false);
      _clearSlashSuggestions();
    }
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
      _workspaceStateKey(getWorkspaceRoot()),
      _panelOpen,
      _mode,
      threads,
      _pendingToObject(),
      _continueControllerApi.stateSnapshot(),
      _conversationExternalIdsToObject(),
      _conversationTitlesToObject(),
      _isFocused,
    );
  }

  function _setConversationFocus(focused) {
    _isFocused = Boolean(focused) && _panelOpen;
    codeMain?.classList.toggle('is-chat-focus', _isFocused);
    btnChatFocus?.classList.toggle('is-active', _isFocused);
    if (btnChatFocus) btnChatFocus.textContent = _isFocused ? 'Return to code' : 'Focus';
    _save();
  }

  function _setPending(path, runId) {
    if (!path || !runId) return;
    _pendingRuns.set(_threadKey(path), runId);
    _save();
    _syncThinkingNote();
  }

  function _clearPending(path) {
    if (!path || !_pendingRuns.has(_threadKey(path))) return;
    _pendingRuns.delete(_threadKey(path));
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
    const hasCurrentPending = _pendingRuns.has(_threadKey(currentThreadPath()));
    progressNote.hidden = !hasAnyPending;
    if (progressNote.hidden) return;
    progressNote.textContent = hasCurrentPending ? 'Generating...' : 'Generating... (in another tab)';
  }

  function _lastUserMessage(_path) {
    const messages = _threadMessages(_path);
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i]?.role === 'user' && typeof messages[i].text === 'string' && messages[i].text.trim()) {
        return messages[i].text;
      }
    }
    return null;
  }

  function _clearThread(path) {
    if (!path) return;
    const key = _threadKey(path);
    _threads.delete(key);
    _conversationExternalIds.delete(key);
    _conversationTitles.delete(key);
    if (_threadKey(currentThreadPath()) === key) {
      _setConversationTitle(null);
    }
    _clearPending(path);
    _save();
    renderThread(path);
  }

  async function _clearThreadRemote(path) {
    if (!path) return;
    try {
      const qs = new URLSearchParams({ path });
      const externalId = _conversationExternalIdForPath(path);
      if (externalId) {
        qs.set('conversation_external_id', externalId);
      }
      const resp = await fetch(`/api/chat/thread?${qs.toString()}`, { method: 'DELETE' });
      if (!resp.ok) {
        throw new Error(`${resp.status} ${resp.statusText}`);
      }
      _conversationExternalIds.delete(_threadKey(path));
    } catch (err) {
      _localAssistantMessage(path, `Clear thread failed: ${_errorText(err)}`);
      return;
    }
    _clearThread(path);
  }

  function _localAssistantMessage(path, text) {
    if (!path || !text) return;
    _pushMessage(path, { role: 'assistant', text });
    renderThread(path);
  }

  function _applyWorkspaceContext(enabled) {
    _workspaceContextEnabled = Boolean(enabled);
    _saveWorkspaceContextEnabled(_workspaceContextEnabled);
    _renderWorkspaceContextToggle();
  }

  async function _syncWorkspaceContextForConversation(enabled) {
    const externalId = _conversationExternalIdForPath(currentThreadPath());
    if (!externalId) return;
    const resp = await fetch('/api/chat/workspace-context', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_external_id: externalId,
        enabled: Boolean(enabled),
      }),
    });
    if (!resp.ok) {
      throw new Error(`${resp.status} ${resp.statusText}`);
    }
  }

  function _resetConversationState({ keepPanel = true } = {}) {
    _threads.clear();
    _pendingRuns.clear();
    _conversationExternalIds.clear();
    _conversationTitles.clear();
    _setConversationTitle(null);
    _setExecutionContract(null);
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

  async function _fetchSlashCompletions(text) {
    const resp = await fetch('/api/slash/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        current_mode: _mode,
        workspace_context_enabled: _workspaceContextEnabled,
        thread_path: currentThreadPath(),
        has_last_user_message: Boolean(_lastUserMessage(currentThreadPath())),
        limit: 8,
      }),
    });
    if (!resp.ok) {
      throw new Error(`${resp.status} ${resp.statusText}`);
    }
    const payload = await resp.json();
    return Array.isArray(payload?.items) ? payload.items : [];
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
    // Conversations belong to the workspace. The active file remains request context,
    // so one chat can reason about and edit multiple files in the same project.
    return _WORKSPACE_THREAD_KEY;
  }

  function _delay(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  function _isToolLoopAssistantText(text) {
    const envelope = extractAgentEnvelope(text);
    const requestedTools = Array.isArray(envelope?.tool_requests) ? envelope.tool_requests : [];
    return envelope?.next === 'continue' && requestedTools.length > 0;
  }

  function _filterVisibleMessages(messages) {
    return (Array.isArray(messages) ? messages : []).filter((msg) => {
      if (String(msg?.sender_display || '') === '__korecode_internal__') return false;
      if (msg?.role !== 'assistant') return true;
      return !_isToolLoopAssistantText(String(msg?.text || ''));
    });
  }

  function _hydrateThread(path, payload, { render = true } = {}) {
    const key = _threadKey(path);
    if (payload?.external_id) {
      _conversationExternalIds.set(key, String(payload.external_id));
    } else {
      _conversationExternalIds.delete(key);
    }
    const nextTitle = typeof payload?.title === 'string' ? payload.title : null;
    if (nextTitle && nextTitle.trim()) {
      _conversationTitles.set(key, nextTitle.trim());
    } else {
      _conversationTitles.delete(key);
    }
    if (_threadKey(currentThreadPath()) === key) {
      _setConversationTitle(nextTitle);
    }
    const messages = _filterVisibleMessages(payload?.messages);
    if (messages.length) {
      _threads.set(key, messages);
    } else {
      _threads.delete(key);
    }
    if (payload?.pending_response) {
      const runId = String(payload?.run?.run_id || payload?.conversation_id || 'pending');
      _setPending(path, runId);
    } else {
      _clearPending(path);
    }
    _save();
    if (render) {
      renderThread(path);
    }
    return payload;
  }

  async function _fetchThread(path) {
    const qs = new URLSearchParams({ path });
    const externalId = _conversationExternalIdForPath(path);
    if (externalId) {
      qs.set('conversation_external_id', externalId);
    }
    qs.set('workspace_context_enabled', _workspaceContextEnabled ? 'true' : 'false');
    const resp = await fetch(`/api/chat/thread?${qs.toString()}`);
    if (!resp.ok) {
      throw new Error(`${resp.status} ${resp.statusText}`);
    }
    const payload = await resp.json();
    return _hydrateThread(path, payload, { render: false });
  }

  function _setConversationExternalId(value, path = currentThreadPath()) {
    const next = typeof value === 'string' && value ? value : null;
    const key = _threadKey(path);
    if (_conversationExternalIds.get(key) === next) return;
    if (next) {
      _conversationExternalIds.set(key, next);
    } else {
      _conversationExternalIds.delete(key);
    }
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

  async function _waitForRun(runId, path = currentThreadPath()) {
    while (true) {
      if (_manualStopRequested) {
        throw new DOMException('Polling aborted', 'AbortError');
      }
      const run = await _fetchRun(runId);
      _setExecutionContract(run);
      if (typeof run?.conversation_external_id === 'string' && run.conversation_external_id) {
        _setConversationExternalId(run.conversation_external_id, path);
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
    _threadUI.renderThread(path, path ? _threadMessages(path) : []);
  }

  _continueControllerApi = createContinueModeController({
    thread,
    btnModeContinue,
    getContinueContext,
    insertContinuation,
    getCurrentPath: currentPath,
    getConversationExternalId: () => _conversationExternalIdForPath(currentThreadPath()),
    setConversationExternalId: (value) => _setConversationExternalId(value, currentThreadPath()),
    startContinueRun: async (payload) => {
      const resp = await fetch('/api/chat/continue-runs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...payload,
          conversation_external_id: payload?.conversation_external_id ?? _conversationExternalIdForPath(currentThreadPath()),
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

  btnChatFocus?.addEventListener('click', () => {
    if (panel.hidden) _setPanelOpen(true);
    _setConversationFocus(!_isFocused);
  });

  aiBtn.addEventListener('click', () => {
    const nowOpen = panel.hidden;
    _setPanelOpen(nowOpen);
    if (nowOpen) {
      _refreshSelectionFromEditor();
      const path = currentThreadPath();
      _setConversationTitle(_conversationTitleForPath(path));
      renderThread(path);
      if (path && _shouldFetchRemoteThread(path)) {
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
    if (e.key === 'Tab' && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {
      if (_applySlashSuggestion()) {
        e.preventDefault();
        return;
      }
    }
    if (_slashSuggestions.length && !e.altKey && !e.ctrlKey && !e.metaKey) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        _moveSlashSuggestion(1);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        _moveSlashSuggestion(-1);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        _clearSlashSuggestions();
        return;
      }
    }
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
    void _refreshSlashSuggestions();
  });

  async function _resumePendingForPath(path) {
    if (!path || _activeReader) return;
    const pendingRunId = _pendingRuns.get(_threadKey(path));
    if (!pendingRunId) return;

    const thinkingEl = _appendThinking('Waiting for agent...');
    _setGenerating(true);

    try {
      const run = await _waitForRun(pendingRunId, path);
      if (_conversationExternalIdForPath(path)) {
        const payload = await _fetchThread(path);
        _hydrateThread(path, payload);
        renderThread(path);
      }
      await _reportAppliedEdits(path, run);
      _showExecutionFromRun(run);
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
    const path            = currentThreadPath();
    const selectionForRun = _currentSelection;
    const cursorForRun    = { ..._currentCursor };
    _clearSlashSuggestions();
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
    if (!text || _activeReader) return;
    if (_pendingRuns.has(_threadKey(path))) {
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
          conversation_external_id: _conversationExternalIdForPath(path),
          workspace_context_enabled: _workspaceContextEnabled,
          work_item_id:             getActiveWorkItemId?.() || null,
        }),
      });
      if (!startResp.ok) {
        throw new Error(`${startResp.status} ${startResp.statusText}`);
      }

      const payload = await startResp.json();
      const run = payload?.run || payload;
      _setExecutionContract(run);
      _setPending(path, run?.run_id);

      const completedRun = await _waitForRun(run?.run_id, path);
      _clearPending(path);
      if (_conversationExternalIdForPath(path)) {
        const threadPayload = await _fetchThread(path);
        _hydrateThread(path, threadPayload);
        renderThread(path);
      }
      await _reportAppliedEdits(path, completedRun);
      _showExecutionFromRun(completedRun);
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
    const key = _threadKey(path);
    if (!_threads.has(key)) _threads.set(key, []);
    _threads.get(key).push(msg);
    _save();
  }

  async function _reportAppliedEdits(path, run) {
    const result = run?.output?.metadata?.edit_application?.apply_result;
    const count  = Number(result?.applied || 0);
    const runId  = String(run?.run_id || '');
    if (!result?.ok || count < 1 || (runId && _reportedAppliedRuns.has(runId))) return;
    if (runId) _reportedAppliedRuns.add(runId);
    const paths = Array.isArray(result.paths) ? result.paths : [];
    const reloadResult = await window.__kcReloadTabs?.(paths);
    const reloaded = Number(reloadResult?.reloaded || 0);
    const target = paths.length ? paths.join(', ') : 'the workspace';
    _localAssistantMessage(path, `Applied ${count} agent edit${count === 1 ? '' : 's'} to ${target}; refreshed ${reloaded} open file${reloaded === 1 ? '' : 's'}.`);
  }

  function _showExecutionFromRun(run) {
    const calls = Array.isArray(run?.tool_calls) ? run.tool_calls : [];
    const execution = calls.find((item) => item?.tool === 'run_python' && item?.ok && item?.result);
    if (execution?.result) window.__kcShowExecution?.(execution.result);
  }

  function _autosize(el) {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 100)}px`;
  }

  _renderWorkspaceContextToggle();

  function _restoreState(saved, { clear = false } = {}) {
    if (clear) {
      _threads.clear();
      _pendingRuns.clear();
      _conversationExternalIds.clear();
      _conversationTitles.clear();
    }
    if (!saved) return false;

    if (saved.threads && typeof saved.threads === 'object') {
      for (const [path, msgs] of Object.entries(saved.threads)) {
        if (Array.isArray(msgs) && msgs.length) {
          _threads.set(_threadKey(path), msgs);
        }
      }
    }

    if (saved.pendingRuns && typeof saved.pendingRuns === 'object') {
      for (const [path, runId] of Object.entries(saved.pendingRuns)) {
        if (typeof runId === 'string' && runId) {
          _pendingRuns.set(_threadKey(path), runId);
        }
      }
    }

    if (saved.conversationExternalIds && typeof saved.conversationExternalIds === 'object') {
      for (const [path, externalId] of Object.entries(saved.conversationExternalIds)) {
        if (typeof externalId === 'string' && externalId) {
          _conversationExternalIds.set(_threadKey(path), externalId);
        }
      }
    } else if (typeof saved.conversationExternalId === 'string' && saved.conversationExternalId) {
      _conversationExternalIds.set(_WORKSPACE_THREAD_KEY, saved.conversationExternalId);
    }

    if (saved.conversationTitles && typeof saved.conversationTitles === 'object') {
      for (const [path, title] of Object.entries(saved.conversationTitles)) {
        if (typeof title === 'string' && title.trim()) {
          _conversationTitles.set(_threadKey(path), title.trim());
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

    if (saved.focus && saved.open) {
      _setConversationFocus(true);
    }

    if (['continue', 'chat', 'explain', 'bughunt', 'refactor', 'tests'].includes(saved.mode)) {
      setMode(saved.mode);
      if (saved.mode === 'continue' && _continueControllerApi.isInProgress()) {
        void _continueControllerApi.resumeContinueIfNeeded();
      }
    }

    _setConversationTitle(_conversationTitleForPath(currentThreadPath()));
    return true;
  }

  (function _restore() {
    _setPanelWidth(_readSavedWidth());
    splitter.hidden = true;
    _legacyState = _loadState(_STATE_KEY);
    _restoreState(_legacyState);
  })();

  return {
    async handleWorkspaceRootChanged() {
      const storageKey = _workspaceStateKey(getWorkspaceRoot());
      const saved = _loadState(storageKey) || _legacyState;
      const restored = _restoreState(saved, { clear: true });
      _legacyState = null;
      if (!restored || !localStorage.getItem(storageKey)) _save();
      if (!_workspaceContextEnabled) return;
      try {
        await _rebuildWorkspaceMenu({ quiet: true });
      } catch (_) {}
    },
    onTabChange(path) {
      if (panel.hidden) return;
      _refreshSelectionFromEditor();
      const threadPath = currentThreadPath();
      _setConversationTitle(_conversationTitleForPath(threadPath));
      _syncThinkingNote();
      if (_mode !== 'continue') {
        renderThread(threadPath);
        if (threadPath && _shouldFetchRemoteThread(threadPath)) {
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
