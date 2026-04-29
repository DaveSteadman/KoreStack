/**
 * main.js — KoreSheet application entry point.
 */

import * as grid       from './grid.js';
import * as cell       from './cell.js';
import * as properties from './properties.js';
import * as fileio     from './fileio.js';
import { colLetter, addrOf, evaluate } from './formula.js';
import * as topbar     from '/static/commonui/js/topbar.js';
import * as appbar     from '/static/commonui/js/appbar.js';
import * as draft      from '/static/shared/js/draft.js';
import { renderAppMenu, initAppMenuEvents } from '/static/commonui/js/appMenu.js';

const canvas      = document.getElementById('grid');
const container   = document.getElementById('canvas-container');
const cellEditor  = document.getElementById('cell-editor');
const formulaInput = document.getElementById('formula-input');
const cellAddrEl   = document.getElementById('cell-addr');

let _formulaEdit = {
  active: false,
  originCol: null,
  originRow: null,
  replaceStart: 0,
  replaceEnd: 0,
  insertedStart: null,
  insertedEnd: null,
};

renderAppMenu({
  app: 'koresheet',
  appLabel: 'KoreSheet',
  titleId: 'sheet-title',
  dirtyId: 'sheet-dirty',
  initialTitle: 'Untitled',
  menus: [
    {
      id: 'edit',
      label: 'Edit',
      items: [
        { action: 'clear-cell', label: 'Clear Cell' },
        { action: 'clear-formula', label: 'Clear Formula Bar' },
        { separator: true },
        { action: 'focus-grid', label: 'Focus Grid' },
      ],
    },
    {
      id: 'view',
      label: 'View',
      items: [
        { action: 'focus-grid', label: 'Focus Grid' },
        { action: 'focus-formula', label: 'Focus Formula Bar' },
        { action: 'focus-properties', label: 'Focus Properties' },
      ],
    },
  ],
});

// ── Bootstrap ──────────────────────────────────────────────────────────────

grid.init(canvas, container, cellEditor, _onCommit, _onSelect, _beginFormulaReferenceEdit);
fileio.init(_onStateChange);
topbar.initTopbar({ currentService: 'koredocs' });
appbar.initAppTabs('koresheet');

// Auto-open from ?file= URL param, else start with a blank sheet
const autoOpened = await fileio.autoOpenFromUrl(_refresh);
if (!autoOpened) {
  location.replace('/kf');
}

// Restore any unsaved draft for this tab
const _savedDraft = draft.load();
if (_savedDraft !== null) {
  cell.fromJSON(_savedDraft);
  fileio.markDirty();
  grid.draw();
  const _sel = grid.getSelection();
  _onSelect(_sel.col, _sel.row, null);
}

// Flush draft before any tab navigation.
// 'kd:before-navigate' is dispatched synchronously by appbar.js before setting
// location.href, so the localStorage write completes before the page unloads.
// We also call grid.commitEdit() to capture any mid-edit cell value (the tab
// click uses mousedown+e.preventDefault() so the cell editor never gets blur).
document.addEventListener('kd:before-navigate', () => {
  grid.commitEdit(); // flush mid-edit cell value into cell model if needed
  // Flush formula bar if that's where focus is
  if (document.activeElement === formulaInput) {
    const sel = grid.getSelection();
    cell.setCellRaw(sel.col, sel.row, formulaInput.value);
  }
  draft.save(cell.toJSON());
  fileio.flushAutosave({ keepalive: true });
});

// pagehide as a secondary safety net (e.g. browser back/forward navigation)
window.addEventListener('pagehide', () => {
  draft.save(cell.toJSON());
  fileio.flushAutosave({ keepalive: true });
});

// ── Formula bar ────────────────────────────────────────────────────────────

formulaInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    e.preventDefault();
    const target = _formulaCommitTarget();
    _onCommit(target.col, target.row, _prepareFormulaCommitValue(formulaInput.value));
    _resetFormulaEdit();
    grid.setSelection(target.col, target.row, null);
    grid.moveSelection(0, 1, false);
    grid.focusGrid();
  }
  if (e.key === 'Escape') {
    const target = _formulaCommitTarget();
    _resetFormulaEdit();
    grid.setSelection(target.col, target.row, null);
    grid.focusGrid();
  }
});

formulaInput.addEventListener('click', _captureFormulaCaret);
formulaInput.addEventListener('input', _captureFormulaCaret);
formulaInput.addEventListener('keyup', _captureFormulaCaret);

// ── Aggregate toolbar ──────────────────────────────────────────────────────

document.getElementById('toolbar').addEventListener('click', e => {
  const decimalBtn = e.target.closest('[data-decimal-action]');
  if (decimalBtn) {
    _applyDecimalAction(decimalBtn.dataset.decimalAction);
    return;
  }

  const btn = e.target.closest('[data-formula]');
  if (!btn) return;

  const sel      = grid.getSelection();
  const rangeEnd = grid.getRangeEnd();

  if (!rangeEnd) {
    alert('Select a range first (Shift+click or Shift+Arrow), then click a formula button.');
    return;
  }

  const fn  = btn.dataset.formula;
  const c1  = Math.min(sel.col, rangeEnd.col);
  const r1  = Math.min(sel.row, rangeEnd.row);
  const c2  = Math.max(sel.col, rangeEnd.col);
  const r2  = Math.max(sel.row, rangeEnd.row);
  const rangeStr = `${colLetter(c1)}${r1 + 1}:${colLetter(c2)}${r2 + 1}`;
  const formula  = `=${fn}(${rangeStr})`;

  // Insert into the first empty cell below (or to the right if at last row)
  let tCol = c1;
  let tRow = r2 + 1;
  if (tRow >= cell.ROWS) { tCol = c2 + 1; tRow = r1; }

  cell.setCellFormula(tCol, tRow, formula);
  fileio.markDirty();
  draft.save(cell.toJSON());
  fileio.queueAutosave(cell.toJSON());
  grid.draw();
  _onSelect(tCol, tRow, null);
});

// ── Style changes from properties panel ────────────────────────────────────

document.getElementById('props-content').addEventListener('ks:style-change', e => {
  const { col, row, rangeEnd, prop, value, toggle } = e.detail;

  if (rangeEnd && !toggle) {
    cell.setRangeStyle(col, row, rangeEnd.col, rangeEnd.row, { [prop]: value });
  } else if (toggle) {
    // Toggle boolean style (bold, italic)
    const current = cell.getCellStyle(col, row)[prop] ?? false;
    cell.setCellStyle(col, row, { [prop]: !current });
  } else {
    cell.setCellStyle(col, row, { [prop]: value });
  }

  fileio.markDirty();
  draft.save(cell.toJSON());
  fileio.queueAutosave(cell.toJSON());
  grid.draw();
  _onSelect(col, row, grid.getRangeEnd());
});

document.getElementById('props-content').addEventListener('ks:decimal-action', e => {
  _applyDecimalAction(e.detail.action, e.detail);
});

// ── Menu bar ───────────────────────────────────────────────────────────────

initAppMenuEvents(_handleAction);

async function _handleAction(action) {
  switch (action) {
    case 'clear-cell': {
      const sel = grid.getSelection();
      cell.setCellRaw(sel.col, sel.row, '');
      fileio.markDirty();
      draft.save(cell.toJSON());
      _refresh();
      break;
    }
    case 'clear-formula': {
      formulaInput.value = '';
      _resetFormulaEdit();
      formulaInput.focus();
      break;
    }
    case 'focus-grid':
      grid.focusGrid();
      break;
    case 'focus-formula':
      formulaInput.focus();
      break;
    case 'focus-properties':
      document.getElementById('props-content')?.scrollIntoView({ block: 'nearest' });
      break;
  }
}

// ── Callbacks ─────────────────────────────────────────────────────────────

function _onCommit(col, row, value) {
  cell.setCellRaw(col, row, value);
  fileio.markDirty();
  draft.save(cell.toJSON());
  fileio.queueAutosave(cell.toJSON());
}

function _onSelect(col, row, rangeEnd) {
  cellAddrEl.textContent = addrOf(col, row);
  if (_isFormulaRangeEditing()) {
    _applyFormulaRange(_rangeRef(col, row, rangeEnd));
  } else {
    formulaInput.value = cell.editValue(col, row);
    _resetFormulaEdit();
  }
  properties.refresh(col, row, rangeEnd);

  // Status bar range info
  if (rangeEnd) {
    const c1 = Math.min(col, rangeEnd.col);
    const r1 = Math.min(row, rangeEnd.row);
    const c2 = Math.max(col, rangeEnd.col);
    const r2 = Math.max(row, rangeEnd.row);
    const n  = (c2 - c1 + 1) * (r2 - r1 + 1);
    document.getElementById('status-sel').textContent =
      `${colLetter(c1)}${r1 + 1}:${colLetter(c2)}${r2 + 1}  ${n} cells`;
  } else {
    document.getElementById('status-sel').textContent = '';
  }
}

function _rangeRef(col, row, rangeEnd) {
  if (!rangeEnd) return addrOf(col, row);
  const c1 = Math.min(col, rangeEnd.col);
  const r1 = Math.min(row, rangeEnd.row);
  const c2 = Math.max(col, rangeEnd.col);
  const r2 = Math.max(row, rangeEnd.row);
  return `${colLetter(c1)}${r1 + 1}:${colLetter(c2)}${r2 + 1}`;
}

function _captureFormulaCaret() {
  const value = formulaInput.value;
  if (!value.trim().startsWith('=')) {
    _resetFormulaEdit();
    return;
  }
  const selection = grid.getSelection();
  const start = formulaInput.selectionStart ?? value.length;
  const end = formulaInput.selectionEnd ?? start;
  _formulaEdit = {
    active: true,
    originCol: _formulaEdit.active ? _formulaEdit.originCol : selection.col,
    originRow: _formulaEdit.active ? _formulaEdit.originRow : selection.row,
    replaceStart: start,
    replaceEnd: end,
    insertedStart: null,
    insertedEnd: null,
  };
}

function _beginFormulaReferenceEdit({ col, row, value, selectionStart, selectionEnd }) {
  formulaInput.value = String(value ?? '');
  formulaInput.focus({ preventScroll: true });
  formulaInput.setSelectionRange(selectionStart ?? formulaInput.value.length, selectionEnd ?? selectionStart ?? formulaInput.value.length);
  _formulaEdit = {
    active: true,
    originCol: col,
    originRow: row,
    replaceStart: selectionStart ?? formulaInput.value.length,
    replaceEnd: selectionEnd ?? selectionStart ?? formulaInput.value.length,
    insertedStart: null,
    insertedEnd: null,
  };
}

function _isFormulaRangeEditing() {
  return _formulaEdit.active && formulaInput.value.trim().startsWith('=');
}

function _applyFormulaRange(rangeRef) {
  const value = formulaInput.value;
  const replaceStart = _formulaEdit.insertedStart ?? _formulaEdit.replaceStart;
  const replaceEnd = _formulaEdit.insertedEnd ?? _formulaEdit.replaceEnd;
  const originCol = _formulaEdit.originCol;
  const originRow = _formulaEdit.originRow;
  const insertedRef = _normalizeInsertedRangeRef(value, replaceStart, rangeRef);
  formulaInput.value = value.slice(0, replaceStart) + insertedRef + value.slice(replaceEnd);
  const caret = replaceStart + insertedRef.length;
  _formulaEdit = {
    active: true,
    originCol,
    originRow,
    replaceStart,
    replaceEnd: caret,
    insertedStart: replaceStart,
    insertedEnd: caret,
  };
  formulaInput.focus({ preventScroll: true });
  formulaInput.setSelectionRange(caret, caret);
}

function _normalizeInsertedRangeRef(value, replaceStart, rangeRef) {
  const prefix = value.slice(0, replaceStart);
  const prefixMatch = prefix.match(/([A-Z]+\d+):$/);
  if (!prefixMatch) return rangeRef;
  const [rangeStart, rangeEnd] = String(rangeRef).split(':');
  if (!rangeEnd || prefixMatch[1] !== rangeStart) return rangeRef;
  return rangeEnd;
}

function _resetFormulaEdit() {
  _formulaEdit = {
    active: false,
    originCol: null,
    originRow: null,
    replaceStart: 0,
    replaceEnd: 0,
    insertedStart: null,
    insertedEnd: null,
  };
}

function _formulaCommitTarget() {
  if (_formulaEdit.active && _formulaEdit.originCol != null && _formulaEdit.originRow != null) {
    return { col: _formulaEdit.originCol, row: _formulaEdit.originRow };
  }
  return grid.getSelection();
}

function _prepareFormulaCommitValue(value) {
  const text = String(value ?? '');
  if (!_isFormulaRangeEditing()) return text;
  const opens = (text.match(/\(/g) || []).length;
  const closes = (text.match(/\)/g) || []).length;
  if (opens <= closes) return text;
  return text + ')'.repeat(opens - closes);
}

function _onStateChange(name, dirty) {
  document.getElementById('sheet-title').textContent = name ?? 'Untitled';
  document.getElementById('sheet-dirty').classList.toggle('hidden', !dirty);
  document.title = (dirty ? '● ' : '') + (name ?? 'Untitled') + ' — KoreSheet';
  document.getElementById('status-file').textContent = name ?? 'Untitled.koresheet';
  if (name) appbar.trackAppTab(name, 'koresheet', fileio.currentId());
}

function _refresh() {
  grid.draw();
  const sel = grid.getSelection();
  _onSelect(sel.col, sel.row, grid.getRangeEnd());
}

function _applyDecimalAction(action, detail = null) {
  const baseSelection = detail ? { col: detail.col, row: detail.row } : grid.getSelection();
  const rangeEnd = detail?.rangeEnd ?? grid.getRangeEnd();
  const end = rangeEnd ?? baseSelection;

  if (action === 'increase') {
    cell.adjustRangeDecimalPlaces(baseSelection.col, baseSelection.row, end.col, end.row, 1);
  } else if (action === 'decrease') {
    cell.adjustRangeDecimalPlaces(baseSelection.col, baseSelection.row, end.col, end.row, -1);
  } else if (action === 'clear') {
    cell.clearRangeDecimalPlaces(baseSelection.col, baseSelection.row, end.col, end.row);
  } else {
    return;
  }

  fileio.markDirty();
  draft.save(cell.toJSON());
  fileio.queueAutosave(cell.toJSON());
  grid.draw();
  _onSelect(baseSelection.col, baseSelection.row, rangeEnd);
}
