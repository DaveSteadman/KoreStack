// chat.js
//
// Chat panel for KoreCode.
//
// Modes:
//   chat     — conversational, one thread per file
//   continue — sends file context up to cursor, inserts ghost text; Tab=accept, else=cancel
//
// Submissions go to KoreAgent via:
//   POST {koreagent}/sessions/{session_id}/prompt  →  { run_id }
//   GET  {koreagent}/runs/{run_id}/stream          →  SSE events
//
// KoreAgent base URL: window.__koreSuiteUrls.koreagent (fallback: http://127.0.0.1:8605).

const _KOREAGENT_FALLBACK = 'http://127.0.0.1:8605';
const _STATE_KEY = 'korecode.chat-state';
const _WIDTH_KEY = 'korecode-chat-w';
const _MIN_PANEL_W = 260;
const _MAX_PANEL_W = 720;
const _DEFAULT_PANEL_W = 340;

// In-memory chat thread store: path → Array<{ role: 'user'|'assistant', text: string }>
const _threads = new Map();

function _saveState(open, mode, pendingRuns = {}) {
  try {
    const threads = {};
    for (const [path, msgs] of _threads) threads[path] = msgs;
    localStorage.setItem(_STATE_KEY, JSON.stringify({ open, mode, threads, pendingRuns }));
  } catch (_) {}
}

function _loadState() {
  try {
    const raw = localStorage.getItem(_STATE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (_) { return null; }
}

// SSE reader active during a streaming response.
let _activeReader = null;

/**
 * Initialise the chat panel.
 * @param {{
 *   getActiveTab:       () => { path: string } | null,
 *   getContinueContext: () => { path: string, text: string, offset: number } | null,
 *   insertContinuation: (text: string) => { accept(): void, cancel(): void },
 * }} opts
 * @returns {{ onTabChange: (path: string | null) => void }}
 */
export function initChat({ getActiveTab, getContinueContext, insertContinuation, insertFromChat = null, getEditorSelection = null }) {
  const panel         = document.getElementById('chat-panel');
  const splitter      = document.getElementById('chat-splitter');
  const thread        = document.getElementById('chat-thread');
  const input         = document.getElementById('chat-input');
  const sendBtn       = document.getElementById('btn-chat-send');
  const aiBtn         = document.getElementById('btn-ai');
  const btnModeChat    = document.getElementById('btn-mode-chat');
  const btnModeContinue = document.getElementById('btn-mode-continue');
  const selectionChip  = document.getElementById('chat-selection-chip');
  const selectionLabel = document.getElementById('chat-selection-label');
  const progressNote = document.getElementById('chat-progress-note');

  let _currentSelection = null;

  function _refreshSelectionFromEditor() {
    _currentSelection = getEditorSelection?.() ?? null;
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
      selectionLabel.textContent = `${lines} line${lines === 1 ? '' : 's'} selected · will be sent as context`;
    } else {
      selectionLabel.textContent = '0 lines selected · insert uses cursor position';
    }
    selectionChip.hidden = false;
  }

  let _mode = 'chat';   // 'chat' | 'continue'
  let _panelOpen = false;
  let _dragStartX = null;
  let _dragStartW = null;
  const _pendingRuns = new Map(); // path -> runId

  function _pendingToObject() {
    const out = {};
    for (const [path, runId] of _pendingRuns) out[path] = runId;
    return out;
  }

  function _save() { _saveState(_panelOpen, _mode, _pendingToObject()); }

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
    progressNote.hidden = !(path && _pendingRuns.has(path));
    if (!progressNote.hidden) {
      progressNote.textContent = 'Thinking...';
    }
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
      name === 'AbortError' ||
      msg.includes('aborted') ||
      msg.includes('failed to fetch') ||
      msg.includes('illegal access') ||
      msg.includes('access is denied') ||
      msg.includes('permission denied') ||
      msg.includes('securityerror') ||
      msg.includes('networkerror') ||
      msg.includes('stream is locked')
    );
  }

  function _safeCancelReader(reader) {
    if (!reader) return;
    try {
      const out = reader.cancel();
      if (out && typeof out.catch === 'function') {
        out.catch(() => {});
      }
    } catch (_) {
      // Ignore reader cancellation failures during tab focus/context changes.
    }
  }

  function _readSavedWidth() {
    try {
      const raw = Number.parseInt(localStorage.getItem(_WIDTH_KEY), 10);
      if (Number.isNaN(raw)) return _DEFAULT_PANEL_W;
      return Math.max(_MIN_PANEL_W, Math.min(_MAX_PANEL_W, raw));
    } catch (_) {
      return _DEFAULT_PANEL_W;
    }
  }

  function _setPanelWidth(widthPx) {
    const clamped = Math.max(_MIN_PANEL_W, Math.min(_MAX_PANEL_W, Math.round(widthPx)));
    panel.style.width = `${clamped}px`;
    try { localStorage.setItem(_WIDTH_KEY, String(clamped)); } catch (_) {}
  }

  function _setPanelOpen(open) {
    panel.hidden = !open;
    splitter.hidden = !open;
    _panelOpen = open;
    aiBtn.classList.toggle('is-active', open);
    _syncThinkingNote();
    _save();
  }

  function _stopResizeDrag() {
    if (_dragStartX === null) return;
    _dragStartX = null;
    _dragStartW = null;
    splitter.classList.remove('is-dragging');
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
  }

  // ── Helpers ─────────────────────────────────────────────────────────────

  function agentBase() {
    return ((window.__koreSuiteUrls?.koreagent) ?? _KOREAGENT_FALLBACK).replace(/\/$/, '');
  }

  /** Derive a stable, URL-safe session ID from the file path. */
  function sessionForPath(path) {
    const sanitized = path.replace(/[^A-Za-z0-9_-]/g, '_').slice(-60);
    return `kc_${sanitized}`;
  }

  function currentPath() {
    return getActiveTab()?.path ?? null;
  }

  // ── Mode bar ─────────────────────────────────────────────────────────────

  function setMode(mode) {
    _mode = mode;
    btnModeChat.classList.toggle('is-active', mode === 'chat');
    btnModeContinue.classList.toggle('is-active', mode === 'continue');
    // Continue mode: hide composer (no text input needed), show status area.
    const composer = document.getElementById('chat-composer');
    if (mode === 'continue') {
      composer.hidden = true;
      _showContinueStatus('idle');
    } else {
      composer.hidden = false;
      _clearContinueStatus();
    }
    _save();
  }

  btnModeChat.addEventListener('click', () => setMode('chat'));
  btnModeContinue.addEventListener('click', () => {
    setMode('continue');
    // Immediately run a continuation if a file is open.
    void _runContinue();
  });

  // ── Toggle ───────────────────────────────────────────────────────────────

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

  // Alt+A keyboard shortcut.
  document.addEventListener('keydown', (e) => {
    if (e.altKey && e.key === 'a') {
      e.preventDefault();
      aiBtn.click();
    }
  });

  // ── Submit ───────────────────────────────────────────────────────────────

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      void _send();
    }
  });

  sendBtn.addEventListener('click', () => void _send());

  async function _resumePendingForPath(path) {
    if (!path || _activeReader) return;
    const runId = _pendingRuns.get(path);
    if (!runId) return;

    const base = agentBase();
    const thinkingEl = _appendThinking('Resuming response…');
    sendBtn.disabled = true;

    try {
      const reply = await _streamResponse(base, runId, thinkingEl);
      _clearPending(path);
      if (reply.trim()) {
        _pushMessage(path, { role: 'assistant', text: reply });
      }
      renderThread(path);
    } catch (err) {
      thinkingEl?.remove();
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
      sendBtn.disabled = false;
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
      for (let i = turns.length - 1; i >= 0; i--) {
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

  async function _send() {
    _refreshSelectionFromEditor();
    const text = input.value.trim();
    const path = currentPath();
    if (!text || !path || _activeReader) return;
    if (_pendingRuns.has(path)) {
      void _resumePendingForPath(path);
      return;
    }

    const sel = _currentSelection;
    const prompt = sel
      ? `The following code is selected in the editor:\n\`\`\`\n${sel}\n\`\`\`\n\n${text}`
      : text;

    input.value = '';
    _autosize(input);
    _currentSelection = null;
    _updateSelectionChip();

    _pushMessage(path, { role: 'user', text });
    renderThread(path);

    const thinkingEl = _appendThinking();
    sendBtn.disabled = true;

    try {
      const sessionId = sessionForPath(path);
      const base      = agentBase();

      const resp = await fetch(`${base}/sessions/${encodeURIComponent(sessionId)}/prompt`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ prompt }),
      });

      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);

      const { run_id } = await resp.json();
      _setPending(path, run_id);
      const reply = await _streamResponse(base, run_id, thinkingEl);

      _clearPending(path);
      _pushMessage(path, { role: 'assistant', text: reply });
      renderThread(path);
    } catch (err) {
      thinkingEl?.remove();
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
      sendBtn.disabled = false;
      _activeReader    = null;
      _syncThinkingNote();
      _scrollBottom();
    }
  }

  // ── Continue mode ────────────────────────────────────────────────────────

  let _continueStatus = null;  // the current status element in the thread

  function _showContinueStatus(state, extra) {
    _clearContinueStatus();
    const el = document.createElement('div');
    el.id = 'continue-status';
    if (state === 'idle') {
      el.className = 'continue-status continue-status--idle';
      el.textContent = 'Click Continue to generate from cursor.';
    } else if (state === 'thinking') {
      el.className = 'continue-status continue-status--thinking';
      el.innerHTML = 'Generating\u2026 <span class="chat-thinking-dots"><span>\u2022</span><span>\u2022</span><span>\u2022</span></span>';
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
    _continueStatus = el;
    thread.scrollTop = thread.scrollHeight;
  }

  function _clearContinueStatus() {
    _continueStatus?.remove();
    _continueStatus = null;
  }

  async function _runContinue() {
    if (_activeReader) return;
    const ctx = getContinueContext?.();
    if (!ctx) {
      _showContinueStatus('error', 'No file open.');
      return;
    }

    _showContinueStatus('thinking');
    btnModeContinue.disabled = true;

    // One shared workspace session for Continue actions.
    const sessionId = 'kc_continue';
    const base      = agentBase();

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

    let controller = null;

    try {
      const resp = await fetch(`${base}/sessions/${encodeURIComponent(sessionId)}/prompt`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ prompt }),
      });
      if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);

      const { run_id } = await resp.json();
      const reply = await _streamContinue(base, run_id);

      if (!reply.trim()) {
        _showContinueStatus('cancelled', '');
        _showContinueStatus('error', 'Empty response.');
        return;
      }

      // Strip any accidental leading newline that appears when the model
      // outputs a blank first line before the code.
      const insertion = reply.replace(/^\n/, '');

      _showContinueStatus('preview');
      controller = insertContinuation(insertion);

      // Replace the preview status once the user decides.
      const origAccept = controller.accept.bind(controller);
      const origCancel = controller.cancel.bind(controller);

      controller.accept = () => {
        origAccept();
        _showContinueStatus('accepted', `${insertion.split('\n').length} line(s) inserted.`);
        btnModeContinue.disabled = false;
        setTimeout(() => setMode('chat'), 1500);
      };
      controller.cancel = () => {
        origCancel();
        _showContinueStatus('cancelled');
        btnModeContinue.disabled = false;
        setTimeout(() => setMode('chat'), 1500);
      };

      document.getElementById('btn-continue-accept')?.addEventListener('click', (e) => { e.stopPropagation(); controller.accept(); });
      document.getElementById('btn-continue-cancel')?.addEventListener('click', (e) => { e.stopPropagation(); controller.cancel(); });

    } catch (err) {
      _showContinueStatus('error', _errorText(err));
      btnModeContinue.disabled = false;
      _activeReader = null;
    }
  }

  async function _streamContinue(base, runId) {
    const response = await fetch(`${base}/runs/${encodeURIComponent(runId)}/stream`);
    if (!response.ok) throw new Error(`Stream ${response.status}`);

    const reader  = response.body.getReader();
    _activeReader = reader;
    const decoder = new TextDecoder();
    let   buffer  = '';
    let   reply   = '';

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
      if (!(reply.trim() && _isTransientStreamInterrupt(err))) {
        throw err;
      }
    } finally {
      _safeCancelReader(reader);
      _activeReader = null;
    }
    return reply;
  }

  // ── SSE streaming (chat mode) ────────────────────────────────────────────

  async function _streamResponse(base, runId, thinkingEl) {
    const response = await fetch(`${base}/runs/${encodeURIComponent(runId)}/stream`);
    if (!response.ok) throw new Error(`Stream ${response.status}`);

    const reader  = response.body.getReader();
    _activeReader = reader;
    const decoder = new TextDecoder();
    let   buffer  = '';
    let   reply   = '';

    thinkingEl?.remove();

    // A live <pre> element that updates as tokens arrive.
    const liveEl = _appendLivePre();

    try {
      outer: while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop(); // hold incomplete last line

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          let event;
          try { event = JSON.parse(raw); } catch { continue; }

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

  // ── Thread rendering ──────────────────────────────────────────────────────

  function _pushMessage(path, msg) {
    if (!_threads.has(path)) _threads.set(path, []);
    _threads.get(path).push(msg);
    _save();
  }

  function renderThread(path) {
    thread.innerHTML = '';
    if (!path) return;

    const msgs = _threads.get(path) ?? [];

    if (!msgs.length) {
      const empty = document.createElement('div');
      empty.id = 'chat-empty';
      empty.textContent = 'Ask about this file\u2026';
      thread.appendChild(empty);
      return;
    }

    for (let i = 0; i < msgs.length; i++) {
      const msg = msgs[i];
      thread.appendChild(_buildMsgEl(msg));

      // Divider after each completed assistant reply (not at end).
      if (msg.role === 'assistant' && i < msgs.length - 1) {
        const div = document.createElement('div');
        div.className = 'chat-divider';
        thread.appendChild(div);
      }
    }

    _scrollBottom();
  }

  function _buildMsgEl(msg) {
    const el = document.createElement('div');

    if (msg.role === 'user') {
      el.className = 'chat-msg chat-msg--user';
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.textContent = msg.text;
      el.appendChild(bubble);
    } else {
      el.className = 'chat-msg chat-msg--assistant';
      const avatar = document.createElement('div');
      avatar.className = 'avatar';
      avatar.textContent = 'Kore';
      const body = document.createElement('div');
      body.className = 'body';
      body.innerHTML = _renderAssistantText(msg.text);
      const codeText = _extractCodeForActions(msg.text);
      el.appendChild(avatar);
      el.appendChild(body);
      if (codeText) {
        el.appendChild(_buildAssistantActions(codeText));
      }
    }

    return el;
  }

  function _appendThinking(label = 'Kore is thinking') {
    const el = document.createElement('div');
    el.className = 'chat-thinking';
    el.innerHTML = `${_esc(label)} <span class="chat-thinking-dots"><span>\u2022</span><span>\u2022</span><span>\u2022</span></span>`;
    thread.appendChild(el);
    _scrollBottom();
    return el;
  }

  function _appendLivePre() {
    const el     = document.createElement('div');
    el.className = 'chat-msg chat-msg--assistant';
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = 'Kore';
    const body   = document.createElement('div');
    body.className = 'body';
    const pre    = document.createElement('pre');
    pre.style.cssText = 'margin:0;padding:0;background:none;border:none;white-space:pre-wrap;word-break:break-word;';
    body.appendChild(pre);
    el.appendChild(avatar);
    el.appendChild(body);
    thread.appendChild(el);
    _scrollBottom();
    return pre;
  }

  function _extractCodeForActions(text) {
    const blocks = [];
    const pattern = /```[^\n]*\n?([\s\S]*?)```/g;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      const snippet = (match[1] ?? '').trim();
      if (snippet) blocks.push(snippet);
    }
    if (!blocks.length) return null;
    return blocks.join('\n\n');
  }

  async function _copyText(text, btn) {
    const prev = btn.textContent;
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = 'copied';
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', 'readonly');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
        btn.textContent = 'copied';
      } finally {
        document.body.removeChild(ta);
      }
    }
    setTimeout(() => {
      btn.textContent = prev;
    }, 1000);
  }

  function _buildAssistantActions(codeText) {
    const row = document.createElement('div');
    row.className = 'chat-msg-actions';

    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'kcui-tag kcui-tag--info';
    copyBtn.textContent = 'copy';
    copyBtn.addEventListener('click', () => {
      void _copyText(codeText, copyBtn);
    });

    const insertBtn = document.createElement('button');
    insertBtn.type = 'button';
    insertBtn.className = 'kcui-tag kcui-tag--accent';
    insertBtn.textContent = 'insert';
    insertBtn.disabled = typeof insertFromChat !== 'function';
    insertBtn.addEventListener('click', () => {
      if (typeof insertFromChat !== 'function') return;
      const inserted = insertFromChat(codeText);
      const prev = insertBtn.textContent;
      insertBtn.textContent = inserted ? 'inserted' : 'no file';
      setTimeout(() => {
        insertBtn.textContent = prev;
      }, 1000);
    });

    row.appendChild(copyBtn);
    row.appendChild(insertBtn);
    return row;
  }

  function _scrollBottom() {
    thread.scrollTop = thread.scrollHeight;
  }

  // ── Text renderer ─────────────────────────────────────────────────────────
  // Splits on fenced code blocks. Everything else is emitted as <p> elements.

  function _esc(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function _renderAssistantText(text) {
    return text
      .split(/(```[\s\S]*?```)/g)
      .map((part) => {
        if (part.startsWith('```')) {
          const inner = part.replace(/^```[^\n]*\n?/, '').replace(/```$/, '');
          return `<pre>${_esc(inner)}</pre>`;
        }
        return part
          .split(/\n{2,}/)
          .map((para) => para.trim())
          .filter(Boolean)
          .map((para) => `<p>${_esc(para)}</p>`)
          .join('');
      })
      .join('');
  }

  // ── Auto-size textarea ────────────────────────────────────────────────────

  function _autosize(el) {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 100)}px`;
  }

  input.addEventListener('input', () => _autosize(input));

  // ── Restore persisted state ───────────────────────────────────────────────

  (function _restore() {
    _setPanelWidth(_readSavedWidth());
    splitter.hidden = true;

    const saved = _loadState();
    if (!saved) return;
    // Restore threads.
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
    _syncThinkingNote();
    // Restore panel open state.
    if (saved.open) {
      _setPanelOpen(true);
    }
    // Restore mode (thread render deferred until onTabChange fires after restoreTabs).
    if (saved.mode === 'continue' || saved.mode === 'chat') {
      setMode(saved.mode);
    }
  })();

  // ── Public API ────────────────────────────────────────────────────────────

  return {
    /** Call when the active editor tab changes so the thread re-renders. */
    onTabChange(path) {
      if (panel.hidden) return;
      _refreshSelectionFromEditor();
      _syncThinkingNote();
      if (_mode === 'chat') {
        renderThread(path);
        void _resumePendingForPath(path);
      }
      else _showContinueStatus('idle');
    },
    /** Call when the editor selection changes. */
    onSelectionChange(text) {
      _currentSelection = text;
      _updateSelectionChip();
    },
  };
}
