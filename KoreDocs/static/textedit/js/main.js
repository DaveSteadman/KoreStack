import { initTopbar, initAppBar, initAppTabs, renderAppMenu, initAppMenuEvents, trackAppTab } from '/ui-elements/assets/js/chrome.js';
import { fetchWithAuth } from '/static/shared/js/auth.js';
import * as draft from '/static/shared/js/draft.js';

const _draftSave = draft.makeSaver();
let _draftRestored = false;

const state = {
  source: null,
  fileId: null,
  path: null,
  revision: null,
};

const els = {
  target: document.getElementById('te-target'),
  open: document.getElementById('te-open'),
  save: document.getElementById('te-save'),
  status: document.getElementById('te-status'),
  editor: document.getElementById('te-editor'),
  source: document.getElementById('te-source'),
  name: document.getElementById('te-name'),
  size: document.getElementById('te-size'),
  encoding: document.getElementById('te-encoding'),
};

renderAppMenu({
  app: 'textedit',
  appLabel: 'TextEdit',
  titleId: 'te-title',
  dirtyId: 'te-dirty',
  initialTitle: 'TextEdit',
  menus: [{ id: 'file', label: 'File', items: [
    { action: 'open-target', label: 'Open Target' },
    { action: 'save-target', label: 'Save' },
  ] }],
});

initAppMenuEvents(action => {
  if (action === 'open-target') openTarget();
  if (action === 'save-target') saveTarget();
});

initTopbar({ currentService: 'koredocs', urls: window.__koreSuiteUrls || {} });
initAppBar({
  mountId: 'tab-bar',
  currentService: 'koredocs',
  overline: 'Plain Text Inspector',
  brandLabel: 'TextEdit',
  brandIcon: 'textedit',
  editorTabsSlot: 'koredocs-tabs',
});
initAppTabs('textedit', {
  mountId: 'koredocs-tabs',
  renderBrand: false,
  typeUrl: { textedit: '/textedit' },
  titleNormalizer(name) {
    if (name && name.startsWith('__new_')) return 'Untitled';
    return String(name || '').replace(/\.[^.]+$/, '');
  },
});

els.open.addEventListener('click', openTarget);
els.save.addEventListener('click', saveTarget);
els.editor.addEventListener('input', () => {
  _draftSave(els.editor.value);
});
els.target.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    openTarget();
  }
});

function setStatus(msg, isError = false) {
  els.status.textContent = msg;
  els.status.style.color = isError ? '#ff8080' : '#95a6c8';
}

function parseTargetInput() {
  const raw = (els.target.value || '').trim();
  if (!raw) return null;

  if (/^\d+$/.test(raw)) return { file_id: parseInt(raw, 10) };

  if (raw.startsWith('id=')) {
    const id = parseInt(raw.slice(3).trim(), 10);
    if (!Number.isNaN(id)) return { file_id: id };
  }

  if (raw.startsWith('path=')) {
    return { path: raw.slice(5).trim() };
  }

  return { path: raw };
}

function applyLoaded(meta) {
  state.source = meta.source;
  state.fileId = meta.file_id ?? null;
  state.path = meta.path ?? null;
  state.revision = meta.revision ?? null;

  els.editor.value = meta.content || '';
  els.source.textContent = meta.source || '-';
  els.name.textContent = meta.name || meta.path || meta.full_path || '-';
  els.size.textContent = typeof meta.byte_length === 'number' ? `${meta.byte_length.toLocaleString()} bytes` : '-';
  els.encoding.textContent = meta.encoding || 'utf-8';
  els.save.disabled = false;

  if (meta.truncated) {
    setStatus('Loaded preview (file exceeded size limit and was truncated).', true);
  } else {
    setStatus('Loaded.');
  }

  if (!_draftRestored) {
    const saved = draft.load();
    if (saved !== null) {
      els.editor.value = saved;
      setStatus('Loaded (restored unsaved draft).');
    }
    _draftRestored = true;
  }

  const tabName = meta.name || meta.path || meta.full_path || 'TextEdit';
  if (tabName) {
    trackAppTab(tabName, 'textedit', meta.file_id ?? null);
  }
}

async function openTarget() {
  const target = parseTargetInput();
  if (!target) {
    setStatus('Enter a file id or path first.', true);
    return;
  }

  const qs = new URLSearchParams();
  if (target.file_id != null) qs.set('file_id', String(target.file_id));
  if (target.path) qs.set('path', target.path);

  els.open.disabled = true;
  setStatus('Opening...');
  try {
    const res = await fetchWithAuth(`/api/textedit/open?${qs.toString()}`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    applyLoaded(body);
  } catch (err) {
    setStatus(`Open failed: ${err.message}`, true);
  } finally {
    els.open.disabled = false;
  }
}

async function saveTarget() {
  if (!state.source) {
    setStatus('Open something first.', true);
    return;
  }

  const payload = { content: els.editor.value };
  if (state.source === 'korefile') {
    payload.file_id = state.fileId;
    payload.expected_revision = state.revision;
  } else {
    payload.path = state.path;
  }

  els.save.disabled = true;
  setStatus('Saving...');
  try {
    const res = await fetchWithAuth('/api/textedit/save', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    if (typeof body.revision === 'number') {
      state.revision = body.revision;
    }
    draft.clear();
    setStatus('Saved.');
  } catch (err) {
    setStatus(`Save failed: ${err.message}`, true);
  } finally {
    els.save.disabled = false;
  }
}

function _persistDraftNow() {
  if (!state.source) return;
  draft.save(els.editor.value || '');
}

document.addEventListener('kcui:before-navigate', _persistDraftNow);
document.addEventListener('kd:before-navigate', _persistDraftNow);
window.addEventListener('pagehide', _persistDraftNow);

(function initFromQuery() {
  const q = new URLSearchParams(location.search);
  const id = q.get('id');
  const path = q.get('path');
  const fileName = q.get('file');

  if (id) {
    els.target.value = `id=${id}`;
  } else if (path) {
    els.target.value = `path=${path}`;
  } else if (fileName) {
    els.target.value = fileName;
  }

  if (els.target.value) {
    openTarget();
  }
})();
