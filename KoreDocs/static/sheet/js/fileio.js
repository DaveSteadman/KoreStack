/**
 * fileio.js — KoreFile-backed file state + autosave for KoreSheet.
 */

import { createKfSyncController } from '/static/shared/js/kfSyncController.js';
import { fromJSON } from './cell.js';

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

const _controller = createKfSyncController({
  logLabel: 'KoreSheet',
  alertLabel: 'Sheet',
  legacyType: 'koresheet',
  buildBlankContent: title => _blankSheet(title),
  applyLoadedContent: (content, file, onLoaded) => {
    fromJSON(content);
    onLoaded?.();
  },
});

export const init = _controller.init;
export const currentId = _controller.currentId;
export const isDirty = _controller.isDirty;
export const currentName = _controller.currentName;
export const currentRevision = _controller.currentRevision;
export const markDirty = _controller.markDirty;
export const markSaved = _controller.markSaved;
export const guardUnsaved = _controller.guardUnsaved;
export const autoOpenFromUrl = _controller.autoOpenFromUrl;
export const queueAutosave = _controller.queueAutosave;
export const flushAutosave = _controller.flushAutosave;
