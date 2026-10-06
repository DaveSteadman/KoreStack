import { initServiceShell } from '/ui-elements/assets/js/serviceShell.js';

const gridNode   = document.querySelector('#koreunittest-grid');
const statusNode = document.querySelector('#koreunittest-status');
const runNode    = document.querySelector('#koreunittest-run');
const summaryNode = document.querySelector('#koreunittest-summary');

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
  const finished = (detail.events || []).find((event) => event.type === 'finished');
  output.textContent = [`${detail.test_id} - ${detail.status} (${detail.build_id})`, finished?.summary?.output || finished?.summary?.error || ''].join('\n\n');
  output.style.cssText = 'max-width:80vw;max-height:70vh;overflow:auto;white-space:pre-wrap';
  close.className = 'kcui-tag kcui-tag--accent';
  close.textContent = 'Close';
  close.addEventListener('click', () => dialog.close());
  dialog.append(output, close);
  dialog.addEventListener('close', () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

function renderGrid(payload) {
  statusNode.textContent = `Current: ${payload.build_id}${payload.active ? ' — session running' : ''}`;
  statusNode.className = `kcui-tag kcui-tag--${payload.active ? 'warning' : 'dim'}`;
  const table = document.createElement('table');
  table.className = 'koreunittest-grid';
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
    id.textContent = `${test.area} / ${test.id}`;
    row.append(id);
    for (const build of payload.builds || []) {
      const cell = document.createElement('td');
      const result = test.results?.[build];
      if (!result) {
        cell.textContent = '—';
      } else {
        const dot = document.createElement('button');
        dot.className = `kcui-tag kcui-tag--${statusTone(result.status)} koreunittest-dot`;
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
  currentService: 'koreunittest',
  urls:           window.__koreSuiteUrls || {},
  path:           '/ui',
  section:        'results',
  shellMeta:      { results: { brandLabel: 'KoreUnitTest', overline: 'Build-keyed unit testing', brandIcon: 'koretest' } },
  shellTabs:      [{ key: 'results', label: 'Results', href: '/ui' }],
});
refresh().catch((error) => { statusNode.textContent = error.message; statusNode.className = 'kcui-tag kcui-tag--danger'; });
window.setInterval(() => refresh().catch(() => {}), 5_000);
