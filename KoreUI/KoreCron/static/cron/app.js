import { initServiceShell } from '/ui-elements/assets/js/serviceShell.js';

const workspace    = document.querySelector('.kcui-workspace');
const form         = document.querySelector('#cronprompt-form');
const nameInput    = document.querySelector('#cron-name');
const chatInput    = document.querySelector('#chat-name');
const clearWorkingData = document.querySelector('#clear-working-data');
const promptList   = document.querySelector('#prompt-list');
const addPrompt    = document.querySelector('#add-prompt');
const cancelEdit   = document.querySelector('#cancel-edit');
const saveButton   = document.querySelector('#save-cronprompt');
const agentResumeButton = document.querySelector('#agent-resume-btn');
const formStatus   = document.querySelector('#form-status');
const listStatus   = document.querySelector('#list-status');
const createButton = document.querySelector('#create-cronprompt');
const editorEyebrow = document.querySelector('#editor-eyebrow');
const editorBlurb  = document.querySelector('#editor-blurb');
const rows         = document.querySelector('#cron-rows');
let editingName    = null;
let cronPrompts    = [];
let selectionReady = false;
let editorDirty    = false;

workspace?.classList.add('kcui-workspace--two-columns', 'kcui-workspace--stack-sm');
initServiceShell({
  currentService: 'korecron',
  urls:           window.__koreSuiteUrls || {},
  path:           '/ui',
  section:        'cron',
  shellMeta:      { cron: { brandLabel: 'KoreCron', overline: 'Scheduled Chat Prompts', brandIcon: 'korecron' } },
  shellTabs:      [{ key: 'cron', label: 'Cron Prompts', href: '/ui' }],
});

const cronPromptRowStyle = document.createElement('style');
cronPromptRowStyle.textContent = `
  .cronprompt-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  .cronprompt-list {
    display: grid;
    gap: 10px;
  }
  .cronprompt-chat-row {
    position: relative;
    display: flex;
    align-items: center;
  }
  .cronprompt-chat-row > input {
    flex: 1 1 auto;
    min-width: 0;
    padding-right: 8.75rem;
  }
  .cronprompt-chat-row > button {
    position: absolute;
    right: 6px;
    top: 50%;
    transform: translateY(-50%);
    z-index: 1;
  }
  .cronprompt-chat-row > button:disabled {
    opacity: 0.52;
  }
  .cronprompt-list__empty {
    padding: 14px 16px;
    border: 1px dashed var(--border, rgba(255, 255, 255, 0.16));
    border-radius: var(--kcui-radius-md, 2px);
    color: var(--text-muted, rgba(255, 255, 255, 0.72));
  }
  .cronprompt-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 14px;
    border: 1px solid var(--border, rgba(255, 255, 255, 0.16));
    border-radius: var(--kcui-radius-md, 2px);
    background: rgba(255, 255, 255, 0.02);
    cursor: pointer;
    transition: background 120ms ease, border-color 120ms ease, box-shadow 120ms ease;
  }
  .cronprompt-row:hover {
    background: rgba(96, 165, 250, 0.08);
  }
  .cronprompt-row.is-selected {
    border-color: rgba(96, 165, 250, 0.65);
    background: rgba(96, 165, 250, 0.14);
    box-shadow: inset 0 0 0 1px rgba(96, 165, 250, 0.22);
  }
  .cronprompt-row:focus-visible {
    outline: 2px solid rgba(96, 165, 250, 0.85);
    outline-offset: 2px;
  }
  .cronprompt-row__summary {
    min-width: 0;
    display: grid;
    gap: 6px;
    flex: 1 1 auto;
  }
  .cronprompt-row__titleline,
  .cronprompt-row__meta {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    flex-wrap: wrap;
  }
  .cronprompt-row__title {
    font-weight: 600;
  }
  .cronprompt-row__meta {
    color: var(--text-muted, rgba(255, 255, 255, 0.72));
    font-size: 0.92rem;
  }
  .cronprompt-row__meta span,
  .cronprompt-row__title {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .cronprompt-row__actions {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    flex: 0 0 auto;
  }
  .cronprompt-row__delete {
    min-width: 2.1rem;
    text-align: center;
  }
  textarea.cronprompt-prompt {
    box-sizing: border-box;
    display: block;
    min-height: 2.35rem;
    overflow-y: hidden;
    resize: vertical;
    width: 100%;
  }
`;
document.head.append(cronPromptRowStyle);

function setTag(element, text, variant = 'dim') {
  element.textContent = text;
  element.className   = `kcui-tag kcui-tag--${variant}`;
}

function updateEditorChrome() {
  const isEditing = Boolean(editingName);
  const hasChatName = Boolean(chatInput.value.trim());
  editorEyebrow.textContent = isEditing ? 'Selected Timed Event' : 'New Timed Event';
  editorBlurb.textContent = isEditing
    ? `Editing ${editingName}. Changes on the right update the selected CronPrompt and its prompt sequence.`
    : 'Choose a timed event on the left or create a new one, then define its destination chat, schedule, and ordered prompts.';
  saveButton.textContent = isEditing ? 'Save changes' : 'Create timed event';
  cancelEdit.hidden = !isEditing;
  listStatus.textContent = isEditing ? `Selected: ${editingName}` : 'Select a timed event';
  agentResumeButton.disabled = !hasChatName;
}


function fitPromptTextarea(textarea) {
  textarea.style.height = 'auto';
  textarea.style.height = `${textarea.scrollHeight}px`;
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
  textarea.className = 'cronprompt-prompt';
  textarea.rows     = 1;
  textarea.value    = value;
  textarea.placeholder = 'Prompt to send after the previous prompt has completed.';
  textarea.dataset.prompt = 'true';
  textarea.addEventListener('input', () => fitPromptTextarea(textarea));
  remove.type        = 'button';
  remove.className   = 'kcui-tag kcui-tag--danger';
  remove.textContent = 'Remove';
  remove.addEventListener('click', () => {
    row.remove();
    renumberPrompts();
    editorDirty = true;
  });

  controls.append(remove);
  row.append(label, textarea, controls);
  promptList.append(row);
  fitPromptTextarea(textarea);
}

function renumberPrompts() {
  [...promptList.children].forEach((row, index) => {
    row.querySelector('label').textContent = `Prompt ${index + 1}`;
  });
}

function renderRows(items) {
  if (!items.length) {
    const empty = document.createElement('div');
    empty.className = 'cronprompt-list__empty';
    empty.textContent = 'No CronPrompts configured.';
    rows.replaceChildren(empty);
    return;
  }
  rows.replaceChildren(...items.map((item) => {
    const row       = document.createElement('div');
    const summary   = document.createElement('div');
    const titleLine = document.createElement('div');
    const title     = document.createElement('span');
    const schedule  = document.createElement('span');
    const meta      = document.createElement('div');
    const chat      = document.createElement('span');
    const promptCount = document.createElement('span');
    const lastRun   = document.createElement('span');
    const actions   = document.createElement('div');
    const runAction  = document.createElement('button');
    const deleteAction = document.createElement('button');
    row.className   = 'cronprompt-row';
    if (item.name === editingName) row.classList.add('is-selected');
    row.title       = `Select ${item.name}`;
    row.tabIndex    = 0;
    row.setAttribute('role', 'listitem');
    row.addEventListener('click', () => startEdit(item));
    row.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        startEdit(item);
      }
    });
    summary.className = 'cronprompt-row__summary';
    titleLine.className = 'cronprompt-row__titleline';
    title.className = 'cronprompt-row__title';
    title.textContent = item.name;
    schedule.className = 'kcui-tag kcui-tag--dim';
    schedule.textContent = item.schedule_text;
    meta.className = 'cronprompt-row__meta';
    chat.textContent = `Chat: ${item.chat_name}`;
    promptCount.textContent = `${item.prompts.length} prompt${item.prompts.length === 1 ? '' : 's'}`;
    lastRun.textContent = `Last run: ${item.last_run || 'Never'}`;
    titleLine.append(title, schedule);
    meta.append(chat, promptCount, lastRun);
    summary.append(titleLine, meta);
    actions.className = 'cronprompt-row__actions';
    runAction.type      = 'button';
    runAction.className = 'kcui-tag kcui-tag--dim';
    runAction.textContent = 'Run';
    runAction.addEventListener('click', (event) => {
      event.stopPropagation();
      runNow(item.name, runAction);
    });
    deleteAction.type      = 'button';
    deleteAction.className = 'kcui-tag kcui-tag--danger';
    deleteAction.textContent = 'X';
    deleteAction.title = `Delete ${item.name}`;
    deleteAction.setAttribute('aria-label', `Delete ${item.name}`);
    deleteAction.classList.add('cronprompt-row__delete');
    deleteAction.addEventListener('click', (event) => {
      event.stopPropagation();
      deleteCronPrompt(item.name);
    });
    actions.append(runAction, deleteAction);
    row.append(summary, actions);
    return row;
  }));
}

function resetEditor({ preserveStatus = false } = {}) {
  editingName = null;
  editorDirty = false;
  form.reset();
  promptList.replaceChildren();
  addPromptRow();
  updateEditorChrome();
  renderRows(cronPrompts);
  if (!preserveStatus) setTag(formStatus, 'Ready', 'dim');
}

function startEdit(item, { scroll = true, updateStatus = true } = {}) {
  editingName       = item.name;
  editorDirty       = false;
  nameInput.value   = item.name;
  chatInput.value   = item.chat_name;
  clearWorkingData.checked = item.clear_working_data !== false;
  form.elements.schedule.value = item.schedule.type === 'daily'
    ? item.schedule.time
    : String(item.schedule.minutes);
  promptList.replaceChildren();
  item.prompts.forEach((prompt) => addPromptRow(prompt.prompt || ''));
  updateEditorChrome();
  renderRows(cronPrompts);
  if (updateStatus) setTag(formStatus, `Editing ${item.name}`, 'warning');
  if (scroll) form.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function load() {
  const cronResponse = await fetch('/api/cronprompts');
  const cronPayload     = await cronResponse.json();
  cronPrompts = cronPayload.cronprompts || [];
  if (editingName) {
    const selected = cronPrompts.find((item) => item.name === editingName);
    if (editorDirty) renderRows(cronPrompts);
    else if (selected) startEdit(selected, { scroll: false, updateStatus: false });
    else resetEditor({ preserveStatus: true });
  } else if (!selectionReady && cronPrompts.length) {
    selectionReady = true;
    startEdit(cronPrompts[0], { scroll: false, updateStatus: false });
  } else {
    renderRows(cronPrompts);
  }
  updateEditorChrome();
}

async function runNow(name, button) {
  button.disabled = true;
  button.textContent = 'Queued';
  try {
    const response = await fetch(`/api/cronprompts/${encodeURIComponent(name)}/run`, { method: 'POST' });
    if (!response.ok) throw new Error((await response.json()).detail || 'Unable to queue event.');
    setTag(formStatus, `Queued ${name}`, 'success');
  } catch (error) {
    button.textContent = 'Failed';
    return;
  }
  window.setTimeout(load, 500);
}

async function deleteCronPrompt(name) {
  if (!window.confirm(`Delete CronPrompt "${name}"?`)) return;
  try {
    const response = await fetch(`/api/cronprompts/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (!response.ok) throw new Error((await response.json()).detail || 'Unable to delete event.');
    if (editingName === name) resetEditor({ preserveStatus: true });
    setTag(formStatus, `Deleted ${name}`, 'success');
    await load();
  } catch (error) {
    setTag(formStatus, error.message || 'Unable to delete event.', 'danger');
  }
}

async function agentResume() {
  if (!editingName) {
    window.alert('Select a CronPrompt first.');
    return;
  }

  agentResumeButton.disabled = true;
  try {
    const response = await fetch(`/api/cronprompts/${encodeURIComponent(editingName)}/agent-resume`, {
      method: 'POST',
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || 'Agent resume failed.');
    }
    window.location.href = result.redirect_url || result.agent_url;
  } catch (error) {
    setTag(formStatus, error.message || 'Agent resume failed.', 'danger');
  } finally {
    updateEditorChrome();
  }
}

addPrompt.addEventListener('click', () => {
  addPromptRow();
  editorDirty = true;
});
cancelEdit.addEventListener('click', () => resetEditor());
createButton.addEventListener('click', () => {
  selectionReady = true;
  resetEditor();
  nameInput.focus();
});
agentResumeButton.addEventListener('click', agentResume);
nameInput.addEventListener('input', () => {
  if (!chatInput.value.trim()) chatInput.placeholder = nameInput.value.trim() || 'Named KoreChat';
});
chatInput.addEventListener('input', updateEditorChrome);
form.addEventListener('input', () => { editorDirty = true; });

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const prompts = [...promptList.querySelectorAll('[data-prompt]')].map((input) => input.value.trim()).filter(Boolean);
  saveButton.disabled = true;
  setTag(formStatus, editingName ? 'Saving' : 'Creating', 'warning');
  try {
    const response = await fetch(editingName ? `/api/cronprompts/${encodeURIComponent(editingName)}` : '/api/cronprompts', {
      method:  editingName ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name:      nameInput.value.trim(),
        chat_name: chatInput.value.trim() || nameInput.value.trim(),
        clear_working_data: clearWorkingData.checked,
        schedule:  form.elements.schedule.value.trim(),
        prompts,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Unable to create timed event.');
    const action = editingName ? 'Saved' : 'Created';
    editingName = result.name;
    selectionReady = true;
    await load();
    setTag(formStatus, `${action} ${result.name}`, 'success');
  } catch (error) {
    setTag(formStatus, error.message || 'Request failed', 'danger');
  } finally {
    saveButton.disabled = false;
  }
});

addPromptRow();
updateEditorChrome();
load().catch((error) => setTag(formStatus, error.message || 'Unable to load', 'danger'));
window.setInterval(() => load().catch(() => {}), 15000);
