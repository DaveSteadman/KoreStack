/**
 * tree.js — Folder tree panel for KoreFile explorer.
 *
 * Emits:
 *   tree:select   { folder }   — user clicked a folder
 *   tree:refresh  {}           — tree data changed (folder added/deleted)
 */

import * as api     from './api.js';
import * as dialogs from './dialogs.js';
import { CHEVRON_SVG, EDIT_SVG, MOVE_SVG, TRASH_SVG } from '/ui-elements/assets/js/svg_icons.js';

const _treeEl = document.getElementById('kf-tree');

// ── State ───────────────────────────────────────────────────────

let _folders  = [];        // flat list from server
let _selected = null;      // currently selected folder id
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
  const isSelected = folder.id === _selected;
  const label = folder.path === '/' ? 'Root' : folder.name;

  const chevron = hasKids
    ? `<span class="tree-toggle">${CHEVRON_SVG}</span>`
    : `<span class="tree-toggle" style="visibility:hidden">${CHEVRON_SVG}</span>`;

  const actions = folder.id !== 1
    ? `<span class="tree-actions">
        ${_iconBtn('rename-folder', 'Rename', EDIT_SVG)}
        ${_iconBtn('move-folder',   'Move to…', MOVE_SVG)}
        ${_iconBtn('delete-folder', 'Delete', TRASH_SVG)}
       </span>`
    : '';

  const html = `
    <div class="tree-item tree-indent ${isSelected ? 'selected' : ''} ${isOpen ? 'open' : ''}"
         data-id="${folder.id}" data-depth="${depth}" style="--indent:${depth}"
         ${folder.id !== 1 ? 'draggable="true"' : ''}>
      ${chevron}
      <span class="tree-label" title="${_esc(folder.path)}">${_esc(label)}</span>
      ${actions}
    </div>
    <div class="tree-children ${isOpen ? 'open' : ''}" data-parent="${folder.id}">
      ${kids.map(c => _renderNode(c, children, depth + 1)).join('')}
    </div>`;
  return html;
}

function _render(folders) {
  _folders = folders;
  const { children, roots } = _buildTree(folders);
  // Auto-expand root-level folders on first load
  if (_open.size === 0) roots.forEach(r => _open.add(r.id));
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
  if (e.target.closest('[data-btn="delete-folder"]')) {
    e.stopPropagation();
    _deleteFolder(folder);
    return;
  }
  // Toggle open/closed
  if (e.target.closest('.tree-toggle')) {
    _open.has(id) ? _open.delete(id) : _open.add(id);
    refresh();
    return;
  }

  // Select
  _selected = id;
  _open.add(id);
  _emit('select', { folder });
  refresh();
});

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
    alert('Could not move folder: ' + err.message);
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
    alert('Could not create folder: ' + err.message);
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
    alert('Could not rename folder: ' + err.message);
  }
}

async function _moveFolder(folder) {
  const targetId = await dialogs.moveFolder(_folders, folder.id, folder.path);
  if (targetId == null) return;
  try {
    await api.patchFolder(folder.id, { parent_id: targetId, expected_revision: folder.revision });
    await refresh();
    _emit('refresh', {});
  } catch (err) {
    alert('Could not move folder: ' + err.message);
  }
}

async function _deleteFolder(folder) {
  const ok = await dialogs.confirm(
    'Delete Folder',
    `Delete "${folder.name}"? This will fail if the folder has files or sub-folders.`,
  );
  if (!ok) return;
  try {
    await api.deleteFolder(folder.id, folder.revision);
    if (_selected === folder.id) _selected = 1;
    await refresh();
    _emit('refresh', {});
  } catch (err) {
    alert('Could not delete folder: ' + err.message);
  }
}

// ── Public API ──────────────────────────────────────────────────

export async function refresh() {
  const folders = await api.listFolders();
  _render(folders);
  return folders;
}

export function select(folderId) {
  _selected = folderId;
}

export function getFolders() { return _folders; }
export function getSelected() { return _selected; }



function _iconBtn(action, title, svg) {
  return `<button class="icon-btn" data-btn="${action}" title="${title}">${svg}</button>`;
}

function _esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
