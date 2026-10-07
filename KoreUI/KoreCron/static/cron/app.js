import { initServiceShell } from '/ui-elements/assets/js/serviceShell.js';
import { initDialogHost, kcuiConfirm } from '/ui-elements/assets/js/dialogs.js';

initServiceShell({
  currentService: 'korecron',
  urls:           window.__koreSuiteUrls || {},
  path:           '/ui',
  section:        'cron',
  shellMeta:      { cron: { brandLabel: 'KoreCron', overline: 'Scheduled Triggers', brandIcon: 'korecron' } },
  shellTabs:      [{ key: 'cron', label: 'Triggers', href: '/ui' }],
});
initDialogHost();

const style = document.createElement('style');
style.textContent = `
  .cron-toolbar { margin-bottom: 12px; }
  .cron-list { display: grid; gap: 8px; }
  .cron-list__empty { padding: 14px 16px; border: 1px dashed var(--border, rgba(255,255,255,.16)); color: var(--text-muted, rgba(255,255,255,.72)); }
  .cron-row { display: grid; gap: 4px; padding: 10px 14px; border: 1px solid var(--border, rgba(255,255,255,.16)); background: rgba(255,255,255,.02); cursor: pointer; }
  .cron-row:hover { background: rgba(96,165,250,.08); }
  .cron-row.is-selected { border-color: rgba(96,165,250,.65); background: rgba(96,165,250,.14); }
  .cron-row.is-off .cron-row__title { opacity: .5; }
  .cron-row__title { display: flex; justify-content: space-between; gap: 10px; font-weight: 600; }
  .cron-row__meta { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: .85em; color: var(--text-muted, rgba(255,255,255,.72)); }
  .cron-enabled { display: flex; align-items: center; gap: 8px; margin-top: 10px; }
  .cron-info { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; margin: 14px 0; font-size: .9em; }
  .cron-info dt { color: var(--text-muted, rgba(255,255,255,.72)); }
  .cron-info dd { margin: 0; overflow-wrap: anywhere; }
  .cron-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 14px; }
`;
document.head.append(style);

const $ = (selector) => document.querySelector(selector);
const form = $('#trigger-form'), empty = $('#detail-empty'), info = $('#trigger-info');
const nameInput = $('#trigger-name'), targetSelect = $('#trigger-target'), scheduleInput = $('#trigger-schedule'), enabledInput = $('#trigger-enabled');
const saveButton = $('#trigger-save'), runButton = $('#trigger-run'), deleteButton = $('#trigger-delete');
const formStatus = $('#form-status'), rows = $('#trigger-rows'), eyebrow = $('#detail-eyebrow');

let triggers = [];
let selectedId = null;   // trigger id, 'new', or null
let dirty = false;

const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const status = (text, kind = 'dim') => { formStatus.textContent = text; formStatus.className = `kcui-tag kcui-tag--${kind}`; };
const stamp = (iso) => iso ? iso.replace('T', ' ') : '—';
const selected = () => triggers.find((t) => t.id === selectedId);

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

async function loadTargets() {
  const { targets, networks } = await api('/api/targets');
  targetSelect.innerHTML = targets.map((t) => `<option value="${escapeHtml(t.key)}">${escapeHtml(t.label)}</option>`).join('')
    + networks.map((n) => `<option value="network:${escapeHtml(n.id)}">Network: ${escapeHtml(n.title)}</option>`).join('');
}

const targetValue = (t) => t.target === 'network' ? `network:${t.network_id}` : t.target;
const outcomeText = (t) => {
  const run = t.run_status || {};
  if (run.status === 'running') return 'running…';
  if (run.status && run.status !== 'idle') return `${run.status}: ${run.detail || ''}`;
  if (!t.last_outcome) return 'never run';
  return t.last_outcome.succeeded ? 'last run ok' : `last run failed: ${t.last_outcome.error}`;
};

function renderList() {
  if (!triggers.length) { rows.innerHTML = '<div class="cron-list__empty">No triggers yet.</div>'; return; }
  rows.innerHTML = triggers.map((t) => `<div class="cron-row ${t.id === selectedId ? 'is-selected' : ''} ${t.enabled ? '' : 'is-off'}" role="listitem" tabindex="0" data-id="${escapeHtml(t.id)}">
    <div class="cron-row__title"><span class="cron-row__name">${escapeHtml(t.name)}</span><span>${t.enabled ? '' : 'off'}</span></div>
    <div class="cron-row__meta"><span>${escapeHtml(t.target_text)}</span><span>${escapeHtml(t.schedule_text)}</span><span>${escapeHtml(outcomeText(t))}</span></div>
  </div>`).join('');
}

function renderInfo() {
  const t = selected();
  info.innerHTML = t ? [['Next run', stamp(t.next_fire)], ['Last run', stamp(t.last_run)], ['Outcome', outcomeText(t)]]
    .map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join('') : '';
}

function fillForm() {
  const t = selected();
  const creating = selectedId === 'new';
  empty.hidden = Boolean(selectedId);
  form.hidden = !selectedId;
  eyebrow.textContent = creating ? 'New trigger' : t ? t.name : 'Details';
  $('#detail-blurb').textContent = selectedId ? 'Edit the schedule and target, then save.' : 'Select a trigger on the left, or create a new one.';
  if (!selectedId) return;
  nameInput.value = t?.name ?? '';
  targetSelect.value = t ? targetValue(t) : targetSelect.options[0]?.value;
  scheduleInput.value = t ? (t.schedule.type === 'daily' ? t.schedule.time : t.schedule.minutes) : '';
  enabledInput.checked = t ? t.enabled : true;
  saveButton.textContent = creating ? 'Create trigger' : 'Save changes';
  runButton.hidden = deleteButton.hidden = creating;
  dirty = false;
  status('Ready');
  renderInfo();
}

function select(id) { selectedId = id; renderList(); fillForm(); }

async function refresh() {
  try {
    triggers = (await api('/api/triggers')).triggers.sort((a, b) => (a.next_fire || '9999').localeCompare(b.next_fire || '9999') || a.name.localeCompare(b.name));
    if (selectedId && selectedId !== 'new' && !selected()) selectedId = null;
    renderList();
    if (!dirty) { if (selectedId && selectedId !== 'new') renderInfo(); else if (!selectedId) fillForm(); }
  } catch (error) { status(error.message, 'warning'); }
}

form.addEventListener('input', () => { dirty = true; });

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const [target, networkId = ''] = targetSelect.value.split(/:(.+)/);
  const body = { name: nameInput.value, target, network_id: networkId, schedule: scheduleInput.value, enabled: enabledInput.checked };
  try {
    const creating = selectedId === 'new';
    const saved = await api(creating ? '/api/triggers' : `/api/triggers/${encodeURIComponent(selectedId)}`, { method: creating ? 'POST' : 'PUT', body: JSON.stringify(body) });
    dirty = false;
    await refresh();
    select(saved.id);
    status('Saved', 'accent');
  } catch (error) { status(error.message, 'warning'); }
});

runButton.addEventListener('click', async () => {
  try { await api(`/api/triggers/${encodeURIComponent(selectedId)}/run`, { method: 'POST' }); status('Triggered', 'accent'); await refresh(); }
  catch (error) { status(error.message, 'warning'); }
});

deleteButton.addEventListener('click', async () => {
  const t = selected();
  if (!t || !(await kcuiConfirm(`Delete ${t.name}?`))) return;
  try { await api(`/api/triggers/${encodeURIComponent(t.id)}`, { method: 'DELETE' }); selectedId = null; await refresh(); fillForm(); }
  catch (error) { status(error.message, 'warning'); }
});

$('#new-trigger').addEventListener('click', () => select('new'));
rows.addEventListener('click', (event) => { const row = event.target.closest('.cron-row'); if (row) select(row.dataset.id); });
rows.addEventListener('keydown', (event) => { const row = event.target.closest('.cron-row'); if (row && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select(row.dataset.id); } });

loadTargets().then(refresh).then(fillForm).catch((error) => status(error.message, 'warning'));
setInterval(refresh, 5000);