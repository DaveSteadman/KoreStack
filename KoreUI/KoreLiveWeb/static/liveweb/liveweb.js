import { initKoreLiveWebShell, resolveIcon, SUITE_ICONS } from '/ui-elements/assets/js/chrome.js';

const bootstrapNode = document.getElementById('klw-bootstrap');
const bootstrap     = bootstrapNode ? JSON.parse(bootstrapNode.textContent || '{}') : {};
const pollMs        = Number(bootstrap.pollMs || 2000);
const logList       = document.getElementById('klw-log-list');
const metricEntries = document.getElementById('metric-entries');
const metricStatus  = document.getElementById('metric-status');
const indicator     = document.getElementById('klw-refresh-indicator');

let lastTopEntryId  = 0;

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
  refreshEntries();
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
