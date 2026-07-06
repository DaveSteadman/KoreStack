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
  requestTarget: null,
  loadedContent: '',
  dirty: false,
};

const els = {
  target: document.getElementById('te-target'),
  open: document.getElementById('te-open'),
  save: document.getElementById('te-save'),
  status: document.getElementById('te-status'),
  editor: document.getElementById('te-editor'),
};

renderAppMenu({
  app: 'textedit',
  appLabel: 'TextEdit',
  titleId: 'te-title',
  dirtyId: 'te-dirty',
  initialTitle: 'TextEdit',
  menus: [{ id: 'file', label: 'File', items: [
    { action: 'refresh-target', label: 'Refresh From Disk' },
    { action: 'save-target', label: 'Save' },
  ] }],
});

initAppMenuEvents(action => {
  if (action === 'refresh-target') refreshTarget();
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

els.open.addEventListener('click', refreshTarget);
els.save.addEventListener('click', saveTarget);
els.editor.addEventListener('input', () => {
  _draftSave(els.editor.value);
  _setDirty(Boolean(state.source) && els.editor.value !== state.loadedContent);
});

function setStatus(msg, isError = false) {
  els.status.textContent = msg;
  els.status.style.color = isError ? '#ff8080' : '#95a6c8';
}

function _displayTarget(meta) {
  return meta.full_path || meta.path || meta.name || 'Untitled';
}

function _setDirty(isDirty) {
  state.dirty = isDirty;
  els.save.classList.toggle('btn-warning', isDirty);
  els.save.classList.toggle('te-btn-success', !isDirty);
}

function _setSaveBusy(isBusy) {
  els.save.disabled = !state.source || isBusy;
  els.open.disabled = !state.requestTarget || isBusy;
}

function applyLoaded(meta) {
  state.source = meta.source;
  state.fileId = meta.file_id ?? null;
  state.path = meta.path ?? null;
  state.revision = meta.revision == null ? null : String(meta.revision);
  state.requestTarget = state.fileId != null ? { file_id: state.fileId } : (state.path ? { path: state.path } : state.requestTarget);

  els.editor.value = meta.content || '';
  els.target.value = _displayTarget(meta);
  state.loadedContent = els.editor.value;
  _setDirty(false);
  _setSaveBusy(false);

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

async function refreshTarget() {
  const target = state.requestTarget;
  if (!target) {
    setStatus('No file is loaded yet.', true);
    return;
  }

  const qs = new URLSearchParams();
  if (target.file_id != null) qs.set('file_id', String(target.file_id));
  if (target.path) qs.set('path', target.path);

  _setSaveBusy(true);
  setStatus('Refreshing...');
  try {
    const res = await fetchWithAuth(`/api/textedit/open?${qs.toString()}`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    draft.clear();
    applyLoaded(body);
  } catch (err) {
    setStatus(`Refresh failed: ${err.message}`, true);
  } finally {
    _setSaveBusy(false);
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
  } else {
    payload.path = state.path;
  }

  _setSaveBusy(true);
  setStatus('Saving...');
  try {
    const res = await fetchWithAuth('/api/textedit/save', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    if (body.revision != null) {
      state.revision = String(body.revision);
    }
    draft.clear();
    state.loadedContent = els.editor.value;
    _setDirty(false);
    setStatus('Saved.');
  } catch (err) {
    setStatus(`Save failed: ${err.message}`, true);
  } finally {
    _setSaveBusy(false);
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
    state.requestTarget = { file_id: parseInt(id, 10) };
    els.target.value = fileName || 'Loading...';
  } else if (path) {
    state.requestTarget = { path };
    els.target.value = path;
  } else if (fileName) {
    els.target.value = fileName;
  }

  _setSaveBusy(false);

  if (state.requestTarget) {
    refreshTarget();
  }
})();
