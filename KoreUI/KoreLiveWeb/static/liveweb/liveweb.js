import { initKoreLiveWebShell, resolveIcon, SUITE_ICONS } from '/ui-elements/assets/js/chrome.js';

const bootstrapNode = document.getElementById('klw-bootstrap');
const bootstrap     = bootstrapNode ? JSON.parse(bootstrapNode.textContent || '{}') : {};
const pollMs        = Number(bootstrap.pollMs || 2000);
const logList       = document.getElementById('klw-log-list');
const metricEntries = document.getElementById('metric-entries');
const metricStatus  = document.getElementById('metric-status');
const indicator     = document.getElementById('klw-refresh-indicator');
const settingsForm  = document.getElementById('klw-settings-form');
const settingsState = document.getElementById('klw-settings-state');
const settingsMeta  = document.getElementById('klw-settings-meta');
const ddgEnabled    = document.getElementById('klw-ddg-enabled');
const ollamaEnabled = document.getElementById('klw-ollama-enabled');
const preferred     = document.getElementById('klw-preferred-provider');
const apiKeyInput   = document.getElementById('klw-ollama-api-key');
const clearKeyBtn   = document.getElementById('klw-settings-clear');
const apiKeyState   = document.getElementById('klw-api-key-state');
const saveBtn       = document.getElementById('klw-settings-save');

let lastTopEntryId  = 0;
let searchSettings  = bootstrap.searchSettings || null;
let settingsDirty   = false;

function setSettingsState(text) {
  if (settingsState) settingsState.textContent = text;
}

function installIcons() {
  for (const node of document.querySelectorAll('[data-icon-key]')) {
    const key = node.getAttribute('data-icon-key');
    if (!key) continue;
    node.innerHTML = resolveIcon(SUITE_ICONS, key, 18);
  }
}

function statusClass(status) {
  const text = String(status || '').toLowerCase();
  if (text.startsWith('http-2') || text === 'requested') return 'is-ok';
  if (text === 'cache-hit') return 'is-cache';
  if (text.startsWith('http-3') || text.startsWith('http-4')) return 'is-warn';
  if (text.startsWith('http-5') || text.includes('error')) return 'is-error';
  return '';
}

function kindLabel(entry) {
  if (entry.kind === 'tool' && entry.tool_name) return entry.tool_name;
  return entry.kind || 'event';
}

function detailLabel(entry) {
  const parts = [];
  if (entry.message) parts.push(entry.message);
  if (entry.final_url && entry.final_url !== entry.target) parts.push(`final ${entry.final_url}`);
  return parts.join(' | ') || '-';
}

function rowCell(label, className, text, useCode = false) {
  const value = text || '-';
  const body  = useCode ? `<code>${escapeHtml(value)}</code>` : escapeHtml(value);
  return `<div class="${className}" data-label="${label}">${body}</div>`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function renderEntries(entries) {
  if (!logList) return;

  metricEntries.textContent = String(entries.length);
  metricStatus.textContent  = entries[0]?.status || 'idle';

  if (!entries.length) {
    logList.innerHTML = '<div class="klw-log-empty">No requests observed yet.</div>';
    return;
  }

  const nextTopId = Number(entries[0]?.id || 0);
  const changed   = nextTopEntryId !== nextTopId;
  nextTopEntryId  = nextTopId;

  logList.innerHTML = entries.map((entry) => `
    <div class="klw-log-row">
      ${rowCell('Time',   'klw-log-time',   entry.ts_label)}
      ${rowCell('Kind',   'klw-log-kind',   kindLabel(entry))}
      ${rowCell('Target', 'klw-log-target', entry.target, true)}
      ${rowCell('Status', `klw-log-status ${statusClass(entry.status)}`.trim(), entry.status)}
      ${rowCell('Detail', 'klw-log-detail', detailLabel(entry))}
    </div>
  `).join('');

  if (changed) {
    logList.scrollTop = 0;
  }
}

function describeSettings(settings) {
  if (!settings) return 'Settings unavailable.';
  const enabled = [];
  if (settings.ddg_enabled) enabled.push('DDG');
  if (settings.ollama_enabled) enabled.push('Ollama');
  const enabledLabel = enabled.length ? enabled.join(', ') : 'none';
  const keyLabel     = settings.ollama_has_api_key ? 'stored' : 'missing';
  return `Active ${settings.active_label}. Enabled: ${enabledLabel}. Ollama key: ${keyLabel}.`;
}

function renderSearchSettings(settings) {
  searchSettings = settings || null;
  if (!settingsForm || !searchSettings) return;

  ddgEnabled.checked    = Boolean(searchSettings.ddg_enabled);
  ollamaEnabled.checked = Boolean(searchSettings.ollama_enabled);
  preferred.value       = searchSettings.preferred_provider || 'ddg';
  apiKeyInput.value     = '';
  settingsDirty         = false;

  if (settingsMeta)  settingsMeta.textContent  = describeSettings(searchSettings);
  setSettingsState('saved');
  if (apiKeyState)   apiKeyState.textContent   = searchSettings.ollama_has_api_key ? 'key stored' : 'key missing';

  const hasKey = Boolean(searchSettings.ollama_has_api_key);
  apiKeyInput.placeholder = hasKey
    ? 'Stored key is hidden. Paste a new key to replace it.'
    : 'Paste Ollama API key';
}

function currentSettingsDraft() {
  return {
    preferred_provider: preferred?.value || 'ddg',
    ddg_enabled:        Boolean(ddgEnabled?.checked),
    ollama_enabled:     Boolean(ollamaEnabled?.checked),
    ollama_api_key:     String(apiKeyInput?.value || '').trim(),
  };
}

function isSettingsDirty() {
  if (!searchSettings) return false;
  const draft = currentSettingsDraft();
  if (draft.preferred_provider !== (searchSettings.preferred_provider || 'ddg')) return true;
  if (draft.ddg_enabled        !== Boolean(searchSettings.ddg_enabled))          return true;
  if (draft.ollama_enabled     !== Boolean(searchSettings.ollama_enabled))       return true;
  if (draft.ollama_api_key) return true;
  return false;
}

function syncDirtyState() {
  settingsDirty = isSettingsDirty();
  setSettingsState(settingsDirty ? 'unsaved' : 'saved');
}

function markSettingsUnsaved() {
  settingsDirty = true;
  setSettingsState('unsaved');
}

window.__klwMarkUnsaved = markSettingsUnsaved;

async function refreshSearchSettings() {
  try {
    const response = await fetch('/api/settings/search-providers', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderSearchSettings(await response.json());
  } catch (error) {
    if (settingsMeta) settingsMeta.textContent = `Settings load failed: ${error.message}`;
    setSettingsState('error');
  }
}

async function saveSearchSettings(clearApiKey = false) {
  if (!settingsForm) return;

  if (!ddgEnabled.checked && !ollamaEnabled.checked) {
    if (settingsMeta) settingsMeta.textContent = 'At least one provider must remain enabled.';
    if (settingsState) settingsState.textContent = 'blocked';
    return;
  }

  settingsDirty = false;
  if (settingsMeta)  settingsMeta.textContent  = clearApiKey ? 'Clearing stored API key...' : 'Saving search settings...';

  const payload = {
    preferred_provider: preferred.value || 'ddg',
    ddg_enabled:        ddgEnabled.checked,
    ollama_enabled:     ollamaEnabled.checked,
    clear_ollama_api_key: clearApiKey,
  };

  const nextKey = String(apiKeyInput.value || '').trim();
  if (nextKey && !clearApiKey) {
    payload.ollama_api_key = nextKey;
  }

  try {
    const response = await fetch('/api/settings/search-providers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
    renderSearchSettings(result);
    if (settingsMeta) settingsMeta.textContent = clearApiKey ? 'Stored Ollama API key cleared from config/korestack_config.json.' : 'Search settings saved to config/korestack_config.json.';
    setSettingsState('saved');
  } catch (error) {
    if (settingsMeta) settingsMeta.textContent = `Save failed: ${error.message}`;
    setSettingsState('unsaved');
  }
}

async function refreshEntries() {
  if (indicator) indicator.textContent = 'polling';
  try {
    const response = await fetch('/api/activity?limit=200', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    renderEntries(Array.isArray(payload.entries) ? payload.entries : []);
    if (indicator) indicator.textContent = 'live';
  } catch (error) {
    if (indicator) indicator.textContent = 'stalled';
    if (metricStatus) metricStatus.textContent = 'fetch-error';
  }
}

function startPolling() {
  renderEntries(Array.isArray(bootstrap.initialEntries) ? bootstrap.initialEntries : []);
  renderSearchSettings(searchSettings);
  refreshEntries();
  refreshSearchSettings();
  window.setInterval(refreshEntries, pollMs);
}

initKoreLiveWebShell({
  path: window.location.pathname,
  urls: window.__koreSuiteUrls || {},
  appBarOptions: {
    chips: [
      { label: 'TOOLS',  value: String((bootstrap.toolNames || []).length), tone: 'info'    },
      { label: 'STATUS', value: 'LIVE',                                tone: 'success' },
    ],
    note: {
      id:    'klw-appbar-note',
      text:  'Web activity observer',
      title: 'Live monitor for MCP web requests',
    },
    actions: [
      {
        kind:      'tag',
        label:     'Endpoint Explorer',
        className: 'kcui-tag kcui-tag--dim',
        type:      'button',
        id:        'klw-endpoints-btn',
      },
    ],
  },
});

installIcons();
startPolling();

const endpointsButton = document.getElementById('klw-endpoints-btn');
if (endpointsButton) {
  endpointsButton.addEventListener('click', () => {
    window.location.href = bootstrap.endpointExplorer || '/endpoints';
  });
}

if (settingsForm) {
  settingsForm.addEventListener('submit', (event) => {
    event.preventDefault();
    return false;
  });
}

for (const node of [ddgEnabled, ollamaEnabled, preferred, apiKeyInput]) {
  if (!node) continue;
  node.addEventListener('input',  markSettingsUnsaved);
  node.addEventListener('change', markSettingsUnsaved);
  node.addEventListener('click',  markSettingsUnsaved);
}

if (saveBtn) {
  saveBtn.addEventListener('click', async () => {
    await saveSearchSettings(false);
  });
}

if (clearKeyBtn) {
  clearKeyBtn.addEventListener('click', async () => {
    apiKeyInput.value = '';
    syncDirtyState();
    await saveSearchSettings(true);
  });
}
