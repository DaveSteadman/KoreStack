import { state, api } from './state.js';

const treeHost = document.getElementById('code-tree');
const treeStatus = document.getElementById('tree-status');
const refreshTreeButton = document.getElementById('btn-refresh-tree');
const rootSelect = document.getElementById('root-select');
const CUSTOM_ROOT_VALUE = '__custom__';

let _openFile = null;
let _onRootChanged = null;
let _settingRoot = false;
let _currentRootValue = '';
let _rootPicker = null;

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

function _currentRootPath() {
  if (!rootSelect) return '';
  return String(rootSelect.dataset.currentRoot || rootSelect.value || '').trim();
}

function _syncRootSelectVisualState() {
  if (!rootSelect) return;
  rootSelect.classList.toggle('is-custom-path', rootSelect.value === CUSTOM_ROOT_VALUE);
}

async function _invalidateDir(dirPath) {
  state.tree.delete(dirPath);
  await ensureDirectory(dirPath);
}

function _askName(title, placeholder = 'Name') {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'name-prompt-overlay';
    overlay.innerHTML = `
      <div class="name-prompt-dialog" role="dialog" aria-modal="true" aria-label="${title}">
        <div class="name-prompt-title">${title}</div>
        <input class="name-prompt-input" type="text" spellcheck="false" placeholder="${placeholder}" />
        <div class="name-prompt-actions">
          <button type="button" class="kcui-tag kcui-tag--muted name-prompt-cancel">Cancel</button>
          <button type="button" class="kcui-tag kcui-tag--accent name-prompt-ok">Create</button>
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    const input = overlay.querySelector('.name-prompt-input');
    const cancelBtn = overlay.querySelector('.name-prompt-cancel');
    const okBtn = overlay.querySelector('.name-prompt-ok');

    let done = false;
    const close = (value = null) => {
      if (done) return;
      done = true;
      overlay.remove();
      resolve(value);
    };

    cancelBtn.addEventListener('click', () => close(null));
    okBtn.addEventListener('click', () => {
      const value = String(input.value || '').trim();
      if (!value) return;
      close(value);
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        okBtn.click();
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        close(null);
      }
    });
    overlay.addEventListener('click', (event) => {
      if (event.target === overlay) close(null);
    });

    input.focus();
    input.select();
  });
}

async function _createFile(dirPath) {
  const name = await _askName('New file', 'File name (e.g. notes.md)');
  if (!name) return;
  const newPath = dirPath ? `${dirPath}/${name}` : name;
  try {
    await api(`/api/file?path=${encodeURIComponent(newPath)}`, { method: 'POST' });
    state.expanded.add(dirPath);
    await _invalidateDir(dirPath);
    renderTree();
    treeStatus.textContent = `Created ${newPath}`;
  } catch (err) {
    await window.kcuiAlert('Create File Failed', err.message || 'Could not create file.');
  }
}

async function _createDir(dirPath) {
  const name = await _askName('New folder', 'Folder name');
  if (!name) return;
  const newPath = dirPath ? `${dirPath}/${name}` : name;
  try {
    await api(`/api/dir?path=${encodeURIComponent(newPath)}`, { method: 'POST' });
    state.expanded.add(dirPath);
    await _invalidateDir(dirPath);
    renderTree();
  } catch (err) {
    await window.kcuiAlert('Create Folder Failed', err.message || 'Could not create folder.');
  }
}

async function _deleteFileItem(filePath, fileName) {
  const confirmed = await window.kcuiConfirm(
    'Delete File',
    `Delete "${fileName}"?`,
    { confirmLabel: 'Delete' },
  );
  if (!confirmed) return;
  try {
    await api(`/api/file?path=${encodeURIComponent(filePath)}`, { method: 'DELETE' });
    await _invalidateDir(_parentPath(filePath));
    renderTree();
  } catch (err) {
    await window.kcuiAlert('Delete File Failed', err.message || 'Could not delete file.');
  }
}

async function _deleteDirItem(dirPath, dirLabel) {
  const confirmed = await window.kcuiConfirm(
    'Delete Folder',
    `Delete "${dirLabel}"?\n\nThe folder must already be empty.`,
    { confirmLabel: 'Delete' },
  );
  if (!confirmed) return;
  try {
    await api(`/api/dir?path=${encodeURIComponent(dirPath)}`, { method: 'DELETE' });
    state.expanded.delete(dirPath);
    await _invalidateDir(_parentPath(dirPath));
    renderTree();
  } catch (err) {
    await window.kcuiAlert('Delete Folder Failed', err.message || 'Could not delete folder.');
  }
}

export function initExplorer({ openFile, onRootChanged = null }) {
  _openFile = openFile;
  _onRootChanged = onRootChanged;
  refreshTreeButton.addEventListener('click', () => {
    void refreshTree();
  });
  rootSelect?.addEventListener('change', () => {
    if (_settingRoot) return;
    if (rootSelect.value === CUSTOM_ROOT_VALUE) {
      void openCustomRootPicker(_currentRootValue);
      return;
    }
    void switchRoot(rootSelect.value);
  });
}

function ensureRootPicker() {
  if (_rootPicker) return _rootPicker;

  const overlay = document.createElement('div');
  overlay.id = 'root-picker-overlay';
  overlay.hidden = true;
  overlay.innerHTML = `
    <div id="root-picker-dialog" role="dialog" aria-modal="true" aria-label="Select root folder">
      <div class="root-picker-title">Select Root Folder</div>
      <div class="root-picker-row">
        <button id="root-picker-up" class="kcui-tag kcui-tag--muted" type="button">Up</button>
        <input id="root-picker-path" type="text" spellcheck="false" placeholder="Absolute folder path" />
        <button id="root-picker-go" class="kcui-tag kcui-tag--muted" type="button">Go</button>
      </div>
      <div id="root-picker-list" class="root-picker-list"></div>
      <div class="root-picker-actions">
        <button id="root-picker-cancel" class="kcui-tag kcui-tag--muted" type="button">Cancel</button>
        <button id="root-picker-select" class="kcui-tag kcui-tag--accent" type="button">Use This Folder</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  const picker = {
    overlay,
    pathInput: overlay.querySelector('#root-picker-path'),
    list: overlay.querySelector('#root-picker-list'),
    upBtn: overlay.querySelector('#root-picker-up'),
    goBtn: overlay.querySelector('#root-picker-go'),
    cancelBtn: overlay.querySelector('#root-picker-cancel'),
    selectBtn: overlay.querySelector('#root-picker-select'),
    currentPath: '',
    parentPath: null,
  };

  const close = () => {
    picker.overlay.hidden = true;
    void loadRootOptions();
  };

  picker.cancelBtn.addEventListener('click', close);
  picker.overlay.addEventListener('click', (event) => {
    if (event.target === picker.overlay) {
      close();
    }
  });
  picker.upBtn.addEventListener('click', () => {
    if (!picker.parentPath) return;
    void loadBrowsePath(picker.parentPath);
  });
  picker.goBtn.addEventListener('click', () => {
    const target = picker.pathInput.value.trim();
    if (!target) return;
    void loadBrowsePath(target);
  });
  picker.pathInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      const target = picker.pathInput.value.trim();
      if (target) void loadBrowsePath(target);
    }
  });
  picker.selectBtn.addEventListener('click', async () => {
    const target = picker.pathInput.value.trim();
    if (!target) return;
    picker.overlay.hidden = true;
    await switchRoot(target);
  });

  _rootPicker = picker;
  return picker;
}

async function loadBrowsePath(path = null) {
  const picker = ensureRootPicker();
  picker.list.textContent = 'Loading folders…';
  const query = path ? `?path=${encodeURIComponent(path)}` : '';
  try {
    const payload = await api(`/api/root-browse${query}`);
    picker.currentPath = payload.path || '';
    picker.parentPath = payload.parent || null;
    picker.pathInput.value = picker.currentPath;
    picker.upBtn.disabled = !picker.parentPath;

    picker.list.innerHTML = '';
    const directories = Array.isArray(payload.directories) ? payload.directories : [];
    if (!directories.length) {
      const empty = document.createElement('div');
      empty.className = 'root-picker-empty';
      empty.textContent = 'No subfolders available.';
      picker.list.appendChild(empty);
      return;
    }
    for (const directory of directories) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'root-picker-item';
      btn.textContent = directory.name || directory.path;
      btn.title = directory.path || '';
      btn.addEventListener('click', () => {
        void loadBrowsePath(directory.path);
      });
      picker.list.appendChild(btn);
    }
  } catch (err) {
    picker.list.innerHTML = '';
    const issue = document.createElement('div');
    issue.className = 'root-picker-empty';
    issue.textContent = `Browse failed: ${err.message || err}`;
    picker.list.appendChild(issue);
  }
}

async function openCustomRootPicker(initialPath) {
  const picker = ensureRootPicker();
  picker.overlay.hidden = false;
  const target = initialPath && initialPath !== CUSTOM_ROOT_VALUE ? initialPath : null;
  await loadBrowsePath(target);
}

async function loadRootOptions() {
  if (!rootSelect) return;
  try {
    const payload = await api('/api/root-options');
    const options = Array.isArray(payload?.options) ? payload.options : [];
    const current = typeof payload?.current === 'string' ? payload.current : '';
    _currentRootValue = current;
    _settingRoot = true;
    rootSelect.innerHTML = '';
    for (const option of options) {
      const opt = document.createElement('option');
      opt.value = option.value || '';
      opt.textContent = option.path || option.value || option.label || 'workspace';
      opt.title       = option.path || option.value || option.label || 'workspace';
      rootSelect.appendChild(opt);
    }
    const customOpt = document.createElement('option');
    customOpt.value = CUSTOM_ROOT_VALUE;
    customOpt.textContent = 'Custom path…';
    customOpt.style.color = 'var(--accent)';
    customOpt.style.fontWeight = '600';
    rootSelect.appendChild(customOpt);
    rootSelect.value = current;
    rootSelect.title = current;
    rootSelect.dataset.currentRoot = current;
    rootSelect.disabled = false;
    _syncRootSelectVisualState();
  } catch {
    rootSelect.innerHTML = '<option value="">root unavailable</option>';
    rootSelect.title = 'root unavailable';
    rootSelect.dataset.currentRoot = '';
    rootSelect.disabled = true;
    _syncRootSelectVisualState();
  } finally {
    _settingRoot = false;
  }
}

export async function switchRoot(rootValue) {
  if (_settingRoot) return;
  treeStatus.textContent = 'Switching root…';
  try {
    _settingRoot = true;
    await api('/api/root', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root: rootValue || '' }),
    });
    state.tree.clear();
    state.expanded.clear();
    _onRootChanged?.();
    await loadRootOptions();
    await refreshTree();
    treeStatus.textContent = 'Root switched';
  } catch (err) {
    treeStatus.textContent = `Root switch failed: ${err.message || err}`;
    await loadRootOptions();
  } finally {
    _settingRoot = false;
  }
}

export async function refreshTree() {
  treeStatus.textContent = 'Loading workspace\u2026';
  await loadRootOptions();
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
  if (rootSelect && listing.root) {
    rootSelect.title = listing.root;
    rootSelect.dataset.currentRoot = listing.root;
    _syncRootSelectVisualState();
  }
  return listing;
}

export { _currentRootPath };

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
  if (isRoot) {
    row.classList.add('is-root');
  }
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
