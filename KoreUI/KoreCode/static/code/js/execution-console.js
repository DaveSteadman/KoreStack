import { api } from './state.js';

export function initExecutionConsole({ getActiveTab, getCursorInfo, openFile, setActiveTab }) {
  const panel             = document.getElementById('execution-panel');
  const output            = document.getElementById('execution-output');
  const summary           = document.getElementById('execution-summary');
  const hierarchy         = document.getElementById('call-hierarchy-content');
  const runButton         = document.getElementById('btn-run-active');
  const checkButton       = document.getElementById('btn-check-active');
  const clearButton       = document.getElementById('btn-clear-execution');
  const toggleButton      = document.getElementById('btn-toggle-execution');
  const refreshHierarchy  = document.getElementById('btn-refresh-hierarchy');

  function showResult(result) {
    const exit = result?.timed_out ? 'timed out' : `exit ${result?.exit_code ?? 'unknown'}`;
    const mode = String(result?.mode || 'run').toUpperCase();
    summary.textContent = `${mode} ${result?.path || ''} · ${exit}`;
    const sections = [];
    if (result?.command) sections.push(`$ ${result.command.join(' ')}`);
    if (result?.stdout) sections.push(`[stdout]\n${result.stdout}`);
    if (result?.stderr) sections.push(`[stderr]\n${result.stderr}`);
    if (!sections.length) sections.push(result?.ok ? 'Process completed without output.' : 'Process failed without captured output.');
    if (result?.output_truncated) sections.push('[output truncated]');
    output.textContent = sections.join('\n\n');
    panel.classList.remove('is-collapsed');
    toggleButton.textContent = 'Collapse';
    toggleButton.setAttribute('aria-pressed', 'true');
  }

  async function execute(mode) {
    const active = getActiveTab();
    if (!active?.path || !active.path.endsWith('.py')) {
      summary.textContent = 'Open a Python file to run or check it.';
      return;
    }
    runButton.disabled = true;
    checkButton.disabled = true;
    summary.textContent = `${mode === 'run' ? 'Running' : 'Checking'} ${active.path}...`;
    try {
      showResult(await api('/api/execution/python', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: active.path, mode }),
      }));
    } catch (error) {
      summary.textContent = 'Execution request failed';
      output.textContent = String(error?.message || error);
    } finally {
      runButton.disabled = false;
      checkButton.disabled = false;
    }
  }

  function renderHierarchy(symbol, callers, callees) {
    hierarchy.replaceChildren();
    if (!symbol) {
      hierarchy.textContent = 'No indexed symbol at the current cursor location.';
      return;
    }
    const title = document.createElement('div');
    title.className = 'hierarchy-symbol';
    title.textContent = symbol.qualname;
    hierarchy.appendChild(title);
    for (const [label, entries, formatter] of [
      ['Callers', callers, (entry) => `${entry.caller_qualname} · L${entry.line}`],
      ['Callees', callees, (entry) => `${entry.target_qualname || entry.call_qualname} · L${entry.line}`],
    ]) {
      const group = document.createElement('div');
      group.className = 'hierarchy-group';
      const heading = document.createElement('strong');
      heading.textContent = label;
      group.appendChild(heading);
      if (!entries.length) {
        const empty = document.createElement('div');
        empty.textContent = 'None found';
        group.appendChild(empty);
      }
      for (const entry of entries) {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'hierarchy-item';
        item.textContent = formatter(entry);
        item.addEventListener('click', () => {
          const path = entry.caller_path || entry.target_path;
          if (!path) return;
          void openFile(path).then(() => setActiveTab(path));
        });
        group.appendChild(item);
      }
      hierarchy.appendChild(group);
    }
  }

  async function refreshCallHierarchy() {
    const active = getActiveTab();
    const cursor = getCursorInfo();
    if (!active?.path || !active.path.endsWith('.py')) {
      hierarchy.textContent = 'Open a Python file to inspect its static call hierarchy.';
      return;
    }
    try {
      const payload = await api(`/api/workspace-index/symbols?path=${encodeURIComponent(active.path)}&limit=200`);
      const symbols = Array.isArray(payload.symbols) ? payload.symbols : [];
      const symbol = symbols.find((item) => cursor.line >= item.line_start && cursor.line <= item.line_end) || null;
      if (!symbol) return renderHierarchy(null, [], []);
      const [callers, callees] = await Promise.all([
        api(`/api/workspace-index/callers?qualname=${encodeURIComponent(symbol.qualname)}`),
        api(`/api/workspace-index/callees?qualname=${encodeURIComponent(symbol.qualname)}`),
      ]);
      renderHierarchy(symbol, callers.callers || [], callees.callees || []);
    } catch (error) {
      hierarchy.textContent = `Build the workspace index to inspect calls. (${String(error?.message || error)})`;
    }
  }

  runButton.addEventListener('click', () => { void execute('run'); });
  checkButton.addEventListener('click', () => { void execute('check'); });
  clearButton.addEventListener('click', () => { summary.textContent = 'No run yet'; output.textContent = ''; });
  toggleButton.addEventListener('click', () => {
    const collapsed = panel.classList.toggle('is-collapsed');
    toggleButton.textContent = collapsed ? 'Expand' : 'Collapse';
    toggleButton.setAttribute('aria-pressed', collapsed ? 'false' : 'true');
  });
  refreshHierarchy.addEventListener('click', () => { void refreshCallHierarchy(); });

  window.__kcShowExecution = showResult;
  return { showResult, refreshCallHierarchy };
}
