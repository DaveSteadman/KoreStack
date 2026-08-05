import { initServiceShell } from '/ui-elements/assets/js/serviceShell.js';

const workspace    = document.querySelector('.kcui-workspace');
const form         = document.querySelector('#cronprompt-form');
const nameInput    = document.querySelector('#cron-name');
const chatInput    = document.querySelector('#chat-name');
const promptList   = document.querySelector('#prompt-list');
const addPrompt    = document.querySelector('#add-prompt');
const saveButton   = document.querySelector('#save-cronprompt');
const formStatus   = document.querySelector('#form-status');
const rows         = document.querySelector('#cron-rows');
const timelineRows = document.querySelector('#timeline-rows');

workspace?.classList.add('kcui-workspace--two-columns', 'kcui-workspace--timeline-left', 'kcui-workspace--stack-sm');
initServiceShell({
  currentService: 'korecron',
  urls:           window.__koreSuiteUrls || {},
  path:           '/ui',
  section:        'cron',
  shellMeta:      { cron: { brandLabel: 'KoreCron', overline: 'Scheduled Chat Prompts', brandIcon: 'korecron' } },
  shellTabs:      [{ key: 'cron', label: 'Cron Prompts', href: '/ui' }],
});

function setTag(element, text, variant = 'dim') {
  element.textContent = text;
  element.className   = `kcui-tag kcui-tag--${variant}`;
}

function makeCell(text) {
  const cell = document.createElement('td');
  cell.textContent = text;
  return cell;
}

function addPromptRow(value = '') {
  const row       = document.createElement('div');
  const label     = document.createElement('label');
  const textarea  = document.createElement('textarea');
  const remove    = document.createElement('button');
  const controls  = document.createElement('div');
  const index     = promptList.children.length + 1;

  label.className   = 'kcui-form-label';
  label.textContent = `Prompt ${index}`;
  textarea.rows     = 3;
  textarea.value    = value;
  textarea.placeholder = 'Prompt to send after the previous prompt has completed.';
  textarea.dataset.prompt = 'true';
  remove.type        = 'button';
  remove.className   = 'kcui-tag kcui-tag--danger';
  remove.textContent = 'Remove';
  remove.addEventListener('click', () => {
    row.remove();
    renumberPrompts();
  });

  controls.append(remove);
  row.append(label, textarea, controls);
  promptList.append(row);
}

function renumberPrompts() {
  [...promptList.children].forEach((row, index) => {
    row.querySelector('label').textContent = `Prompt ${index + 1}`;
  });
}

function renderRows(items) {
  if (!items.length) {
    const row = document.createElement('tr');
    row.append(makeCell('No CronPrompts configured.'));
    row.firstChild.colSpan = 6;
    rows.replaceChildren(row);
    return;
  }
  rows.replaceChildren(...items.map((item) => {
    const row    = document.createElement('tr');
    const action = document.createElement('button');
    const cell   = document.createElement('td');
    action.type      = 'button';
    action.className = 'kcui-tag kcui-tag--dim';
    action.textContent = 'Run now';
    action.addEventListener('click', () => runNow(item.name, action));
    cell.append(action);
    row.append(
      makeCell(item.name),
      makeCell(item.chat_name),
      makeCell(item.schedule_text),
      makeCell(String(item.prompts.length)),
      makeCell(item.last_run || 'Never'),
      cell,
    );
    return row;
  }));
}

function renderTimeline(items) {
  if (!items.length) {
    const row = document.createElement('tr');
    row.append(makeCell('No enabled CronPrompts.'));
    row.firstChild.colSpan = 4;
    timelineRows.replaceChildren(row);
    return;
  }
  timelineRows.replaceChildren(...items.map((item) => {
    const row = document.createElement('tr');
    row.append(makeCell(item.next_fire), makeCell(item.name), makeCell(item.chat_name), makeCell(item.schedule_text));
    return row;
  }));
}

async function load() {
  const [cronResponse, timelineResponse] = await Promise.all([fetch('/api/cronprompts'), fetch('/api/timeline')]);
  const cronPayload     = await cronResponse.json();
  const timelinePayload = await timelineResponse.json();
  renderRows(cronPayload.cronprompts || []);
  renderTimeline(timelinePayload.items || []);
}

async function runNow(name, button) {
  button.disabled = true;
  button.textContent = 'Queued';
  try {
    const response = await fetch(`/api/cronprompts/${encodeURIComponent(name)}/run`, { method: 'POST' });
    if (!response.ok) throw new Error((await response.json()).detail || 'Unable to queue event.');
  } catch (error) {
    button.textContent = 'Failed';
    return;
  }
  window.setTimeout(load, 500);
}

addPrompt.addEventListener('click', () => addPromptRow());
nameInput.addEventListener('input', () => {
  if (!chatInput.value.trim()) chatInput.placeholder = nameInput.value.trim() || 'Named KoreChat';
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const prompts = [...promptList.querySelectorAll('[data-prompt]')].map((input) => input.value.trim()).filter(Boolean);
  saveButton.disabled = true;
  setTag(formStatus, 'Creating', 'warning');
  try {
    const response = await fetch('/api/cronprompts', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name:      nameInput.value.trim(),
        chat_name: chatInput.value.trim() || nameInput.value.trim(),
        schedule:  form.elements.schedule.value.trim(),
        prompts,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Unable to create timed event.');
    form.reset();
    promptList.replaceChildren();
    addPromptRow();
    setTag(formStatus, `Created ${result.name}`, 'success');
    await load();
  } catch (error) {
    setTag(formStatus, error.message || 'Request failed', 'danger');
  } finally {
    saveButton.disabled = false;
  }
});

addPromptRow();
load().catch((error) => setTag(formStatus, error.message || 'Unable to load', 'danger'));
window.setInterval(() => load().catch(() => {}), 15000);
