/**
 * main.js — KoreFile explorer entry point.
 *
 * Wires together: appMenu, tabs, tree panel, file list, toolbar, breadcrumb,
 * search box, and import button.
 */

import * as topbar   from '/static/commonui/js/topbar.js';
import { renderAppMenu, initAppMenuEvents } from '/static/commonui/js/appMenu.js';
import * as tree     from './tree.js';
import * as filelist from './filelist.js';
import * as api      from './api.js';

// ── App menu ────────────────────────────────────────────────────

renderAppMenu({
  app: 'koredoc',   // reuse doc icon for now (generic doc)
  appLabel: 'KoreFile',
  titleId: 'kf-title',
  dirtyId: 'kf-dirty',
  initialTitle: 'File Explorer',
  menus: [
    {
      id: 'file',
      label: 'File',
      items: [
        { action: 'new-file',   label: 'New File…' },
        { action: 'new-folder', label: 'New Folder…' },
        { separator: true },
        { action: 'import-fs',  label: 'Import from File Storage' },
      ],
    },
  ],
});

initAppMenuEvents(action => {
  if (action === 'new-file')   _newFile();
  if (action === 'new-folder') document.getElementById('btn-new-folder').click();
  if (action === 'import-fs')  _importFs();
});

// ── Tab bar ─────────────────────────────────────────────────────

topbar.initSuiteTopbar({ currentService: 'koredocs' });
topbar.init('koredoc');

// ── State ───────────────────────────────────────────────────────

let _currentFolder = null;   // folder object { id, path, name, … }

// ── Initial load ────────────────────────────────────────────────

(async () => {
  const folders = await tree.refresh();
  let target = null;
  try {
    const saved = JSON.parse(localStorage.getItem('koredocs:kf:scroll') || 'null');
    if (saved && saved.folderId) {
      target = folders.find(f => f.id === saved.folderId) || null;
    }
  } catch { /* ignore */ }
  if (!target) target = folders.find(f => f.path === '/') || folders[0];
  if (target) _selectFolder(target);
})();

// ── Tree selection ───────────────────────────────────────────────

document.addEventListener('kf:select', e => {
  _selectFolder(e.detail.folder);
});

document.addEventListener('kf:refresh', async () => {
  await tree.refresh();
  if (_currentFolder) {
    const updated = tree.getFolders().find(f => f.id === _currentFolder.id);
    if (updated) _selectFolder(updated);
  }
});

function _selectFolder(folder) {
  _currentFolder = folder;
  tree.select(folder.id);
  _renderBreadcrumb(folder);
  filelist.loadFolder(folder.id);
}

// ── Breadcrumb ───────────────────────────────────────────────────

function _renderBreadcrumb(folder) {
  const bc = document.getElementById('kf-breadcrumb');
  const folders = tree.getFolders();
  const segments = _pathSegments(folder, folders);
  bc.innerHTML = segments.map((seg, i) => {
    const isLast = i === segments.length - 1;
    return isLast
      ? `<span class="bc-seg">${_esc(seg.label)}</span>`
      : `<span class="bc-seg" data-id="${seg.id}">${_esc(seg.label)}</span>
         <span class="bc-sep">/</span>`;
  }).join('');

  bc.querySelectorAll('.bc-seg[data-id]').forEach(el => {
    el.addEventListener('click', () => {
      const f = folders.find(x => x.id === parseInt(el.dataset.id, 10));
      if (f) _selectFolder(f);
    });
  });
}

function _pathSegments(folder, folders) {
  const byId = {};
  for (const f of folders) byId[f.id] = f;
  const segs = [];
  let cur = folder;
  while (cur) {
    segs.unshift({ id: cur.id, label: cur.path === '/' ? 'Root' : cur.name });
    cur = cur.parent_id != null ? byId[cur.parent_id] : null;
  }
  return segs;
}

// ── Toolbar ──────────────────────────────────────────────────────

document.getElementById('btn-new-file').addEventListener('click', _newFile);
document.getElementById('btn-import-fs').addEventListener('click', _importFs);

async function _newFile() {
  const folderId = _currentFolder?.id ?? 1;
  await filelist.createNewFile(folderId);
}

async function _importFs() {
  const btn = document.getElementById('btn-import-fs');
  btn.disabled = true;
  btn.textContent = 'Importing…';
  try {
    const result = await api.importFs();
    alert(`Import complete.\nImported: ${result.imported}  Skipped: ${result.skipped}  Errors: ${result.errors}`);
    if (_currentFolder) await filelist.loadFolder(_currentFolder.id);
    await tree.refresh();
  } catch (err) {
    alert('Import failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg viewBox="0 0 20 20" fill="none" width="14" height="14">
      <path d="M10 3v10m0 0-3-3m3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M3 14v2a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    </svg> Import FS Files`;
  }
}

// ── Search ───────────────────────────────────────────────────────

let _searchTimer = null;
const _searchInput = document.getElementById('kf-search');

_searchInput.addEventListener('input', () => {
  clearTimeout(_searchTimer);
  const q = _searchInput.value.trim();
  if (!q) {
    if (_currentFolder) filelist.loadFolder(_currentFolder.id);
    return;
  }
  _searchTimer = setTimeout(() => filelist.loadSearch(q), 280);
});

_searchInput.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    _searchInput.value = '';
    if (_currentFolder) filelist.loadFolder(_currentFolder.id);
  }
});

// ── Resize handle ─────────────────────────────────────────────────

const _handle = document.getElementById('kf-resize-handle');
const _panel  = document.getElementById('kf-tree-panel');
let   _drag   = null;

_handle.addEventListener('mousedown', e => {
  _drag = { startX: e.clientX, startW: _panel.offsetWidth };
  _handle.classList.add('dragging');
  e.preventDefault();
});
document.addEventListener('mousemove', e => {
  if (!_drag) return;
  const w = Math.max(140, Math.min(420, _drag.startW + e.clientX - _drag.startX));
  _panel.style.width = w + 'px';
  document.documentElement.style.setProperty('--kf-tree-w', w + 'px');
});
document.addEventListener('mouseup', () => {
  if (_drag) { _drag = null; _handle.classList.remove('dragging'); }
});

// ── Helpers ──────────────────────────────────────────────────────

function _esc(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
