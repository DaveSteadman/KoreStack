/**
 * main.js — Entry point. Wires everything together.
 */

import * as model       from './model.js';
import * as store       from './store.js';
import * as renderer    from './renderer.js';
import * as interaction from './interaction.js';
import * as ui          from './ui.js';
import * as fileio      from './fileio.js';
import * as topbar      from '/ui-elements/assets/js/topbar.js';
import * as appbar      from '/ui-elements/assets/js/appbar.js';
import * as draft       from '/static/shared/js/draft.js';
import { renderAppMenu } from '/ui-elements/assets/js/appMenu.js';

const _draftSave = draft.makeSaver();

renderAppMenu({
  app: 'kodiag',
  appLabel: 'KoreDiag',
  titleId: 'diag-title',
  dirtyId: 'diag-dirty',
  initialTitle: 'KoreDiag',
  editableTitle: true,
  menus: [
    {
      id: 'file',
      label: 'File',
      items: [
        { action: 'export-png', label: 'Export PNG', shortcut: 'Ctrl+Shift+E' },
      ],
    },
    {
      id: 'edit',
      label: 'Edit',
      items: [
        { action: 'undo', label: 'Undo', shortcut: 'Ctrl+Z' },
        { action: 'redo', label: 'Redo', shortcut: 'Ctrl+Y' },
        { separator: true },
        { action: 'select-all', label: 'Select All', shortcut: 'Ctrl+A' },
        { action: 'delete', label: 'Delete', shortcut: 'Del' },
      ],
    },
    {
      id: 'view',
      label: 'View',
      items: [
        { action: 'zoom-in', label: 'Zoom In' },
        { action: 'zoom-out', label: 'Zoom Out' },
        { action: 'reset-view', label: 'Reset View', shortcut: 'Ctrl+Shift+H' },
        { separator: true },
        { action: 'toggle-grid', label: 'Toggle Grid' },
      ],
    },
  ],
});

// Expose model helpers for fileio PNG export
window._koreModel = model;

// ── View state persistence ─────────────────────────────────────────────────
// Save/restore zoom + pan per file so switching tabs preserves the viewport.

const _VIEW_STORE_KEY = 'koredocs:diag:view';

function _viewKey() {
  const params = new URLSearchParams(location.search);
  const id = params.get('id');
  if (id) return `kf:${id}`;
  return params.get('file') ?? '__new__';
}

function _saveView() {
  try {
    const all = JSON.parse(localStorage.getItem(_VIEW_STORE_KEY) || '{}');
    all[_viewKey()] = { zoom: store.view.zoom, pan: { ...store.view.pan } };
    localStorage.setItem(_VIEW_STORE_KEY, JSON.stringify(all));
  } catch { /* ignore */ }
}

function _restoreView() {
  try {
    const all = JSON.parse(localStorage.getItem(_VIEW_STORE_KEY) || '{}');
    const saved = all[_viewKey()];
    if (saved) {
      store.view.zoom  = saved.zoom;
      store.view.pan.x = saved.pan.x;
      store.view.pan.y = saved.pan.y;
      return true;
    }
  } catch { /* ignore */ }
  return false;
}



const canvas = document.getElementById('canvas');

renderer.init(canvas);
interaction.init(canvas);
ui.initMenus();
ui.initToolbar();
ui.initHierarchy();
topbar.initTopbar({ currentService: 'koredocs', urls: window.__koreSuiteUrls || {} });
appbar.initAppBar({
  mountId: 'tab-bar',
  currentService: 'koredocs',
  overline: 'Diagram Editor',
  brandLabel: 'KoreDiag',
  brandIcon: 'kodiag',
  editorTabsSlot: 'koredocs-tabs',
});
appbar.initAppTabs('kodiag', { mountId: 'koredocs-tabs', renderBrand: false });

const autoOpened = await fileio.autoOpenFromUrl(diagram => {
  store.loadDiagram(diagram);
});
if (!autoOpened) {
  location.replace('/ui');
}

if (fileio.currentName()) {
  appbar.trackAppTab(fileio.currentName(), 'kodiag', fileio.currentId());
}

// Set initial tool + cursor
interaction.setTool('select');

// Centre the origin in the viewport (overridden below if a saved view exists)
store.view.pan.x = canvas.parentElement.clientWidth  / 2;
store.view.pan.y = canvas.parentElement.clientHeight / 2;

// ── Reactive updates ───────────────────────────────────────────────────────

store.on('change', () => {
  renderer.draw();
  ui.refreshProperties();
  ui.refreshHierarchy();
  ui.refreshTitle();
  const text = JSON.stringify(store.getDiagram());
  _draftSave(text);
  fileio.queueAutosave(text);
});

store.on('diagram-loaded', () => {
  // Restore saved view if available, otherwise re-centre
  if (!_restoreView()) {
    store.view.pan.x = canvas.parentElement.clientWidth  / 2;
    store.view.pan.y = canvas.parentElement.clientHeight / 2;
    store.view.zoom  = 1;
  }
  renderer.draw();
  ui.refreshProperties();
  ui.refreshHierarchy();
  ui.refreshTitle();
});

document.addEventListener('kd:selection-change', () => {
  ui.refreshProperties();
  ui.refreshHierarchy();
});

// ── Global event bus (keyboard shortcuts + menu actions) ───────────────────

document.addEventListener('kd:export-png', () => { fileio.exportPng(); });

document.addEventListener('kd:zoom', e => {
  store.view.zoom = Math.max(0.1, Math.min(5, store.view.zoom * e.detail));
  renderer.draw();
  _saveView();
});

document.addEventListener('kd:reset-view', () => {
  store.view.pan.x = canvas.parentElement.clientWidth  / 2;
  store.view.pan.y = canvas.parentElement.clientHeight / 2;
  store.view.zoom  = 1;
  renderer.draw();
  _saveView();
});

document.addEventListener('kd:scroll-to', e => {
  const id  = e.detail;
  const nm  = store.getNodeMap();
  const b   = model.worldBounds(id, nm);
  if (!b) return;
  const { gridSize } = store.getDiagram().settings;
  const gs = gridSize * store.view.zoom;
  store.view.pan.x = canvas.parentElement.clientWidth  / 2 - (b.x + b.width  / 2) * gs;
  store.view.pan.y = canvas.parentElement.clientHeight / 2 - (b.y + b.height / 2) * gs;
  renderer.draw();
});

document.addEventListener('kd:autosaved', () => {
  ui.refreshTitle();
});

// Flush draft + view state synchronously before any tab navigation
document.addEventListener('kd:before-navigate', () => {
  draft.save(JSON.stringify(store.getDiagram()));
  _saveView();
  fileio.flushAutosave({ keepalive: true });
});

// Save view state after pan/zoom (debounced — fires 400 ms after last change)
let _viewSaveTimer = null;
document.addEventListener('kd:view-changed', () => {
  clearTimeout(_viewSaveTimer);
  _viewSaveTimer = setTimeout(_saveView, 400);
});

// pagehide as a secondary safety net (e.g. browser back/forward)
window.addEventListener('pagehide', () => {
  draft.save(JSON.stringify(store.getDiagram()));
  _saveView();
  fileio.flushAutosave({ keepalive: true });
});

// ── Restore any unsaved draft for this tab ───────────────────────────────

const _savedDraft = draft.load();
if (_savedDraft !== null) {
  try {
    store.loadDiagram(JSON.parse(_savedDraft));
    store.markDirty(); // draft = unsaved work
  } catch (e) {
    console.warn('[KoreDiag] failed to restore draft:', e);
  }
} else {
  // No draft — still try to restore view for this file
  _restoreView();
}

// ── Initial draw ──────────────────────────────────────────────────────────

renderer.draw();
ui.refreshHierarchy();
ui.refreshTitle();
