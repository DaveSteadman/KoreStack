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
  return _showMoveDialog('Move File', folders, currentFolderId);
}

/** Show the move form for a folder, excluding that folder and its descendants. */
export function moveFolder(folders, folderId, folderPath) {
  const available = folders.filter(folder =>
    folder.id !== folderId && !folder.path.startsWith(`${folderPath}/`),
  );
  return _showMoveDialog('Move Folder', available, null);
}

async function _showMoveDialog(title, folders, currentFolderId) {
  const available = folders.filter(folder => currentFolderId == null || folder.id !== currentFolderId);
  const values = await kcuiForm(title, {
    confirmLabel: 'Move',
    fields: [{
      name:  'folderId',
      label: 'Destination folder',
      type:  'select',
      options: available.map(folder => ({
        value: String(folder.id),
        label: folder.path === '/' ? '/ (Root)' : folder.path,
      })),
    }],
  });
  return values ? Number.parseInt(values.folderId, 10) : null;
}
