import { state, api } from './state.js';

const treeHost = document.getElementById('code-tree');
const treeStatus = document.getElementById('tree-status');
const rootLabel = document.getElementById('root-label');
const refreshTreeButton = document.getElementById('btn-refresh-tree');

let _openFile = null;

// ── SVG icons ────────────────────────────────────────────────────────────────

const SVG_NEW_FILE = `<svg viewBox="0 0 20 20" fill="none" width="11" height="11">
  <path d="M5 2h7l4 4v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1z" stroke="currentColor" stroke-width="1.5"/>
  <line x1="8" y1="11" x2="12" y2="11" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
  <line x1="10" y1="9" x2="10" y2="13" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
</svg>`;

const SVG_NEW_DIR = `<svg viewBox="0 0 20 20" fill="none" width="11" height="11">
  <path d="M2 5a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5z" stroke="currentColor" stroke-width="1.5"/>
  <line x1="10" y1="8" x2="10" y2="14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
  <line x1="7" y1="11" x2="13" y2="11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
</svg>`;

const SVG_DELETE = `<svg viewBox="0 0 20 20" fill="none" width="11" height="11">
  <path d="M3 6h14M8 6V4h4v2M5 6l1 11h8l1-11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
</svg>`;

// ── Action helpers ────────────────────────────────────────────────────────────

function _actionBtn(icon, title) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'tree-action-btn';
  btn.title = title;
  btn.innerHTML = icon;
  return btn;
}

function _parentPath(itemPath) {
  const slash = itemPath.lastIndexOf('/');
  return slash < 0 ? '' : itemPath.slice(0, slash);
}

async function _invalidateDir(dirPath) {
  state.tree.delete(dirPath);
  await ensureDirectory(dirPath);
}

async function _createFile(dirPath) {
  const name = window.prompt('New file name:');
  if (!name?.trim()) return;
  const newPath = dirPath ? `${dirPath}/${name.trim()}` : name.trim();
  try {
    await api(`/api/file?path=${encodeURIComponent(newPath)}`, { method: 'POST' });
    state.expanded.add(dirPath);
    await _invalidateDir(dirPath);
    renderTree();
    void _openFile(newPath);
  } catch (err) {
    alert('Could not create file: ' + err.message);
  }
}

async function _createDir(dirPath) {
  const name = window.prompt('New folder name:');
  if (!name?.trim()) return;
  const newPath = dirPath ? `${dirPath}/${name.trim()}` : name.trim();
  try {
    await api(`/api/dir?path=${encodeURIComponent(newPath)}`, { method: 'POST' });
    state.expanded.add(dirPath);
    await _invalidateDir(dirPath);
    renderTree();
  } catch (err) {
    alert('Could not create folder: ' + err.message);
  }
}

async function _deleteFileItem(filePath, fileName) {
  if (!window.confirm(`Delete "${fileName}"?`)) return;
  try {
    await api(`/api/file?path=${encodeURIComponent(filePath)}`, { method: 'DELETE' });
    await _invalidateDir(_parentPath(filePath));
    renderTree();
  } catch (err) {
    alert('Could not delete file: ' + err.message);
  }
}

async function _deleteDirItem(dirPath, dirLabel) {
  if (!window.confirm(`Delete "${dirLabel}"? The folder must be empty.`)) return;
  try {
    await api(`/api/dir?path=${encodeURIComponent(dirPath)}`, { method: 'DELETE' });
    state.expanded.delete(dirPath);
    await _invalidateDir(_parentPath(dirPath));
    renderTree();
  } catch (err) {
    alert('Could not delete folder: ' + err.message);
  }
}

export function initExplorer({ openFile }) {
  _openFile = openFile;
  refreshTreeButton.addEventListener('click', () => {
    void refreshTree();
  });
}

export async function refreshTree() {
  treeStatus.textContent = 'Loading workspace\u2026';
  const expandedPaths = [...state.expanded].filter(Boolean);
  state.tree.clear();
  try {
    await ensureDirectory('');
    for (const path of expandedPaths) {
      try {
        await ensureDirectory(path);
      } catch {
        state.expanded.delete(path);
      }
    }
    if (state.activePath) {
      try {
        await expandAncestors(state.activePath);
      } catch {
        // If the active file path no longer exists, keep refresh successful.
      }
    }
    treeStatus.textContent = 'Workspace loaded';
    renderTree();
  } catch (error) {
    treeStatus.textContent = String(error.message || error);
  }
}

export async function ensureDirectory(path) {
  if (state.tree.has(path)) {
    return state.tree.get(path);
  }
  const listing = await api(`/api/tree?path=${encodeURIComponent(path)}`);
  state.tree.set(path, listing);
  state.root = listing.root;
  rootLabel.textContent = listing.name || 'KoreStack';
  const rootPath = document.getElementById('root-path');
  if (rootPath) {
    rootPath.textContent = listing.root;
    rootPath.title = listing.root;
  }
  return listing;
}

export function renderTree() {
  treeHost.innerHTML = '';
  const rootListing = state.tree.get('');
  if (!rootListing) {
    return;
  }
  const rootFragment = document.createDocumentFragment();
  rootFragment.appendChild(renderDirectory('', rootListing.name || 'KoreStack', 0, true));
  treeHost.appendChild(rootFragment);
}

function renderDirectory(path, label, depth, isRoot = false) {
  const container = document.createElement('div');

  const row = document.createElement('div');
  row.className = 'tree-row is-dir';
  row.setAttribute('role', 'button');
  row.setAttribute('tabindex', '0');
  row.style.setProperty('--depth', String(depth));
  if (state.activePath && isAncestorDirectory(path, state.activePath)) {
    row.classList.add('is-active');
  }

  const caret = document.createElement('span');
  caret.className = 'tree-caret';
  caret.textContent = state.expanded.has(path) ? '\u25be' : '\u25b8';

  const labelSpan = document.createElement('span');
  labelSpan.className = 'tree-label';
  labelSpan.textContent = label;

  const actions = document.createElement('span');
  actions.className = 'tree-actions';

  const newFileBtn = _actionBtn(SVG_NEW_FILE, 'New file here');
  const newDirBtn  = _actionBtn(SVG_NEW_DIR,  'New folder here');
  newFileBtn.addEventListener('click', (e) => { e.stopPropagation(); void _createFile(path); });
  newDirBtn.addEventListener('click',  (e) => { e.stopPropagation(); void _createDir(path); });
  actions.appendChild(newFileBtn);
  actions.appendChild(newDirBtn);
  if (!isRoot) {
    const delDirBtn = _actionBtn(SVG_DELETE, 'Delete folder');
    delDirBtn.addEventListener('click', (e) => { e.stopPropagation(); void _deleteDirItem(path, label); });
    actions.appendChild(delDirBtn);
  }

  row.appendChild(caret);
  row.appendChild(labelSpan);
  row.appendChild(actions);

  const toggleExpand = async () => {
    if (state.expanded.has(path)) {
      state.expanded.delete(path);
      renderTree();
      return;
    }
    state.expanded.add(path);
    await ensureDirectory(path);
    renderTree();
  };

  row.addEventListener('click', (e) => {
    if (e.target.closest('.tree-actions')) return;
    void toggleExpand();
  });
  row.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); void toggleExpand(); }
  });

  container.appendChild(row);

  if (!state.expanded.has(path)) {
    return container;
  }

  const childrenHost = document.createElement('div');
  childrenHost.className = 'tree-children';
  const listing = state.tree.get(path);
  for (const directory of listing?.directories || []) {
    childrenHost.appendChild(renderDirectory(directory.path, directory.name, depth + 1));
  }
  for (const file of listing?.files || []) {
    childrenHost.appendChild(renderFile(file, depth + 1));
  }
  container.appendChild(childrenHost);
  return container;
}

function renderFile(file, depth) {
  const row = document.createElement('div');
  row.className = 'tree-row is-file';
  row.setAttribute('role', 'button');
  row.setAttribute('tabindex', '0');
  row.style.setProperty('--depth', String(depth));
  if (file.path === state.activePath) {
    row.classList.add('is-active');
  }
  row.title = file.path;

  const caret = document.createElement('span');
  caret.className = 'tree-caret';
  caret.textContent = '\u2022';

  const labelSpan = document.createElement('span');
  labelSpan.className = 'tree-label';
  labelSpan.textContent = file.name;

  const actions = document.createElement('span');
  actions.className = 'tree-actions';
  const delBtn = _actionBtn(SVG_DELETE, 'Delete file');
  delBtn.addEventListener('click', (e) => { e.stopPropagation(); void _deleteFileItem(file.path, file.name); });
  actions.appendChild(delBtn);

  row.appendChild(caret);
  row.appendChild(labelSpan);
  row.appendChild(actions);

  row.addEventListener('click', (e) => {
    if (e.target.closest('.tree-actions')) return;
    void _openFile(file.path);
  });
  row.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); void _openFile(file.path); }
  });

  return row;
}

export async function expandAncestors(path) {
  const segments = path.split('/');
  const directories = [];
  for (let index = 0; index < segments.length - 1; index += 1) {
    directories.push(segments.slice(0, index + 1).join('/'));
  }
  for (const directory of directories) {
    state.expanded.add(directory);
    await ensureDirectory(directory);
  }
}

export function isAncestorDirectory(directoryPath, filePath) {
  if (!directoryPath) {
    return true;
  }
  return filePath === directoryPath || filePath.startsWith(`${directoryPath}/`);
}
