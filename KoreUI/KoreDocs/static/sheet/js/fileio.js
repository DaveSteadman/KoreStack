/**
 * fileio.js — KoreFile-backed file state + autosave for KoreSheet.
 */

import { createKorefileSyncController } from '/static/shared/js/korefileSyncController.js?v=20261117a';
import { initKorefileSaveStatus } from '/static/shared/js/saveStatus.js';
import { createFile, getFile } from '/static/shared/js/korefileapi.js';
import { fromJSON } from './cell.js';
import { csvToSheetJson, limitCsvPreview } from './csv.js';

export { limitCsvPreview };

let _csvPreviewInfo = null;

function _isCsvFile(fileOrName) {
  const name = typeof fileOrName === 'string' ? fileOrName : fileOrName?.name;
  return String(name || '').toLowerCase().endsWith('.csv');
}

export function csvPreviewInfo() {
  return _csvPreviewInfo ? { ..._csvPreviewInfo } : null;
}

function _blankSheet(name) {
  const title = name.replace(/\.[^.]+$/, '');
  const today = new Date().toISOString().slice(0, 10);
  return JSON.stringify({
    version: 1,
    meta: { title, created: today },
    cols: 26,
    rows: 100,
    cells: {},
  });
}

const _controller = createKorefileSyncController({
  logLabel: 'KoreSheet',
  alertLabel: 'Sheet',
  legacyType: 'koresheet',
  buildBlankContent: title => _blankSheet(title),
  applyLoadedContent: (content, file, onLoaded) => {
    if (_isCsvFile(file)) {
      const source = String(file?.content || '').trim() ? String(file.content) : '';
      const preview = source ? csvToSheetJson(source, file.name) : _blankSheet(file.name);
      _csvPreviewInfo = JSON.parse(preview).meta.csvPreview ?? null;
      fromJSON(preview);
    } else {
      _csvPreviewInfo = null;
      fromJSON(content);
    }
    onLoaded?.();
  },
});

initKorefileSaveStatus(() => _controller.flushAutosave());

export const init = _controller.init;
export const currentId = _controller.currentId;
export const isDirty = _controller.isDirty;
export const currentName = _controller.currentName;
export const currentRevision = _controller.currentRevision;
export const markDirty = _controller.markDirty;
export const markSaved = _controller.markSaved;
export const guardUnsaved = _controller.guardUnsaved;
export const autoOpenFromUrl = _controller.autoOpenFromUrl;
export const reloadCurrent = _controller.reloadCurrent;
export const isCsvFile = () => _isCsvFile(_controller.currentName());

export async function convertToKoreSheet(name, content) {
  if (!_isCsvFile(_controller.currentName()) || _controller.currentId() == null) {
    throw new Error('Only an open CSV file can be converted to KoreSheet.');
  }
  const source = await getFile(_controller.currentId(), { includeContent: false });
  const targetName = String(name || '').trim();
  if (!targetName.toLowerCase().endsWith('.koresheet')) {
    throw new Error('Converted files must use the .koresheet extension.');
  }
  return createFile(source.folder_id, targetName, content, {
    title: targetName.replace(/\.koresheet$/i, ''),
    source_file_id: source.id,
    source_file_name: source.name,
  });
}

export function queueAutosave(text) {
  if (_isCsvFile(_controller.currentName())) return;
  _controller.queueAutosave(text);
}

export const flushAutosave = _controller.flushAutosave;
