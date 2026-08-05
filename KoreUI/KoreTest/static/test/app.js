import { initServiceShell } from '/ui-elements/assets/js/serviceShell.js';

const suiteSelect   = document.querySelector('#suite');
const form          = document.querySelector('#system-test-form');
const runButton     = document.querySelector('#run-suite');
const runStatus     = document.querySelector('#run-status');
const runResult     = document.querySelector('#run-result');
const trendSummary  = document.querySelector('#trend-summary');
const trendRows     = document.querySelector('#trend-rows');

initServiceShell({
  currentService: 'koretest',
  urls:           window.__koreSuiteUrls || {},
  path:           window.location.pathname,
  section:        'system-tests',
  shellMeta: {
    'system-tests': { brandLabel: 'KoreTest', overline: 'System Test Control', brandIcon: 'koretest' },
  },
  shellTabs: [{ key: 'system-tests', label: 'System Tests', href: '/ui' }],
});

function setTag(element, text, variant = 'dim') {
  element.textContent = text;
  element.className = `kcui-tag kcui-tag--${variant}`;
}

function renderTrend(points) {
  if (!points.length) {
    trendSummary.textContent = 'No historical runs for the selected suite.';
    trendRows.replaceChildren(Object.assign(document.createElement('tr'), {
      innerHTML: '<td colspan="4">No history recorded.</td>',
    }));
    return;
  }
  trendSummary.textContent = `${points.length} recorded run${points.length === 1 ? '' : 's'} for ${suiteSelect.value}.`;
  trendRows.replaceChildren(...points.map((point) => {
    const row = document.createElement('tr');
    const tokens = point.prompt_tokens === null ? '—' : point.prompt_tokens.toLocaleString();
    row.innerHTML = `<td>${point.label}</td><td>${point.passed}/${point.total} (${point.pass_rate}%)</td><td>${point.duration_seconds.toFixed(1)}s</td><td>${tokens}</td>`;
    return row;
  }));
}

async function loadTrend() {
  trendSummary.textContent = `Loading history for ${suiteSelect.value}.`;
  trendRows.replaceChildren(Object.assign(document.createElement('tr'), {
    innerHTML: '<td colspan="4">Loading…</td>',
  }));
  const response = await fetch(`/api/trend-points?suite=${encodeURIComponent(suiteSelect.value)}`);
  const payload = await response.json();
  renderTrend(payload.points || []);
}

async function loadSuites() {
  const response = await fetch('/api/suites');
  const payload = await response.json();
  suiteSelect.replaceChildren(...(payload.suites || []).map((name) => new Option(name, name)));
  await loadTrend();
}

async function loadLatestRun() {
  const response = await fetch('/api/runs?limit=1');
  const payload = await response.json();
  const latest = (payload.runs || [])[0];
  if (!latest) return;
  const result = latest.result || {};
  const label = `${latest.suite}: ${result.passed ?? '—'}/${result.total ?? '—'} · ${latest.status}`;
  setTag(runResult, label, latest.status === 'passed' ? 'success' : latest.status === 'running' ? 'warning' : 'danger');
}

suiteSelect.addEventListener('change', loadTrend);

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  runButton.disabled = true;
  setTag(runStatus, 'Running', 'warning');
  try {
    const response = await fetch('/api/runs', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ suite: suiteSelect.value }),
    });
    const result = await response.json();
    const success = response.ok && result.status === 'passed';
    setTag(runStatus, success ? 'Passed' : 'Failed', success ? 'success' : 'danger');
    setTag(runResult, `${result.suite}: ${result.passed}/${result.total}`, success ? 'success' : 'danger');
    await loadTrend();
    await loadLatestRun();
  } catch (error) {
    setTag(runStatus, 'Request failed', 'danger');
    setTag(runResult, String(error), 'danger');
  } finally {
    runButton.disabled = false;
  }
});

Promise.all([loadSuites(), loadLatestRun()]).catch((error) => setTag(runStatus, String(error), 'danger'));
