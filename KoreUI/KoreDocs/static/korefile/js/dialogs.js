/**
 * dialogs.js - KoreFile adapters for the shared UIElements dialog host.
 */

import {
  kcuiAlert,
  kcuiConfirm,
  kcuiForm,
  kcuiPrompt,
} from '/ui-elements/assets/js/dialogs.js';

export function prompt(title, initial = '') {
  return kcuiPrompt(title, { initial });
}

export function confirm(title, message) {
  return kcuiConfirm(title, message, { confirmLabel: 'Delete' });
}

export function alert(title, message) {
  return kcuiAlert(title, message);
}

/** Show the new-file form.  Returns { name, ext } or null. */
export async function newFile() {
  const values = await kcuiForm('New File', {
    confirmLabel: 'Create',
    fields: [
      {
        name:        'name',
        label:       'Name',
        placeholder: 'my-doc.koredoc',
        required:    true,
      },
      {
        name:  'ext',
        label: 'Type',
        type:  'select',
        value: 'koredoc',
        options: [
          { value: 'koredoc',   label: 'KoreDoc (.koredoc)' },
          { value: 'koresheet', label: 'KoreSheet (.koresheet)' },
          { value: 'korediag',  label: 'KoreDiag (.korediag)' },
        ],
      },
    ],
  });
  if (!values) return null;

  const ext = values.ext;
  const name = values.name.endsWith(`.${ext}`) ? values.name : `${values.name}.${ext}`;
  return { name, ext };
}

/** Show the move-to-folder form.  Returns the selected folder id or null. */
export function moveFile(folders, currentFolderId) {
  return _showMoveDialog(
    'Move File',
    folders.filter(folder => folder.id !== currentFolderId),
    'Move',
  );
}

/** Show the move form for a folder, excluding that folder and its descendants. */
export function moveFolder(folders, folderId, folderPath) {
  const available = folders.filter(folder =>
    folder.id !== folderId && !folder.path.startsWith(`${folderPath}/`),
  );
  return _showMoveDialog('Move Folder', available, 'Move');
}

export function copyFolder(folders, folderId, folderPath) {
  const available = folders.filter(folder =>
    folder.id !== folderId && !folder.path.startsWith(`${folderPath}/`),
  );
  return _showMoveDialog('Copy Folder', available, 'Copy');
}

export function moveFiles(folders, sourceFolderIds, count) {
  const sourceIds = new Set(sourceFolderIds);
  return _showMoveDialog(`Move ${count} ${count === 1 ? 'File' : 'Files'}`,
    folders.filter(folder => !sourceIds.has(folder.id)), 'Move');
}

export function copyFiles(folders, count) {
  return _showMoveDialog(`Copy ${count} ${count === 1 ? 'File' : 'Files'}`, folders, 'Copy');
}

export function moveFolders(folders, selectedFolders, count) {
  return _showFolderTransferDialog('Move', folders, selectedFolders, count);
}

export function copyFolders(folders, selectedFolders, count) {
  return _showFolderTransferDialog('Copy', folders, selectedFolders, count);
}

function _showFolderTransferDialog(action, folders, selectedFolders, count) {
  const paths = selectedFolders.map(folder => folder.path);
  const currentParents = new Set(selectedFolders.map(folder => folder.parent_id));
  const available = folders.filter(folder =>
    !paths.some(path => folder.path === path || folder.path.startsWith(`${path}/`)) &&
    (action !== 'Move' || !currentParents.has(folder.id)),
  );
  return _showMoveDialog(`${action} ${count} ${count === 1 ? 'Folder' : 'Folders'}`, available, action);
}

async function _showMoveDialog(title, folders, action) {
  if (!folders.length) {
    await kcuiAlert(title, 'There are no available destination folders.');
    return null;
  }
  const values = await kcuiForm(title, {
    confirmLabel: action,
    fields: [{
      name:  'folderId',
      label: 'Destination folder',
      type:  'select',
      options: folders.map(folder => ({
        value: String(folder.id),
        label: folder.path === '/' ? '/ (Root)' : folder.path,
      })),
    }],
  });
  return values ? Number.parseInt(values.folderId, 10) : null;
}
