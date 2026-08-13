import { initServiceShell } from '/ui-elements/assets/js/serviceShell.js';

const suiteSelect   = document.querySelector('#suite');
const form          = document.querySelector('#system-test-form');
const runButton     = document.querySelector('#run-suite');
const runStatus     = document.querySelector('#run-status');
const runResult     = document.querySelector('#run-result');
const trendSummary  = document.querySelector('#trend-summary');
const trendRows     = document.querySelector('#trend-rows');
const runDetails    = document.querySelector('#run-details');
let isRunInProgress = false;

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

function setRunInProgress(running) {
  isRunInProgress    = running;
  runButton.disabled  = running;
  suiteSelect.disabled = running;
}

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hours   = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest    = seconds % 60;
  return [hours, minutes, rest].map((part) => String(part).padStart(2, '0')).join(':');
}

function renderRunDetails(runs) {
  if (!runs.length) {
    runDetails.innerHTML = '<tr><td colspan="4">No active run.</td></tr>';
    return;
  }
  const ordered = [...runs].sort((left, right) => String(left.started_at).localeCompare(String(right.started_at)));
  runDetails.replaceChildren(...ordered.map((run) => {
    const result = run.result || {};
    const progress = result.progress || {};
    const row = document.createElement('tr');
    const cells = [
      run.suite,
      `${progress.completed_tests ?? 0}/${progress.total_tests ?? '—'} exchanges`,
      `${progress.passed_tests ?? result.passed ?? 0}/${progress.completed_tests ?? result.total ?? 0}`,
      run.status,
    ];
    for (const value of cells) {
      const cell = document.createElement('td');
      cell.textContent = value;
      row.append(cell);
    }
    return row;
  }));
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
    row.innerHTML = `<td>${point.label}</td><td>${point.passed}/${point.total} (${point.pass_rate}%)</td><td>${formatDuration(point.duration_seconds)}</td><td>${tokens}</td>`;
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
  suiteSelect.replaceChildren(
    new Option('All prompt suites (release run)', 'all'),
    ...(payload.suites || []).map((name) => new Option(name, name)),
  );
  await loadTrend();
}

async function loadRunState() {
  const response = await fetch('/api/runs?limit=50');
  const payload = await response.json();
  const runs = payload.runs || [];
  const active = runs.find((run) => run.status === 'running' && run.suite !== 'all');
  const latest = runs[0];
  const collectionRuns = active?.collection_id
    ? runs.filter((run) => run.collection_id === active.collection_id)
    : active ? [active] : latest ? [latest] : [];
  renderRunDetails(collectionRuns.filter((run) => run.suite !== 'all'));
  if (active) {
    setRunInProgress(true);
    const progress = active.result?.progress || {};
    setTag(runStatus, `Running ${active.suite}: ${progress.completed_tests ?? 0}/${progress.total_tests ?? '—'}`, 'warning');
    setTag(runResult, `${active.suite}: ${progress.passed_tests ?? 0}/${progress.completed_tests ?? 0} passed`, 'warning');
  } else if (isRunInProgress) {
    setRunInProgress(false);
    setTag(runStatus, 'Ready', 'dim');
  }
  if (!latest || active) return;
  const result = latest.result || {};
  const label = result.stats_line || `${latest.suite}: ${result.passed ?? '—'}/${result.total ?? '—'} · ${latest.status}`;
  setTag(runResult, label, latest.status === 'passed' ? 'success' : latest.status === 'running' ? 'warning' : 'danger');
}

suiteSelect.addEventListener('change', loadTrend);

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  setRunInProgress(true);
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
    setTag(runResult, result.stats_line || `${result.suite}: ${result.passed}/${result.total}`, success ? 'success' : 'danger');
    await loadTrend();
    await loadRunState();
  } catch (error) {
    setTag(runStatus, 'Request failed', 'danger');
    setTag(runResult, String(error), 'danger');
  } finally {
    setRunInProgress(false);
  }
});

Promise.all([loadSuites(), loadRunState()]).catch((error) => setTag(runStatus, String(error), 'danger'));
setInterval(() => loadRunState().catch((error) => setTag(runStatus, String(error), 'danger')), 2000);
