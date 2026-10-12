import { api } from './state.js';

export function initDebugger({ getActiveTab, getBreakpoints, revealLine, clearProgramCounter }) {
  const startButton = document.getElementById('btn-debug-start');
  const summary = document.getElementById('execution-summary');
  const status = document.getElementById('debug-status');
  const stack = document.getElementById('debug-stack');
  const locals = document.getElementById('debug-locals');
  const controls = {
    continue: document.getElementById('btn-debug-continue'),
    step: document.getElementById('btn-debug-step'),
    next: document.getElementById('btn-debug-next'),
    pause: document.getElementById('btn-debug-pause'),
    stop: document.getElementById('btn-debug-stop'),
  };
  let sessionId = null;
  let pollTimer = null;
  let programCounterKey = null;

  function setControls(snapshot) {
    const paused = snapshot?.status === 'paused';
    const running = snapshot?.status === 'running';
    controls.continue.disabled = !paused;
    controls.step.disabled = !paused;
    controls.next.disabled = !paused;
    controls.pause.disabled = !running;
    controls.stop.disabled = !sessionId || ['completed', 'error', 'stopped'].includes(snapshot?.status);
  }

  function render(snapshot) {
    const currentStatus = snapshot?.status || 'not started';
    stack.replaceChildren();
    locals.replaceChildren();

    const frames = Array.isArray(snapshot?.frames) ? snapshot.frames : [];
    const topFrame = frames[0];
    const breakpointReached = currentStatus === 'paused' && snapshot?.pause_reason === 'breakpoint' && topFrame;
    status.textContent = breakpointReached ? 'Breakpoint reached' : currentStatus.replace('_', ' ');
    status.dataset.state = breakpointReached ? 'breakpoint' : currentStatus;
    status.title = snapshot?.error || snapshot?.pause_reason || currentStatus;
    summary.textContent = sessionId
      ? breakpointReached
        ? `BREAKPOINT REACHED · L${topFrame.line} · ${topFrame.path}`
        : `DEBUG ${snapshot?.path || ''} · ${snapshot?.error || currentStatus}`
      : 'No debug session';
    if (currentStatus === 'paused' && topFrame) {
      const key = `${topFrame.path}:${topFrame.line}`;
      if (key !== programCounterKey) {
        programCounterKey = key;
        void revealLine(topFrame.path, topFrame.line, { programCounter: true });
      }
    } else if (programCounterKey) {
      programCounterKey = null;
      clearProgramCounter();
    }
    if (!frames.length) {
      stack.textContent = currentStatus === 'error' ? snapshot.error || 'Debug session failed.' : 'No paused frame.';
      stack.className = 'debug-empty';
    } else {
      stack.className = 'debug-list';
      frames.forEach((frame, index) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'debug-frame';
        item.textContent = `${index === 0 ? '▶ ' : ''}${frame.name} · ${frame.path}:${frame.line}`;
        item.title = frame.source || '';
        item.addEventListener('click', () => { void revealLine(frame.path, frame.line, { programCounter: index === 0 }); });
        stack.appendChild(item);
      });
    }

    const variables = Array.isArray(snapshot?.locals) ? snapshot.locals : [];
    if (!variables.length) {
      locals.textContent = 'No local variables.';
      locals.className = 'debug-empty';
    } else {
      locals.className = 'debug-list';
      variables.forEach(variable => {
        const item = document.createElement('div');
        item.className = 'debug-variable';
        item.title = `${variable.type}: ${variable.value}`;
        item.innerHTML = `<span class="debug-variable-name"></span><span class="debug-variable-value"></span>`;
        item.querySelector('.debug-variable-name').textContent = variable.name;
        item.querySelector('.debug-variable-value').textContent = variable.value;
        locals.appendChild(item);
      });
    }
    setControls(snapshot);
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  async function refresh() {
    if (!sessionId) return;
    try {
      const snapshot = await api(`/api/debug/${encodeURIComponent(sessionId)}`);
      render(snapshot);
      if (['completed', 'error', 'stopped'].includes(snapshot.status)) stopPolling();
    } catch (error) {
      status.textContent = 'unavailable';
      stack.textContent = String(error?.message || error);
      stopPolling();
    }
  }

  async function start() {
    const active = getActiveTab();
    if (!active?.path?.endsWith('.py')) {
      summary.textContent = 'Open a Python file to debug.';
      return;
    }
    startButton.disabled = true;
    try {
      const snapshot = await api('/api/debug/python', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: active.path, breakpoints: getBreakpoints() }),
      });
      sessionId = snapshot.id;
      render(snapshot);
      stopPolling();
      pollTimer = setInterval(() => { void refresh(); }, 350);
      await refresh();
    } catch (error) {
      summary.textContent = `Debug start failed: ${error?.message || error}`;
    } finally {
      startButton.disabled = false;
    }
  }

  async function control(action) {
    if (!sessionId) return;
    try {
      render({ status: action === 'stop' ? 'stopping' : 'running', path: getActiveTab()?.path });
      const snapshot = await api(`/api/debug/${encodeURIComponent(sessionId)}/control`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      render(snapshot);
      await refresh();
    } catch (error) {
      summary.textContent = `Debug ${action} failed: ${error?.message || error}`;
    }
  }

  startButton.addEventListener('click', () => { void start(); });
  Object.entries(controls).forEach(([action, button]) => {
    button.addEventListener('click', () => { void control(action); });
  });
  setControls(null);
  return { refresh };
}
