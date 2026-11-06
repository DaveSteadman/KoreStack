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
import * as tree    from './tree.js?v=20261106g';

// ── State ───────────────────────────────────────────────────────

const _SCROLL_KEY = 'koredocs:kf:scroll';

let _currentFolderId = null;
let _files           = [];
let _selectedFileIds = new Set();
let _fileSelectionAnchor = null;
let _fileCurrentId   = null;
let _sortKey         = 'name';
let _sortAsc         = true;
let _searchMode      = false;
let _searchQuery     = '';
let _contextTarget   = null;
let _pendingRestore  = true;   // restore scroll/selection once on initial page load
let _loadSequence    = 0;

// ── DOM refs ────────────────────────────────────────────────────

const _tbody     = document.getElementById('kf-file-tbody');
const _empty     = document.getElementById('kf-empty');
const _loading   = document.getElementById('kf-loading');
const _ctxMenu   = document.getElementById('ctx-menu');
const _summary   = document.getElementById('kf-results-summary');

// ── Public: load a folder ───────────────────────────────────────

export async function loadFolder(folderId) {
  const sequence = ++_loadSequence;
  _searchMode = false;
  _searchQuery = '';
  _currentFolderId = folderId;
  _clearFileSelection();
  _showLoading(true);
  try {
    const files = await api.listFiles({ folderId });
    if (sequence !== _loadSequence) return;
    _files = files;
    _renderList();
    if (_pendingRestore) {
      _pendingRestore = false;
      _tryRestoreScrollState(folderId);
    }
  } catch (err) {
    if (sequence !== _loadSequence) return;
    _tbody.innerHTML = '';
    _empty.textContent = 'Error loading files: ' + err.message;
    _empty.style.display = '';
    _summary.textContent = 'Could not load files';
  } finally {
    if (sequence === _loadSequence) _showLoading(false);
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
          _selectedFileIds.add(Number(row.dataset.id));
          _fileSelectionAnchor = Number(row.dataset.id);
          _fileCurrentId = Number(row.dataset.id);
          break;
        }
      }
      _renderList();
      if (wrap) wrap.scrollTop = saved.scrollTop || 0;
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
  const sequence = ++_loadSequence;
  _searchMode = true;
  _searchQuery = q;
  _clearFileSelection();
  _showLoading(true);
  try {
    const files = await api.search(q);
    if (sequence !== _loadSequence) return;
    _files = files;
    _renderList();
  } catch (err) {
    if (sequence !== _loadSequence) return;
    _tbody.innerHTML = '';
    _empty.textContent = 'Search error: ' + err.message;
    _empty.style.display = '';
    _summary.textContent = 'Search failed';
  } finally {
    if (sequence === _loadSequence) _showLoading(false);
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

const COPY_SVG = `<svg viewBox="0 0 20 20" fill="none" width="12" height="12">
  <rect x="7" y="6" width="10" height="11" rx="1" stroke="currentColor" stroke-width="1.4"/>
  <path d="M13 6V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h3" stroke="currentColor" stroke-width="1.4"/>
</svg>`;

const MOVE_SVG = `<svg viewBox="0 0 20 20" fill="none" width="12" height="12">
  <path d="M3 10h13M11 5l5 5-5 5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>`;

const TRASH_SVG = `<svg viewBox="0 0 20 20" fill="none" width="12" height="12">
  <path d="M3 6h14M8 6V4h4v2M5 6l1 11h8l1-11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
</svg>`;

const TYPE_URL = { koredoc: '/doc', koresheet: '/sheet', korediag: '/diag', csv: '/sheet' };
const TEXT_EXTS = new Set(['csv', 'json', 'log', 'md', 'py', 'txt', 'xml', 'yaml', 'yml']);

function _fileIcon(ext) {
  return resolveIcon(SUITE_ICONS, ext, 20);
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

function _visibleFileRows() {
  return [..._tbody.querySelectorAll('tr[data-id]')];
}

function _selectFileRow(row, event) {
  const id = Number(row.dataset.id);
  if (event.shiftKey) {
    _selectFileRange(id);
    _fileCurrentId = id;
  } else if (event.ctrlKey || event.metaKey) {
    _toggleFileSelection(id);
    return;
  } else {
    _selectedFileIds = new Set([id]);
    _fileSelectionAnchor = id;
    _fileCurrentId = id;
  }
  _renderList();
  _focusFileRow(id);
}

function _selectFileRange(id) {
  const rows = _visibleFileRows();
  const anchor = _fileSelectionAnchor ?? id;
  const start = rows.findIndex(row => Number(row.dataset.id) === anchor);
  const end = rows.findIndex(row => Number(row.dataset.id) === id);
  if (start < 0 || end < 0) {
    _selectedFileIds = new Set([id]);
    _fileSelectionAnchor = id;
    return;
  }
  const [from, to] = start < end ? [start, end] : [end, start];
  _selectedFileIds = new Set(rows.slice(from, to + 1).map(row => Number(row.dataset.id)));
}

function _toggleFileSelection(id) {
  if (_selectedFileIds.has(id)) _selectedFileIds.delete(id);
  else _selectedFileIds.add(id);
  _fileSelectionAnchor = id;
  _fileCurrentId = _selectedFileIds.has(id) ? id : [..._selectedFileIds].at(-1) ?? null;
  _renderList();
  _focusFileRow(_fileCurrentId ?? id);
}

function _focusFileRow(id) {
  _tbody.querySelector(`tr[data-id="${id}"]`)?.focus();
}

function _clearFileSelection() {
  _selectedFileIds.clear();
  _fileSelectionAnchor = null;
  _fileCurrentId = null;
}

function _renderList() {
  const sorted = _sortedFiles();
  _empty.style.display   = sorted.length ? 'none' : '';
  _empty.textContent     = _searchMode ? 'No results.' : 'No files in this folder.';
  _summary.textContent   = `${sorted.length.toLocaleString()} ${sorted.length === 1 ? 'file' : 'files'}${_searchMode ? ' found' : ''}`;

  _tbody.innerHTML = sorted.map(f => {
    const meta  = typeof f.metadata === 'object' ? f.metadata : {};
    const title = meta.title || f.name.replace(/\.[^.]+$/, '');
    const isSelected = _selectedFileIds.has(f.id);
    const isCurrent = isSelected && f.id === _fileCurrentId;
    const singleActions = `${_actionBtn('open', 'Open', OPEN_SVG)}
           ${_actionBtn('open-text', 'Open as Text', TEXT_SVG)}
           ${_actionBtn('rename', 'Rename', EDIT_SVG)}
           ${_actionBtn('move', 'Move', MOVE_SVG)}
           ${_actionBtn('copy', 'Copy', COPY_SVG)}
           ${_actionBtn('delete', 'Delete', TRASH_SVG)}`;
    const actions = _selectedFileIds.size > 1
      ? isCurrent
        ? `${_actionBtn('bulk-move', 'Move selected files', MOVE_SVG)}
           ${_actionBtn('bulk-copy', 'Copy selected files', COPY_SVG)}
           ${_actionBtn('bulk-delete', 'Delete selected files', TRASH_SVG)}`
        : isSelected ? '' : singleActions
      : _selectedFileIds.size === 0 || isCurrent ? singleActions : '';
    return `
      <tr class="${isSelected ? 'selected' : ''} ${isCurrent ? 'current-action-row' : ''}" data-id="${f.id}" data-folder="${f.folder_id}" data-name="${_esc(f.name)}" data-ext="${f.ext}" data-revision="${f.revision ?? ''}" tabindex="0" aria-selected="${isSelected}" aria-label="${_esc(`File: ${title}. ${f.ext} file.`)}">
        <td class="col-icon"><span class="file-icon">${_fileIcon(f.ext)}</span></td>
        <td class="col-name" title="${_esc(f.name)}">${_esc(title)}</td>
        <td class="col-type"><span class="kcui-tag kcui-tag--info">${_esc(f.ext)}</span></td>
        <td class="col-words">${(f.word_count ?? 0).toLocaleString()}</td>
        <td class="col-modified">${_fmt(f.modified_at)}</td>
        <td class="col-actions">
          <div class="row-actions">${actions}</div>
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
  _selectFileRow(row, e);
});

_tbody.addEventListener('keydown', e => {
  const row = e.target.closest('tr[data-id]');
  if (!row || e.target.closest('button, input')) return;
  if (e.key === 'Enter') {
    e.preventDefault();
    _openFile(row);
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
    e.preventDefault();
    _selectedFileIds = new Set(_visibleFileRows().map(item => Number(item.dataset.id)));
    _fileSelectionAnchor = Number(row.dataset.id);
    _fileCurrentId = Number(row.dataset.id);
    _renderList();
    _focusFileRow(_fileSelectionAnchor);
  } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const rows = _visibleFileRows();
    const next = rows[rows.indexOf(row) + (e.key === 'ArrowDown' ? 1 : -1)];
    if (!next) return;
    if (e.shiftKey) {
      _selectFileRange(Number(next.dataset.id));
    } else {
      _selectedFileIds = new Set([Number(next.dataset.id)]);
      _fileSelectionAnchor = Number(next.dataset.id);
    }
    _fileCurrentId = Number(next.dataset.id);
    _renderList();
    _focusFileRow(Number(next.dataset.id));
  } else if (e.key === ' ' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    _toggleFileSelection(Number(row.dataset.id));
  } else if (e.key === 'F2') {
    e.preventDefault();
    _startRename(row);
  } else if (e.key === 'Delete') {
    e.preventDefault();
    if (_selectedFileIds.size > 1) _deleteFiles(_selectedFileRecords());
    else void _deleteFile(row);
  }
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
  if (action === 'move') _moveFiles([_fileForRow(row)]);
  if (action === 'copy') _copyFiles([_fileForRow(row)]);
  if (action === 'delete') _deleteFile(row);
  if (action === 'bulk-move') _moveFiles(_selectedFileRecords());
  if (action === 'bulk-copy') _copyFiles(_selectedFileRecords());
  if (action === 'bulk-delete') _deleteFiles(_selectedFileRecords());
});

// ── Context menu ────────────────────────────────────────────────

_tbody.addEventListener('contextmenu', e => {
  const row = e.target.closest('tr[data-id]');
  if (!row) return;
  e.preventDefault();
  const id = Number(row.dataset.id);
  if (!_selectedFileIds.has(id)) {
    _selectedFileIds = new Set([id]);
    _fileSelectionAnchor = id;
  }
  _fileCurrentId = id;
  _renderList();
  _contextTarget = _tbody.querySelector(`tr[data-id="${id}"]`);
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
  if (action === 'copy') _copyFiles([_fileForRow(_contextTarget)]);
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
    void dialogs.alert('Unknown File Type', `Unknown file type: ${ext}`);
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
      await dialogs.alert('Rename Failed', err.message || 'Rename failed.');
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
  await _deleteFiles([_fileForRow(row)]);
}

async function _deleteFiles(files) {
  if (!files.length) return;
  const ok = await dialogs.confirm(
    files.length === 1 ? 'Delete File' : 'Delete Files',
    files.length === 1
      ? `Delete "${files[0].name}"?`
      : `Delete ${files.length} selected files? This cannot be undone.`,
  );
  if (!ok) return;
  const failed = [];
  let deleted = 0;
  for (const file of files) {
    try {
      await api.deleteFile(file.id, file.revision);
      deleted += 1;
    } catch (err) {
      failed.push({ file, message: err.message || 'Delete failed.' });
    }
  }
  await _refreshFileView();
  await _showFileOperationResult('deleted', deleted, files.length, failed);
}

async function _moveFile(row) {
  await _moveFiles([_fileForRow(row)]);
}

async function _moveFiles(files) {
  if (!files.length) return;
  const newFolderId = await dialogs.moveFiles(
    tree.getFolders(),
    files.map(file => file.folder_id),
    files.length,
  );
  if (newFolderId == null) return;
  const failed = [];
  let moved = 0;
  for (const file of files) {
    try {
      await api.patchFile(file.id, {
        folder_id: newFolderId,
        expected_revision: file.revision,
      });
      moved += 1;
    } catch (err) {
      failed.push({ file, message: err.message || 'Move failed.' });
    }
  }
  await _refreshFileView();
  await _showFileOperationResult('moved', moved, files.length, failed);
}

async function _copyFiles(files) {
  if (!files.length) return;
  const destinationFolderId = await dialogs.copyFiles(tree.getFolders(), files.length);
  if (destinationFolderId == null) return;
  let result;
  try {
    result = await api.copyFiles(
      files.map(file => ({ id: file.id, expected_revision: file.revision })),
      destinationFolderId,
    );
  } catch (err) {
    await dialogs.alert('File Copy Failed', err.message || 'Could not copy files.');
    return;
  }
  await _refreshFileView();
  const failures = result.failed.map(item => ({
    file: files.find(file => file.id === item.id),
    message: item.error,
  }));
  await _showFileOperationResult('copied', result.copied.length, files.length, failures);
}

function _selectedFileRecords() {
  return _files.filter(file => _selectedFileIds.has(file.id));
}

function _fileForRow(row) {
  const id = Number(row.dataset.id);
  return _files.find(file => file.id === id) || {
    id,
    name: row.dataset.name,
    folder_id: Number(row.dataset.folder),
    revision: row.dataset.revision || null,
  };
}

async function _refreshFileView() {
  if (_searchMode) await loadSearch(_searchQuery);
  else if (_currentFolderId != null) await loadFolder(_currentFolderId);
}

async function _showFileOperationResult(action, completed, total, failures) {
  if (!failures.length) return;
  const details = failures.map(({ file, message }) =>
    `${file?.name || 'File'}: ${message}`,
  ).join('\n');
  await dialogs.alert(
    'Some files were not processed',
    `${completed} of ${total} files ${action}.\n${details}`,
  );
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
    await dialogs.alert('Create Failed', err.message || 'Create failed.');
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
  return `<button class="icon-btn" type="button" data-btn="${action}" title="${title}" aria-label="${title}">${svg}</button>`;
}

function _esc(s) {
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}
