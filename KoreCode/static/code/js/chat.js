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

// In-memory chat thread store: path → Array<{ role: 'user'|'assistant', text: string }>
const _threads = new Map();

function _saveState(open, mode) {
  try {
    const threads = {};
    for (const [path, msgs] of _threads) threads[path] = msgs;
    localStorage.setItem(_STATE_KEY, JSON.stringify({ open, mode, threads }));
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
export function initChat({ getActiveTab, getContinueContext, insertContinuation, getEditorSelection = null }) {
  const panel         = document.getElementById('chat-panel');
  const thread        = document.getElementById('chat-thread');
  const input         = document.getElementById('chat-input');
  const sendBtn       = document.getElementById('btn-chat-send');
  const aiBtn         = document.getElementById('btn-ai');
  const btnModeChat    = document.getElementById('btn-mode-chat');
  const btnModeContinue = document.getElementById('btn-mode-continue');
  const selectionChip  = document.getElementById('chat-selection-chip');
  const selectionLabel = document.getElementById('chat-selection-label');

  let _currentSelection = null;

  function _updateSelectionChip() {
    if (!selectionChip) return;
    if (_currentSelection && !panel.hidden) {
      const lines = _currentSelection.split('\n').length;
      selectionLabel.textContent = `${lines} line${lines === 1 ? '' : 's'} selected · will be sent as context`;
      selectionChip.hidden = false;
    } else {
      selectionChip.hidden = true;
    }
  }

  let _mode = 'chat';   // 'chat' | 'continue'
  let _panelOpen = false;

  function _save() { _saveState(_panelOpen, _mode); }

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
    panel.hidden = !nowOpen;
    _panelOpen = nowOpen;
    aiBtn.classList.toggle('is-active', nowOpen);
    _save();
    if (nowOpen) {
      _currentSelection = getEditorSelection?.() ?? null;
      _updateSelectionChip();
      renderThread(currentPath());
      input.focus();
    } else {
      selectionChip && (selectionChip.hidden = true);
    }
  });

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

  async function _send() {
    const text = input.value.trim();
    const path = currentPath();
    if (!text || !path || _activeReader) return;

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
      const reply = await _streamResponse(base, run_id, thinkingEl);

      _pushMessage(path, { role: 'assistant', text: reply });
      renderThread(path);
    } catch (err) {
      thinkingEl?.remove();
      _pushMessage(path, { role: 'assistant', text: `Error: ${err.message}` });
      renderThread(path);
    } finally {
      sendBtn.disabled = false;
      _activeReader    = null;
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
      _showContinueStatus('error', err.message);
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
    } finally {
      reader.cancel();
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
    } finally {
      reader.cancel();
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
      el.appendChild(avatar);
      el.appendChild(body);
    }

    return el;
  }

  function _appendThinking() {
    const el = document.createElement('div');
    el.className = 'chat-thinking';
    el.innerHTML = 'Kore is thinking <span class="chat-thinking-dots"><span>\u2022</span><span>\u2022</span><span>\u2022</span></span>';
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
    const saved = _loadState();
    if (!saved) return;
    // Restore threads.
    if (saved.threads) {
      for (const [path, msgs] of Object.entries(saved.threads)) {
        if (Array.isArray(msgs) && msgs.length) _threads.set(path, msgs);
      }
    }
    // Restore panel open state.
    if (saved.open) {
      _panelOpen = true;
      panel.hidden = false;
      aiBtn.classList.add('is-active');
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
      if (_mode === 'chat') renderThread(path);
      else _showContinueStatus('idle');
    },
    /** Call when the editor selection changes. */
    onSelectionChange(text) {
      _currentSelection = text;
      _updateSelectionChip();
    },
  };
}
