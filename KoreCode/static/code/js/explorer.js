import { state, api } from './state.js';

const treeHost = document.getElementById('code-tree');
const treeStatus = document.getElementById('tree-status');
const rootLabel = document.getElementById('root-label');
const refreshTreeButton = document.getElementById('btn-refresh-tree');

let _openFile = null;

export function initExplorer({ openFile }) {
  _openFile = openFile;
  refreshTreeButton.addEventListener('click', () => {
    void refreshTree();
  });
}

export async function refreshTree() {
  treeStatus.textContent = 'Loading workspace\u2026';
  state.tree.clear();
  try {
    await ensureDirectory('');
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
  const row = document.createElement('button');
  row.type = 'button';
  row.className = 'tree-row is-dir';
  row.style.setProperty('--depth', String(depth));
  if (state.activePath && isAncestorDirectory(path, state.activePath)) {
    row.classList.add('is-active');
  }
  row.innerHTML = `<span class="tree-caret">${state.expanded.has(path) ? '\u25be' : '\u25b8'}</span><span class="tree-label"></span>`;
  row.querySelector('.tree-label').textContent = label;
  row.addEventListener('click', async () => {
    if (state.expanded.has(path)) {
      state.expanded.delete(path);
      renderTree();
      return;
    }
    state.expanded.add(path);
    await ensureDirectory(path);
    renderTree();
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
    const fileButton = document.createElement('button');
    fileButton.type = 'button';
    fileButton.className = 'tree-row is-file';
    fileButton.style.setProperty('--depth', String(depth + 1));
    if (file.path === state.activePath) {
      fileButton.classList.add('is-active');
    }
    fileButton.innerHTML = '<span class="tree-caret">\u2022</span><span class="tree-label"></span>';
    fileButton.querySelector('.tree-label').textContent = file.name;
    fileButton.title = file.path;
    fileButton.addEventListener('click', () => {
      void _openFile(file.path);
    });
    childrenHost.appendChild(fileButton);
  }
  container.appendChild(childrenHost);
  return container;
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
