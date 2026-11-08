/**
 * tree.js — Folder tree panel for KoreFile explorer.
 *
 * Emits:
 *   tree:select   { folder }   — user clicked a folder
 *   tree:refresh  {}           — tree data changed (folder added/deleted)
 */

import * as api     from './api.js';
import * as dialogs from './dialogs.js';
import { svgIconMask } from '/ui-elements/assets/js/svgicons.js';

const _treeEl = document.getElementById('kf-tree');

const TRIANGLE_SVG = svgIconMask('chevron-right-svgrepo-com', { size: 10 });
const EDIT_SVG = svgIconMask('pencil-svgrepo-com', { size: 12 });
const TRASH_SVG = svgIconMask('trash-svgrepo-com', { size: 12 });
const MOVE_SVG = svgIconMask('folder-arrow-down-svgrepo-com', { size: 12 });
const COPY_SVG = svgIconMask('clone-svgrepo-com', { size: 12 });

// ── State ───────────────────────────────────────────────────────

let _folders  = [];        // flat list from server
let _selected = null;      // currently selected folder id
let _focused  = null;      // roving keyboard focus folder id
let _folderSelection = new Set();
let _folderAnchor = null;
let _folderActionCurrentId = null;
let _open     = new Set(); // folder ids that are expanded

// ── Events ─────────────────────────────────────────────────────

function _emit(name, detail) {
  document.dispatchEvent(new CustomEvent('kf:' + name, { detail }));
}

// ── Build tree structure ────────────────────────────────────────

function _buildTree(folders) {
  // adjacency list by id
  const byId = {};
  const children = {};
  for (const f of folders) {
    byId[f.id] = f;
    children[f.id] = [];
  }
  const roots = [];
  for (const f of folders) {
    if (f.parent_id == null) roots.push(f);
    else if (children[f.parent_id]) children[f.parent_id].push(f);
  }
  return { byId, children, roots };
}

// ── Rendering ──────────────────────────────────────────────────

function _renderNode(folder, children, depth) {
  const kids = children[folder.id] || [];
  const hasKids = kids.length > 0;
  const isOpen = _open.has(folder.id);
  const isSelected = _folderSelection.has(folder.id);
  const isActionCurrent = isSelected && folder.id === _folderActionCurrentId;
  const label = folder.path === '/' ? 'Root' : folder.name;

  const chevron = hasKids
    ? `<span class="tree-toggle">${TRIANGLE_SVG}</span>`
    : `<span class="tree-toggle" style="visibility:hidden">${TRIANGLE_SVG}</span>`;

  const singleActions = `
    ${_iconBtn('rename-folder', 'Rename folder', EDIT_SVG)}
    ${_iconBtn('move-folder', 'Move folder', MOVE_SVG)}
    ${_iconBtn('copy-folder', 'Copy folder', COPY_SVG)}
    ${_iconBtn('delete-folder', 'Delete folder', TRASH_SVG)}`;
  const bulkActions = `
    ${_iconBtn('bulk-move-folders', 'Move selected folders', MOVE_SVG)}
    ${_iconBtn('bulk-copy-folders', 'Copy selected folders', COPY_SVG)}
    ${_iconBtn('bulk-delete-folders', 'Delete selected folders', TRASH_SVG)}`;
  const actionButtons = folder.id === 1
    ? ''
    : _folderSelection.size > 1
      ? isActionCurrent ? bulkActions : isSelected ? '' : singleActions
      : _folderSelection.size === 0 || isActionCurrent ? singleActions : '';
  const actions = actionButtons ? `<span class="tree-actions">${actionButtons}</span>` : '';

  const html = `
    <div class="tree-item tree-indent ${isSelected ? 'selected' : ''} ${isOpen ? 'open' : ''}"
         data-id="${folder.id}" data-depth="${depth}" style="--indent:${depth}"
         role="treeitem" aria-level="${depth + 1}" aria-selected="${isSelected}"
         tabindex="${folder.id === _focused ? '0' : '-1'}"
         ${hasKids ? `aria-expanded="${isOpen}" aria-owns="kf-tree-children-${folder.id}"` : ''}
         ${folder.id !== 1 ? 'draggable="true"' : ''}>
      ${chevron}
      <span class="tree-label" title="${_esc(folder.path)}">${_esc(label)}</span>
      ${actions}
    </div>
    <div id="kf-tree-children-${folder.id}" class="tree-children ${isOpen ? 'open' : ''}"
         data-parent="${folder.id}" role="group">
      ${kids.map(c => _renderNode(c, children, depth + 1)).join('')}
    </div>`;
  return html;
}

function _render(folders) {
  _folders = folders;
  const { children, roots } = _buildTree(folders);
  const folderIds = new Set(folders.map(folder => folder.id));
  _folderSelection = new Set([..._folderSelection].filter(id => id !== 1 && folderIds.has(id)));
  if (!_folderSelection.has(_folderActionCurrentId)) {
    _folderActionCurrentId = _folderSelection.has(_focused)
      ? _focused
      : [..._folderSelection].pop() ?? null;
  }
  // Auto-expand root-level folders on first load
  if (_open.size === 0) roots.forEach(r => _open.add(r.id));
  if (!_folders.some(folder => folder.id === _focused)) {
    _focused = _folders.some(folder => folder.id === _selected) ? _selected : (roots[0]?.id ?? null);
  }
  _treeEl.innerHTML = roots.map(r => _renderNode(r, children, 0)).join('');
}

// ── Events inside tree ──────────────────────────────────────────

_treeEl.addEventListener('click', e => {
  const item = e.target.closest('.tree-item');
  if (!item) return;

  const id = parseInt(item.dataset.id, 10);
  const folder = _folders.find(f => f.id === id);
  if (!folder) return;

  // Action buttons
  if (e.target.closest('[data-btn="rename-folder"]')) {
    e.stopPropagation();
    _renameFolder(folder);
    return;
  }
  if (e.target.closest('[data-btn="move-folder"]')) {
    e.stopPropagation();
    _moveFolder(folder);
    return;
  }
  if (e.target.closest('[data-btn="copy-folder"]')) {
    e.stopPropagation();
    _copyFolders([folder]);
    return;
  }
  if (e.target.closest('[data-btn="delete-folder"]')) {
    e.stopPropagation();
    _deleteFolder(folder);
    return;
  }
  if (e.target.closest('[data-btn="bulk-move-folders"]')) {
    e.stopPropagation();
    _moveFolders(_selectedFolderRoots());
    return;
  }
  if (e.target.closest('[data-btn="bulk-copy-folders"]')) {
    e.stopPropagation();
    _copyFolders(_selectedFolderRoots());
    return;
  }
  if (e.target.closest('[data-btn="bulk-delete-folders"]')) {
    e.stopPropagation();
    _deleteFolders(_selectedFolderRoots());
    return;
  }
  // Toggle open/closed
  if (e.target.closest('.tree-toggle')) {
    _open.has(id) ? _open.delete(id) : _open.add(id);
    _renderAndFocus(id);
    return;
  }

  if (e.shiftKey) {
    _selectFolderRange(id);
    _focusItem(id);
    return;
  }
  if (e.ctrlKey || e.metaKey) {
    if (_folderSelection.has(id)) _folderSelection.delete(id);
    else _folderSelection.add(id);
    _folderAnchor = id;
    _setFolderActionCurrent(id);
    _render(_folders);
    _focusItem(id);
    return;
  }
  _selectFolder(id);
});

_treeEl.addEventListener('keydown', e => {
  if (e.target.closest('.tree-actions')) return;
  const item = e.target.closest('.tree-item[role="treeitem"]');
  if (!item) return;

  const id = Number(item.dataset.id);
  const visible = [..._treeEl.querySelectorAll('.tree-item[role="treeitem"]')]
    .filter(node => node.getClientRects().length > 0);
  const index = visible.indexOf(item);
  const hasChildren = _folders.some(folder => folder.parent_id === id);

  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const next = visible[index + (e.key === 'ArrowDown' ? 1 : -1)];
    if (next) {
      const nextId = Number(next.dataset.id);
      if (e.shiftKey) {
        _selectFolderRange(nextId);
        _focusItem(nextId);
      } else {
        _focusItem(nextId);
      }
    }
  } else if (e.key === 'Home' || e.key === 'End') {
    e.preventDefault();
    const next = e.key === 'Home' ? visible[0] : visible[visible.length - 1];
    if (next) {
      const nextId = Number(next.dataset.id);
      if (e.shiftKey) _selectFolderRange(nextId);
      _focusItem(nextId);
    }
  } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'a') {
    e.preventDefault();
    _folderSelection = new Set(visible
      .map(node => Number(node.dataset.id))
      .filter(folderId => folderId !== 1));
    _folderAnchor = id;
    _setFolderActionCurrent(id);
    _render(_folders);
    _focusItem(id);
  } else if (e.key === 'ArrowRight' && hasChildren) {
    e.preventDefault();
    if (!_open.has(id)) {
      _open.add(id);
      _render(_folders);
    }
    const child = _treeEl.querySelector(`#kf-tree-children-${id} > .tree-item`);
    _focusItem(child ? Number(child.dataset.id) : id);
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault();
    if (hasChildren && _open.has(id)) {
      _open.delete(id);
      _render(_folders);
      _focusItem(id);
    } else {
      const parentId = item.parentElement?.dataset.parent;
      if (parentId) _focusItem(Number(parentId));
    }
  } else if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    const folder = _folders.find(entry => entry.id === id);
    if (!folder) return;
    _selectFolder(id);
  }
});

function _selectFolder(id) {
  const folder = _folders.find(entry => entry.id === id);
  if (!folder) return;
  _selected = id;
  _folderSelection = new Set([id]);
  _folderAnchor = id;
  _folderActionCurrentId = id === 1 ? null : id;
  _open.add(id);
  _emit('select', { folder });
  _renderAndFocus(id);
}

function _selectFolderRange(id) {
  const visible = [..._treeEl.querySelectorAll('.tree-item[role="treeitem"]')]
    .filter(node => node.getClientRects().length > 0);
  const anchor = _folderAnchor ?? id;
  const start = visible.findIndex(node => Number(node.dataset.id) === anchor);
  const end = visible.findIndex(node => Number(node.dataset.id) === id);
  if (start < 0 || end < 0) {
    _folderSelection = new Set([id]);
    _folderAnchor = id;
  } else {
    const [from, to] = start < end ? [start, end] : [end, start];
    _folderSelection = new Set(visible.slice(from, to + 1)
      .map(node => Number(node.dataset.id))
      .filter(folderId => folderId !== 1));
  }
  _setFolderActionCurrent(id);
  _render(_folders);
}

function _focusItem(id) {
  _focused = id;
  const actionCurrentChanged = _folderSelection.has(id) && _folderActionCurrentId !== id;
  if (actionCurrentChanged) {
    _folderActionCurrentId = id;
    _render(_folders);
  }
  _treeEl.querySelectorAll('.tree-item[role="treeitem"]').forEach(item => {
    item.tabIndex = Number(item.dataset.id) === id ? 0 : -1;
  });
  _treeEl.querySelector(`.tree-item[data-id="${id}"]`)?.focus();
}

function _renderAndFocus(id) {
  _focused = id;
  _render(_folders);
  _focusItem(id);
}

function _setFolderActionCurrent(id) {
  _folderActionCurrentId = _folderSelection.has(id)
    ? id
    : [..._folderSelection].pop() ?? null;
}

// ── Drag / drop to re-parent folders ───────────────────────────

let _dragId = null;

_treeEl.addEventListener('dragstart', e => {
  const item = e.target.closest('.tree-item[draggable="true"]');
  if (!item) return;
  _dragId = parseInt(item.dataset.id, 10);
  e.dataTransfer.effectAllowed = 'move';
  item.classList.add('dragging');
});

_treeEl.addEventListener('dragend', () => {
  _dragId = null;
  _treeEl.querySelectorAll('.drag-over, .dragging').forEach(el =>
    el.classList.remove('drag-over', 'dragging'));
});

_treeEl.addEventListener('dragover', e => {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const item = e.target.closest('.tree-item');
  if (!item) return;
  const targetId = parseInt(item.dataset.id, 10);
  if (targetId === _dragId) return;
  _treeEl.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
  item.classList.add('drag-over');
});

_treeEl.addEventListener('dragleave', e => {
  // Only clear if leaving the item entirely (not entering a child element)
  const item = e.target.closest('.tree-item');
  if (item && !item.contains(e.relatedTarget)) item.classList.remove('drag-over');
});

_treeEl.addEventListener('drop', async e => {
  e.preventDefault();
  _treeEl.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
  const item = e.target.closest('.tree-item');
  if (!item || _dragId == null) return;
  const targetId = parseInt(item.dataset.id, 10);
  if (targetId === _dragId) return;
  const dragFolder   = _folders.find(f => f.id === _dragId);
  const targetFolder = _folders.find(f => f.id === targetId);
  if (!dragFolder || !targetFolder) return;
  // Prevent dropping into own subtree
  if (targetFolder.path === dragFolder.path ||
      targetFolder.path.startsWith(dragFolder.path + '/')) return;
  try {
    await api.patchFolder(_dragId, { parent_id: targetId, expected_revision: dragFolder.revision });
    _open.add(targetId);
    await refresh();
    _emit('refresh', {});
  } catch (err) {
    await dialogs.alert('Move Folder Failed', err.message || 'Could not move folder.');
  }
});

// ── Folder CRUD ─────────────────────────────────────────────────

document.getElementById('btn-new-folder').addEventListener('click', async () => {
  const parentId = _selected ?? 1;
  const name = await dialogs.prompt('New Folder');
  if (!name) return;
  try {
    await api.createFolder(name, parentId);
    _open.add(parentId);
    await refresh();
    _emit('refresh', {});
  } catch (err) {
    await dialogs.alert('Create Folder Failed', err.message || 'Could not create folder.');
  }
});

async function _renameFolder(folder) {
  const newName = await dialogs.prompt('Rename Folder', folder.name);
  if (!newName || newName === folder.name) return;
  try {
    await api.patchFolder(folder.id, { name: newName, expected_revision: folder.revision });
    await refresh();
    _emit('refresh', {});
  } catch (err) {
    await dialogs.alert('Rename Folder Failed', err.message || 'Could not rename folder.');
  }
}

async function _moveFolder(folder) {
  await _moveFolders([folder]);
}

async function _moveFolders(folders) {
  if (!folders.length) return;
  const targetId = await dialogs.moveFolders(_folders, folders, folders.length);
  if (targetId == null) return;
  const failed = [];
  let moved = 0;
  for (const folder of folders) {
    try {
      await api.patchFolder(folder.id, { parent_id: targetId, expected_revision: folder.revision });
      moved += 1;
    } catch (err) {
      failed.push({ folder, message: err.message || 'Could not move folder.' });
    }
  }
  await refresh();
  _emit('refresh', {});
  await _showFolderOperationResult('moved', moved, folders.length, failed);
}

async function _deleteFolder(folder) {
  await _deleteFolders([folder]);
}

async function _copyFolders(folders) {
  if (!folders.length) return;
  const destinationId = await dialogs.copyFolders(_folders, folders, folders.length);
  if (destinationId == null) return;
  let result;
  try {
    result = await api.copyFolders(
      folders.map(folder => ({ id: folder.id, expected_revision: folder.revision })),
      destinationId,
    );
  } catch (err) {
    await dialogs.alert('Folder Copy Failed', err.message || 'Could not copy folders.');
    return;
  }
  await refresh();
  _emit('refresh', {});
  const failures = result.failed.map(item => ({
    folder: folders.find(folder => folder.id === item.id),
    message: item.error,
  }));
  await _showFolderOperationResult('copied', result.copied.length, folders.length, failures);
}

async function _deleteFolders(folders) {
  if (!folders.length) return;
  const names = folders.map(folder => `"${folder.name}"`).join(', ');
  const ok = await dialogs.confirm(
    `Delete ${folders.length === 1 ? 'Folder' : 'Folders'}`,
    `Delete ${names} and everything inside? This cannot be undone.`,
  );
  if (!ok) return;
  const active = _folders.find(folder => folder.id === _selected);
  const deletedPaths = [];
  const failed = [];
  let deleted = 0;
  for (const folder of folders) {
    try {
      await api.deleteFolder(folder.id, folder.revision, true);
      deleted += 1;
      deletedPaths.push(folder.path);
    } catch (err) {
      failed.push({ folder, message: err.message || 'Could not delete folder.' });
    }
  }
  await refresh();
  const deletedRoot = active && folders.find(folder =>
    deletedPaths.includes(folder.path) &&
    (active.path === folder.path || active.path.startsWith(`${folder.path}/`)),
  );
  if (deletedRoot) {
    const parent = _folders.find(folder => folder.id === deletedRoot.parent_id)
      || _folders.find(folder => folder.path === '/');
    if (parent) _selectFolder(parent.id);
  }
  _emit('refresh', {});
  await _showFolderOperationResult('deleted', deleted, folders.length, failed);
}

function _selectedFolderRoots() {
  const selected = _folders.filter(folder => _folderSelection.has(folder.id) && folder.id !== 1);
  return selected.filter(folder => !selected.some(parent =>
    parent.id !== folder.id && folder.path.startsWith(`${parent.path}/`),
  ));
}

async function _showFolderOperationResult(action, completed, total, failures) {
  if (!failures.length) return;
  const details = failures.map(({ folder, message }) =>
    `${folder?.name || 'Folder'}: ${message}`,
  ).join('\n');
  await dialogs.alert(
    'Some folders were not processed',
    `${completed} of ${total} folders ${action}.\n${details}`,
  );
}

// ── Public API ──────────────────────────────────────────────────

export async function refresh() {
  const folders = await api.listFolders();
  _render(folders);
  return folders;
}

export function select(folderId) {
  _selected = folderId;
  _folderSelection = new Set(folderId === 1 ? [] : [folderId]);
  _folderAnchor = folderId;
  _folderActionCurrentId = folderId === 1 ? null : folderId;
  if (_folders.length) _render(_folders);
}

export function getFolders() { return _folders; }
export function getSelected() { return _selected; }



function _iconBtn(action, title, svg) {
  return `<button class="icon-btn" type="button" data-btn="${action}" title="${title}" aria-label="${title}">${svg}</button>`;
}

function _esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
