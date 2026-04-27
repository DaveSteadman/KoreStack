/**
 * main.js — KoreDoc application entry point.
 * Wires together editor, toolbar, properties panel, file I/O, and the menu bar.
 */

import * as editor     from './editor.js';
import * as toolbar    from './toolbar.js';
import * as properties from './properties.js';
import * as fileio     from './fileio.js';
import * as topbar     from '/static/commonui/js/topbar.js';
import * as draft      from '/static/shared/js/draft.js';
import { initAppMenuEvents } from '/static/commonui/js/appMenu.js';

const _draftSave = draft.makeSaver();

// ── Bootstrap ──────────────────────────────────────────────────────────────

editor.init(document.getElementById('editor-host'), _onEditorChange);
toolbar.init();

function _onEditorChange(markdown) {
  if (!fileio.isDirty()) fileio.markDirty();
  fileio.queueAutosave(markdown);
  _draftSave(markdown);
  _updateStatus();
  properties.refresh(markdown, fileio.currentName());
}
fileio.init(_onStateChange);

// Auto-open from ?file= URL param, else start with a blank document
const autoOpened = await fileio.autoOpenFromUrl(v => {
  editor.setValue(v);
  properties.refresh(v, fileio.currentName());
});
if (!autoOpened) {
  location.replace('/kf');
}

// Restore any unsaved draft for this tab (takes priority over server / blank content)
const _savedDraft = draft.load();
if (_savedDraft !== null) {
  editor.setValue(_savedDraft);
  fileio.markDirty();
  properties.refresh(_savedDraft, fileio.currentName());
}

// ── Dirty tracking ─────────────────────────────────────────────────────────


// ── Menu bar ───────────────────────────────────────────────────────────────

initAppMenuEvents(_handleAction);

// Flush draft synchronously before any tab navigation
document.addEventListener('kd:before-navigate', () => {
  draft.save(editor.getValue());
  fileio.flushAutosave({ keepalive: true });
});

// pagehide as a secondary safety net (e.g. browser back/forward)
window.addEventListener('pagehide', () => {
  draft.save(editor.getValue());
  fileio.flushAutosave({ keepalive: true });
});

async function _handleAction(action) {
  switch (action) {
    case 'undo':        editor.doUndo();      break;
    case 'redo':        editor.doRedo();      break;
    case 'select-all':  editor.doSelectAll(); break;
    case 'focus-editor': editor.getView()?.focus(); break;
    case 'focus-properties':
      document.getElementById('props-content')?.scrollIntoView({ block: 'nearest' });
      break;
    case 'focus-map':
      document.getElementById('map-content')?.scrollIntoView({ block: 'nearest' });
      break;
  }
}

// ── Document map navigation ────────────────────────────────────────────────

document.addEventListener('kd:goto-heading', e => {
  editor.scrollToHeading(e.detail);
});

// ── State change callback ──────────────────────────────────────────────────

function _onStateChange(name, dirty) {
  document.getElementById('doc-title').textContent = name ?? 'Untitled';
  document.getElementById('doc-dirty').classList.toggle('hidden', !dirty);
  document.title = (dirty ? '● ' : '') + (name ?? 'Untitled') + ' — KoreDoc';
  _updateStatus();
  properties.refresh(editor.getValue(), name);
  if (name) topbar.track(name, 'koredoc', fileio.currentId());
}

function _updateStatus() {
  const text = editor.getValue();
  const lines = text.split('\n').length;
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  document.getElementById('status-file').textContent   = fileio.currentName() ?? 'Untitled.koredoc';
  document.getElementById('status-counts').textContent = `${lines} lines · ${words} words`;
}


