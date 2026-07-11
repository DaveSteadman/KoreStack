/**
 * filelist.js — File list panel for KoreFile explorer.
 *
 * Listens for:
 *   kf:folder-changed  { folderId }  — reload list for new folder
 *   kf:search          { q }         — show search results instead
 *
 * Emits nothing public — actions go directly through api.js.
 */

import * as api     from './api.js';
import * as dialogs from './dialogs.js';
import * as tree    from './tree.js';

// ── State ───────────────────────────────────────────────────────

const _SCROLL_KEY = 'koredocs:kf:scroll';

let _currentFolderId = null;
let _files           = [];
let _sortKey         = 'name';
let _sortAsc         = true;
let _searchMode      = false;
let _contextTarget   = null;
let _pendingRestore  = true;   // restore scroll/selection once on initial page load

// ── DOM refs ────────────────────────────────────────────────────

const _tbody     = document.getElementById('kf-file-tbody');
const _empty     = document.getElementById('kf-empty');
const _loading   = document.getElementById('kf-loading');
const _ctxMenu   = document.getElementById('ctx-menu');

// ── Public: load a folder ───────────────────────────────────────

export async function loadFolder(folderId) {
  _searchMode = false;
  _currentFolderId = folderId;
  _showLoading(true);
  try {
    _files = await api.listFiles({ folderId });
    _renderList();
    if (_pendingRestore) {
      _pendingRestore = false;
      _tryRestoreScrollState(folderId);
    }
  } catch (err) {
    _tbody.innerHTML = '';
    _empty.textContent = 'Error loading files: ' + err.message;
    _empty.style.display = '';
  } finally {
    _showLoading(false);
  }
}

function _tryRestoreScrollState(folderId) {
  try {
    const saved = JSON.parse(localStorage.getItem(_SCROLL_KEY) || 'null');
    if (!saved || saved.folderId !== folderId) return;
    const wrap = document.getElementById('kf-file-list-wrap');
    if (wrap) wrap.scrollTop = saved.scrollTop || 0;
    if (saved.selectedName) {
      for (const row of _tbody.querySelectorAll('tr[data-name]')) {
        if (row.dataset.name === saved.selectedName) {
          row.classList.add('selected');
          break;
        }
      }
    }
  } catch { /* ignore */ }
}

export function saveScrollState() {
  if (_searchMode || _currentFolderId == null) return;
  try {
    const wrap = document.getElementById('kf-file-list-wrap');
    const selectedRow = _tbody.querySelector('tr.selected');
    localStorage.setItem(_SCROLL_KEY, JSON.stringify({
      folderId: _currentFolderId,
      scrollTop: wrap ? wrap.scrollTop : 0,
      selectedName: selectedRow ? selectedRow.dataset.name : null,
    }));
  } catch { /* ignore */ }
}

window.addEventListener('pagehide', saveScrollState);
document.addEventListener('kd:before-navigate', saveScrollState);

export async function loadSearch(q) {
  _searchMode = true;
  _showLoading(true);
  try {
    _files = await api.search(q);
    _renderList();
  } catch (err) {
    _tbody.innerHTML = '';
    _empty.textContent = 'Search error: ' + err.message;
    _empty.style.display = '';
  } finally {
    _showLoading(false);
  }
}

// ── Rendering ───────────────────────────────────────────────────

import { resolveIcon, SUITE_ICONS } from '/ui-elements/assets/js/icons.js';

const OPEN_SVG = `<svg viewBox="0 0 20 20" fill="none" width="12" height="12">
  <path d="M4 4h5v2H6v8h8v-3h2v5H4V4z" fill="currentColor"/>
  <path d="M10 10l6-6M11 4h5v5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
</svg>`;

const EDIT_SVG = `<svg viewBox="0 0 20 20" fill="none" width="12" height="12">
  <path d="M14.5 3.5a2.121 2.121 0 0 1 3 3L6 18H3v-3L14.5 3.5z" stroke="currentColor" stroke-width="1.5"/>
</svg>`;

const TEXT_SVG = `<svg viewBox="0 0 20 20" fill="none" width="12" height="12">
  <path d="M4 5h12M4 10h12M4 15h8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>
</svg>`;

const TRASH_SVG = `<svg viewBox="0 0 20 20" fill="none" width="12" height="12">
  <path d="M3 6h14M8 6V4h4v2M5 6l1 11h8l1-11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
</svg>`;

const TYPE_URL = { koredoc: '/doc', koresheet: '/sheet', korediag: '/diag', csv: '/sheet' };
const TEXT_EXTS = new Set(['csv', 'json', 'log', 'md', 'py', 'txt', 'xml', 'yaml', 'yml']);

function _fileIcon(ext) {
  return resolveIcon(SUITE_ICONS, ext, 15);
}

function _sortedFiles() {
  const key = _sortKey;
  const asc = _sortAsc;
  return [..._files].sort((a, b) => {
    let va = a[key] ?? '', vb = b[key] ?? '';
    if (typeof va === 'string') va = va.toLowerCase();
    if (typeof vb === 'string') vb = vb.toLowerCase();
    if (va < vb) return asc ? -1 : 1;
    if (va > vb) return asc ? 1 : -1;
    return 0;
  });
}

function _fmt(iso) {
  if (!iso) return '';
  try {
    const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
    return d.toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' });
  } catch { return iso; }
}

function _renderList() {
  const sorted = _sortedFiles();
  _empty.style.display   = sorted.length ? 'none' : '';
  _empty.textContent     = _searchMode ? 'No results.' : 'No files in this folder.';

  _tbody.innerHTML = sorted.map(f => {
    const meta  = typeof f.metadata === 'object' ? f.metadata : {};
    const title = meta.title || f.name.replace(/\.[^.]+$/, '');
    return `
      <tr data-id="${f.id}" data-folder="${f.folder_id}" data-name="${_esc(f.name)}" data-ext="${f.ext}" data-revision="${f.revision ?? ''}">
        <td class="col-icon"><span class="file-icon">${_fileIcon(f.ext)}</span></td>
        <td class="col-name" title="${_esc(f.name)}">${_esc(title)}</td>
        <td class="col-type"><span class="kcui-tag kcui-tag--info">${_esc(f.ext)}</span></td>
        <td class="col-words">${(f.word_count ?? 0).toLocaleString()}</td>
        <td class="col-modified">${_fmt(f.modified_at)}</td>
        <td class="col-actions">
          <div class="row-actions">
            ${_actionBtn('open', 'Open', OPEN_SVG)}
            ${_actionBtn('open-text', 'Open as Text', TEXT_SVG)}
            ${_actionBtn('rename', 'Rename', EDIT_SVG)}
            ${_actionBtn('delete', 'Delete', TRASH_SVG)}
          </div>
        </td>
      </tr>`;
  }).join('');

  // Update sort arrows
  document.querySelectorAll('#kf-file-list th[data-sort]').forEach(th => {
    th.classList.toggle('sorted', th.dataset.sort === _sortKey);
    const arrow = th.querySelector('.sort-arrow');
    if (arrow) {
      arrow.textContent = th.dataset.sort === _sortKey
        ? (_sortAsc ? ' ▲' : ' ▼') : '';
    }
  });
}

// ── Sort header clicks ──────────────────────────────────────────

document.querySelectorAll('#kf-file-list th[data-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const key = th.dataset.sort;
    if (_sortKey === key) _sortAsc = !_sortAsc;
    else { _sortKey = key; _sortAsc = true; }
    _renderList();
  });
});

// ── Row double-click: open ──────────────────────────────────────

_tbody.addEventListener('dblclick', e => {
  const row = e.target.closest('tr[data-id]');
  if (!row) return;
  _openFile(row);
});

// ── Row single click: select ────────────────────────────────────

_tbody.addEventListener('click', e => {
  const btn = e.target.closest('[data-btn]');
  if (btn) return; // handled below

  const row = e.target.closest('tr[data-id]');
  if (!row) return;
  document.querySelectorAll('#kf-file-tbody tr.selected')
    .forEach(r => r.classList.remove('selected'));
  row.classList.add('selected');
});

// ── Action buttons inside row ───────────────────────────────────

_tbody.addEventListener('click', e => {
  const btn = e.target.closest('[data-btn]');
  if (!btn) return;
  e.stopPropagation();
  const row = btn.closest('tr[data-id]');
  if (!row) return;
  const action = btn.dataset.btn;
  if (action === 'open')   _openFile(row);
  if (action === 'open-text') _openFileAsText(row);
  if (action === 'rename') _startRename(row);
  if (action === 'delete') _deleteFile(row);
});

// ── Context menu ────────────────────────────────────────────────

_tbody.addEventListener('contextmenu', e => {
  const row = e.target.closest('tr[data-id]');
  if (!row) return;
  e.preventDefault();
  _contextTarget = row;
  document.querySelectorAll('#kf-file-tbody tr.selected')
    .forEach(r => r.classList.remove('selected'));
  row.classList.add('selected');
  _showCtx(e.clientX, e.clientY);
});

document.addEventListener('click', () => _hideCtx());
document.addEventListener('keydown', e => { if (e.key === 'Escape') _hideCtx(); });

function _showCtx(x, y) {
  _ctxMenu.style.left = x + 'px';
  _ctxMenu.style.top  = y + 'px';
  _ctxMenu.classList.add('visible');
  // Ensure it stays on screen
  requestAnimationFrame(() => {
    const r = _ctxMenu.getBoundingClientRect();
    if (r.right  > window.innerWidth)  _ctxMenu.style.left = (x - r.width)  + 'px';
    if (r.bottom > window.innerHeight) _ctxMenu.style.top  = (y - r.height) + 'px';
  });
}

function _hideCtx() {
  _ctxMenu.classList.remove('visible');
}

_ctxMenu.addEventListener('click', e => {
  const li = e.target.closest('li[data-action]');
  if (!li || !_contextTarget) return;
  _hideCtx();
  const action = li.dataset.action;
  if (action === 'open')   _openFile(_contextTarget);
  if (action === 'open-text') _openFileAsText(_contextTarget);
  if (action === 'rename') _startRename(_contextTarget);
  if (action === 'delete') _deleteFile(_contextTarget);
  if (action === 'move')   _moveFile(_contextTarget);
});

// ── File operations ─────────────────────────────────────────────

const _TABS_KEY = 'koredocs:tabs';

function _openFile(row) {
  const id   = parseInt(row.dataset.id, 10);
  const ext  = row.dataset.ext;
  const name = row.dataset.name;
  const url  = TYPE_URL[ext];
  if (!url) {
    if (TEXT_EXTS.has(ext)) {
      _openFileAsText(row);
      return;
    }
    alert('Unknown file type: ' + ext);
    return;
  }

  // Register in the shared tab store so it appears in the tab bar.
  try {
    const tabs = JSON.parse(localStorage.getItem(_TABS_KEY) || '[]');
    if (!tabs.find(t => (t.id != null ? t.id === id : t.name === name))) {
      tabs.push({ id, name, type: ext });
      localStorage.setItem(_TABS_KEY, JSON.stringify(tabs));
    }
  } catch { /* ignore storage errors */ }

  // Navigate in the same window — everything is one SPA.
  location.href = url + '?id=' + encodeURIComponent(id) + '&file=' + encodeURIComponent(name);
}

function _openFileAsText(row) {
  const id = parseInt(row.dataset.id, 10);
  const name = row.dataset.name;
  try {
    const tabs = JSON.parse(localStorage.getItem(_TABS_KEY) || '[]');
    if (!tabs.find(t => t.type === 'textedit' && (t.id != null ? t.id === id : t.name === name))) {
      tabs.push({ id, name, type: 'textedit' });
      localStorage.setItem(_TABS_KEY, JSON.stringify(tabs));
    }
  } catch {
    // Ignore storage failures.
  }
  location.href = '/textedit?id=' + encodeURIComponent(id) + '&file=' + encodeURIComponent(name);
}

function _startRename(row) {
  const td   = row.querySelector('.col-name');
  const orig = row.dataset.name.replace(/\.[^.]+$/, '');
  td.innerHTML = `<input class="rename-input" value="${_esc(orig)}" />`;
  const inp = td.querySelector('input');
  inp.focus();
  inp.select();

  const commit = async () => {
    const newBase = inp.value.trim();
    const ext     = '.' + row.dataset.ext;
    if (!newBase || newBase + ext === row.dataset.name) {
      await _refreshRow(row);
      return;
    }
    const newName = newBase + ext;
    try {
      const expectedRevision = row.dataset.revision || null;
      const updated = await api.patchFile(parseInt(row.dataset.id, 10), {
        name: newName,
        expected_revision: expectedRevision,
      });
      row.dataset.name = updated.name;
      row.dataset.revision = String(updated.revision ?? row.dataset.revision ?? '');
      _renameOpenTabs(parseInt(row.dataset.id, 10), updated.name);
      const meta = typeof updated.metadata === 'object' ? updated.metadata : {};
      td.textContent = _esc(meta.title || newBase);
    } catch (err) {
      alert('Rename failed: ' + err.message);
      await _refreshRow(row);
    }
  };

  inp.addEventListener('blur', commit, { once: true });
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { e.preventDefault(); inp.blur(); }
    if (e.key === 'Escape') { inp.removeEventListener('blur', commit); _refreshRow(row); }
  });
}

async function _deleteFile(row) {
  const ok = await dialogs.confirm('Delete File', `Delete "${row.dataset.name}"?`);
  if (!ok) return;
  try {
    await api.deleteFile(
      parseInt(row.dataset.id, 10),
      row.dataset.revision || null,
    );
    _files = _files.filter(f => f.id !== parseInt(row.dataset.id, 10));
    _renderList();
  } catch (err) {
    alert('Delete failed: ' + err.message);
  }
}

async function _moveFile(row) {
  const folders = tree.getFolders();
  const newFolderId = await dialogs.moveFile(folders, parseInt(row.dataset.folder, 10));
  if (newFolderId == null) return;
  try {
    const fileId = parseInt(row.dataset.id, 10);
    const expectedRevision = row.dataset.revision || null;
    await api.patchFile(fileId, {
      folder_id: newFolderId,
      expected_revision: expectedRevision,
    });
    _files = _files.filter(f => f.id !== fileId);
    _renderList();
  } catch (err) {
    alert('Move failed: ' + err.message);
  }
}

function _renameOpenTabs(fileId, newName) {
  try {
    const tabs = JSON.parse(localStorage.getItem(_TABS_KEY) || '[]');
    let changed = false;
    for (const tab of tabs) {
      if (tab.id === fileId && tab.name !== newName) {
        tab.name = newName;
        changed = true;
      }
    }
    if (changed) localStorage.setItem(_TABS_KEY, JSON.stringify(tabs));
  } catch {
    // Ignore storage failures.
  }
}

async function _refreshRow(row) {
  // Re-render the full list to restore the row's content after a cancelled edit
  if (_currentFolderId != null) await loadFolder(_currentFolderId);
}

// ── New file (called from toolbar) ─────────────────────────────

export async function createNewFile(folderId) {
  const result = await dialogs.newFile();
  if (!result) return;
  try {
    const created = await api.createFile(
      folderId,
      result.name,
      _initialContent(result.name, result.ext),
      { title: result.name.replace(/\.[^.]+$/, '') },
    );
    _files.push(created);
    _renderList();
  } catch (err) {
    alert('Create failed: ' + err.message);
  }
}

function _initialContent(name, ext) {
  const title = name.replace(/\.[^.]+$/, '');
  const today = new Date().toISOString().slice(0, 10);
  if (ext === 'koredoc') {
    return `---\ntitle: ${title}\ncreated: ${today}\n---\n\n`;
  }
  if (ext === 'koresheet') {
    return JSON.stringify({
      version: 1,
      meta: { title, created: today },
      cols: 26,
      rows: 100,
      cells: {},
    }, null, 2);
  }
  return JSON.stringify({
    koreDiag: '1.0',
    id: crypto.randomUUID(),
    title,
    created: new Date().toISOString(),
    modified: new Date().toISOString(),
    settings: {
      gridSize: 20,
      defaultArrow: 'forward',
      showGrid: true,
      defaultNodeStyle: {
        fillColor: '#1f1f1f',
        strokeColor: '#6f6f6f',
        strokeWidth: 1.5,
        fontSize: 13,
      },
      customColors: [],
    },
    nodes: [],
    edges: [],
  }, null, 2);
}

// ── Helpers ─────────────────────────────────────────────────────

function _showLoading(on) {
  _loading.style.display = on ? '' : 'none';
  if (on) _tbody.innerHTML = '';
}

function _actionBtn(action, title, svg) {
  return `<button class="icon-btn" data-btn="${action}" title="${title}">${svg}</button>`;
}

function _esc(s) {
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}


