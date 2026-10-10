import { initServiceShell } from '/ui-elements/assets/js/serviceShell.js';

const gridNode   = document.querySelector('#koretest2-grid');
const statusNode = document.querySelector('#koretest2-status');
const runNode    = document.querySelector('#koretest2-run');
const summaryNode = document.querySelector('#koretest2-summary');

function statusTone(status) {
  if (status === 'passed') return 'success';
  if (status === 'failed' || status === 'error') return 'danger';
  if (status === 'timeout') return 'warning';
  return 'dim';
}

async function showDetail(testId, buildId) {
  const response = await fetch(`/api/runs/${encodeURIComponent(testId)}?build_id=${encodeURIComponent(buildId)}`);
  const detail = await response.json();
  const dialog = document.createElement('dialog');
  const output = document.createElement('pre');
  const close = document.createElement('button');
  output.textContent = JSON.stringify(detail, null, 2);
  close.className = 'kcui-tag kcui-tag--accent';
  close.textContent = 'Close';
  close.addEventListener('click', () => dialog.close());
  dialog.append(output, close);
  dialog.addEventListener('close', () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

let lastPayload = null;

function renderGrid(payload) {
  lastPayload = payload;
  statusNode.textContent = `Current: ${payload.build_id}${payload.active ? ' — session running' : ''}`;
  statusNode.className = `kcui-tag kcui-tag--${payload.active ? 'warning' : 'dim'}`;
  const table = document.createElement('table');
  table.className = 'koretest2-grid';
  const header = document.createElement('tr');
  const testHeader = document.createElement('th');
  testHeader.textContent = 'Test ID';
  header.append(testHeader);
  for (const build of payload.builds || []) {
    const cell = document.createElement('th');
    cell.textContent = String(build).replace(/^Build:\s*/i, '').match(/^\S+/)?.[0] || build;
    cell.title = build;
    header.append(cell);
  }
  table.append(header);
  for (const test of payload.tests || []) {
    const row = document.createElement('tr');
    const id = document.createElement('th');
    id.textContent = test.id;
    row.append(id);
    for (const build of payload.builds || []) {
      const cell = document.createElement('td');
      const result = test.results?.[build];
      if (!result) {
        cell.textContent = '—';
      } else {
        const dot = document.createElement('button');
        dot.className = `kcui-tag kcui-tag--${statusTone(result.status)} koretest2-dot`;
        dot.textContent = '●';
        dot.title = `${test.id}: ${result.status}`;
        dot.addEventListener('click', () => showDetail(test.id, build));
        cell.append(dot);
      }
      row.append(cell);
    }
    table.append(row);
  }
  gridNode.replaceChildren(table);
  fitColumns(table);
}

// Drop the oldest (rightmost) build columns until the table fits the available width.
function fitColumns(table) {
  const rows = [...table.rows];
  while (rows[0].cells.length > 2 && table.offsetWidth > gridNode.clientWidth) {
    for (const row of rows) row.deleteCell(-1);
  }
}

runNode.addEventListener('click', async () => {
  runNode.disabled = true;
  try {
    const response = await fetch('/api/sessions', { method: 'POST' });
    if (!response.ok) throw new Error(`Unable to start run (${response.status})`);
    await refresh();
  } catch (error) {
    statusNode.textContent = error.message;
    statusNode.className = 'kcui-tag kcui-tag--danger';
  } finally {
    runNode.disabled = false;
  }
});

async function refresh() {
  const response = await fetch('/api/grid', { cache: 'no-store' });
  if (!response.ok) throw new Error(`Unable to load results (${response.status})`);
  renderGrid(await response.json());
  const status = await (await fetch('/status', { cache: 'no-store' })).json();
  summaryNode.textContent = status.line || '';
}

initServiceShell({
  currentService: 'koretest2',
  urls:           window.__koreSuiteUrls || {},
  path:           '/ui',
  section:        'results',
  shellMeta:      { results: { brandLabel: 'KoreTest2', overline: 'Build-keyed prompt testing', brandIcon: 'koretest' } },
  shellTabs:      [{ key: 'results', label: 'Results', href: '/ui' }],
});
refresh().catch((error) => { statusNode.textContent = error.message; statusNode.className = 'kcui-tag kcui-tag--danger'; });
window.addEventListener('resize', () => { if (lastPayload) renderGrid(lastPayload); });
window.setInterval(() => refresh().catch(() => {}), 5_000);
