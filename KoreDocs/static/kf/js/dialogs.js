/**
 * dialogs.js — Reusable dialog helpers for KoreFile explorer.
 */

// ── Generic prompt ─────────────────────────────────────────────

const _dlgPrompt = document.getElementById('dlg-prompt');
const _promptTitle = document.getElementById('dlg-prompt-title');
const _promptInput = document.getElementById('dlg-prompt-input');
const _promptOk    = document.getElementById('dlg-prompt-ok');
const _promptCancel= document.getElementById('dlg-prompt-cancel');

/** Show a simple prompt dialog.  Returns the entered string or null. */
export function prompt(title, initial = '') {
  return new Promise(resolve => {
    _promptTitle.textContent = title;
    _promptInput.value = initial;
    _dlgPrompt.showModal();
    _promptInput.select();

    const finish = val => {
      _dlgPrompt.close();
      _promptOk.removeEventListener('click', onOk);
      _promptCancel.removeEventListener('click', onCancel);
      _dlgPrompt.removeEventListener('close', onClose);
      resolve(val);
    };
    const onOk     = () => finish(_promptInput.value.trim() || null);
    const onCancel = () => finish(null);
    const onClose  = () => finish(null);

    _promptOk.addEventListener('click', onOk,     { once: true });
    _promptCancel.addEventListener('click', onCancel, { once: true });
    _dlgPrompt.addEventListener('close', onClose,  { once: true });
  });
}

// ── Confirm / delete ────────────────────────────────────────────

const _dlgConfirm = document.getElementById('dlg-confirm');
const _confirmTitle = document.getElementById('dlg-confirm-title');
const _confirmMsg   = document.getElementById('dlg-confirm-msg');
const _confirmOk    = document.getElementById('dlg-confirm-ok');
const _confirmCancel= document.getElementById('dlg-confirm-cancel');

export function confirm(title, message) {
  return new Promise(resolve => {
    _confirmTitle.textContent = title;
    _confirmMsg.textContent   = message;
    _dlgConfirm.showModal();

    const finish = val => {
      _dlgConfirm.close();
      _confirmOk.removeEventListener('click', onOk);
      _confirmCancel.removeEventListener('click', onCancel);
      _dlgConfirm.removeEventListener('close', onClose);
      resolve(val);
    };
    const onOk     = () => finish(true);
    const onCancel = () => finish(false);
    const onClose  = () => finish(false);

    _confirmOk.addEventListener('click', onOk,     { once: true });
    _confirmCancel.addEventListener('click', onCancel, { once: true });
    _dlgConfirm.addEventListener('close', onClose,  { once: true });
  });
}

// ── New file ───────────────────────────────────────────────────

const _dlgNewFile    = document.getElementById('dlg-new-file');
const _newFileName   = document.getElementById('new-file-name');
const _newFileType   = document.getElementById('new-file-type');
const _newFileOk     = document.getElementById('new-file-ok');
const _newFileCancel = document.getElementById('new-file-cancel');

/** Show the new-file dialog.  Returns { name, ext } or null. */
export function newFile() {
  return new Promise(resolve => {
    _newFileName.value = '';
    _dlgNewFile.showModal();
    _newFileName.focus();

    const finish = val => {
      _dlgNewFile.close();
      _newFileOk.removeEventListener('click', onOk);
      _newFileCancel.removeEventListener('click', onCancel);
      _dlgNewFile.removeEventListener('close', onClose);
      resolve(val);
    };
    const onOk = () => {
      let name = _newFileName.value.trim();
      const ext  = _newFileType.value;
      if (!name) { _newFileName.focus(); return; }
      if (!name.endsWith('.' + ext)) name = name + '.' + ext;
      finish({ name, ext });
    };
    const onCancel = () => finish(null);
    const onClose  = () => finish(null);

    _newFileOk.addEventListener('click', onOk,     { once: true });
    _newFileCancel.addEventListener('click', onCancel, { once: true });
    _dlgNewFile.addEventListener('close', onClose,  { once: true });
  });
}

// ── Move to folder ─────────────────────────────────────────────

const _dlgMove        = document.getElementById('dlg-move');
const _dlgMoveTitle   = document.getElementById('dlg-move-title');
const _moveFolderSel  = document.getElementById('move-folder-select');
const _moveOk         = document.getElementById('move-ok');
const _moveCancel     = document.getElementById('move-cancel');

/** Show the move-to-folder dialog.  Returns the selected folder id or null. */
export function moveFile(folders, currentFolderId) {
  _dlgMoveTitle.textContent = 'Move File';
  return _showMoveDialog(folders, currentFolderId);
}

/** Show the move dialog for a folder.  Excludes the folder and all its descendants. */
export function moveFolder(folders, folderId, folderPath) {
  _dlgMoveTitle.textContent = 'Move Folder';
  const filtered = folders.filter(
    f => f.id !== folderId && !f.path.startsWith(folderPath + '/'),
  );
  return _showMoveDialog(filtered, null);
}

function _showMoveDialog(folders, currentFolderId) {
  return new Promise(resolve => {
    _moveFolderSel.innerHTML = '';
    folders
      .filter(f => currentFolderId == null || f.id !== currentFolderId)
      .forEach(f => {
        const option = document.createElement('option');
        option.value = String(f.id);
        option.textContent = f.path === '/' ? '/ (Root)' : f.path;
        _moveFolderSel.appendChild(option);
      });
    _dlgMove.showModal();

    const finish = val => {
      _dlgMove.close();
      _moveOk.removeEventListener('click', onOk);
      _moveCancel.removeEventListener('click', onCancel);
      _dlgMove.removeEventListener('close', onClose);
      resolve(val);
    };
    const onOk     = () => finish(parseInt(_moveFolderSel.value, 10));
    const onCancel = () => finish(null);
    const onClose  = () => finish(null);

    _moveOk.addEventListener('click', onOk,     { once: true });
    _moveCancel.addEventListener('click', onCancel, { once: true });
    _dlgMove.addEventListener('close', onClose,  { once: true });
  });
}
