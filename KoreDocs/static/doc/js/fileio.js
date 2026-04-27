/**
 * fileio.js — KoreFile-backed file state + autosave for KoreDoc.
 */

import { createKfSyncController } from '/static/shared/js/kfSyncController.js';

/** Return a blank document string with YAML frontmatter. */
export function blankDoc(title = 'Untitled') {
  const today = new Date().toISOString().slice(0, 10);
  return `---\ntitle: ${title}\ncreated: ${today}\n---\n\n`;
}

const _controller = createKfSyncController({
  logLabel: 'KoreDoc',
  alertLabel: 'Document',
  legacyType: 'koredoc',
  buildBlankContent: title => blankDoc(title),
  applyLoadedContent: (content, file, setContent) => {
    setContent?.(content);
  },
});

export const init = _controller.init;
export const currentId = _controller.currentId;
export const currentName = _controller.currentName;
export const currentRevision = _controller.currentRevision;
export const isDirty = _controller.isDirty;
export const markDirty = _controller.markDirty;
export const markSaved = _controller.markSaved;
export const guardUnsaved = _controller.guardUnsaved;
export const autoOpenFromUrl = _controller.autoOpenFromUrl;
export const queueAutosave = _controller.queueAutosave;
export const flushAutosave = _controller.flushAutosave;
