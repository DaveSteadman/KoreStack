/**
 * grid.js — Canvas grid renderer and keyboard/mouse input handler for KoreSheet.
 */

import {
  COLS, ROWS, COL_W, ROW_H, ROW_HEADER_W, COL_HEADER_H,
  displayValue, editValue, getCellStyle,
} from './cell.js';
import { colLetter, addrOf } from './formula.js';

// ── Canvas dimensions ──────────────────────────────────────────────────────

export const TOTAL_W = ROW_HEADER_W + COLS * COL_W;
export const TOTAL_H = COL_HEADER_H + ROWS * ROW_H;

// ── Palette (mirrors CSS custom properties) ────────────────────────────────

const P = {
  bg:        '#0f1520',
  surface:   '#1a2640',
  border:    '#2a3f5a',
  header:    '#131d2e',
  headerTxt: '#4a5c70',
  text:      '#c8d8ec',
  accent:    '#4a6fa5',
  selFill:   'rgba(74,111,165,0.18)',
  selBorder: '#4a6fa5',
};

// ── State ──────────────────────────────────────────────────────────────────

let _canvas    = null;
let _ctx       = null;
let _container = null;
let _editor    = null;   // <input id="cell-editor">

// Active cell
let _sel       = { col: 0, row: 0 };
// Range anchor (for Shift-click / Shift-Arrow / drag)  null = no range
let _rangeEnd  = null;   // { col, row }

let _editMode  = false;
let _dragging  = false;  // true while a mousedown-drag selection is in progress

// Callbacks
let _onCommit  = null;   // (col, row, rawValue) => void
let _onSelect  = null;   // (col, row, rangeEnd|null) => void
let _onFormulaEditReference = null; // ({ col, row, value, selectionStart, selectionEnd }) => void

// ── Init ───────────────────────────────────────────────────────────────────

export function init(canvas, container, cellEditor, onCommit, onSelect, onFormulaEditReference = null) {
  _canvas    = canvas;
  _ctx       = canvas.getContext('2d');
  _container = container;
  _editor    = cellEditor;
  _onCommit  = onCommit;
  _onSelect  = onSelect;
  _onFormulaEditReference = onFormulaEditReference;

  if (!_canvas.hasAttribute('tabindex')) {
    _canvas.tabIndex = 0;
  }

  canvas.width  = TOTAL_W;
  canvas.height = TOTAL_H;

  canvas.addEventListener('mousedown', _onMouseDown);
  canvas.addEventListener('dblclick',  _onDblClick);
  document.addEventListener('mousemove', _onMouseDrag);
  document.addEventListener('mouseup',   _onMouseUp);
  document.addEventListener('keydown', _onKeyDown);

  cellEditor.addEventListener('blur',    _commitEdit);
  cellEditor.addEventListener('keydown', _editorKeyDown);
}

// ── Public ─────────────────────────────────────────────────────────────────

export function draw() {
  const ctx = _ctx;
  ctx.clearRect(0, 0, TOTAL_W, TOTAL_H);
  _drawHeaders(ctx);
  _drawCells(ctx);
  _drawGridLines(ctx);
  _drawSelection(ctx);
}

export function getSelection() { return { ..._sel }; }
export function getRangeEnd()  { return _rangeEnd ? { ..._rangeEnd } : null; }
export function focusGrid()    { _canvas?.focus(); }
export function moveSelection(dc, dr, shift = false) { _move(dc, dr, shift); }
export function setSelection(col, row, rangeEnd = null) {
  _sel = {
    col: Math.max(0, Math.min(COLS - 1, col)),
    row: Math.max(0, Math.min(ROWS - 1, row)),
  };
  _rangeEnd = rangeEnd
    ? {
        col: Math.max(0, Math.min(COLS - 1, rangeEnd.col)),
        row: Math.max(0, Math.min(ROWS - 1, rangeEnd.row)),
      }
    : null;
  draw();
  _onSelect?.(_sel.col, _sel.row, _rangeEnd);
  _scrollToActive();
}

// ── Drawing ────────────────────────────────────────────────────────────────

function _drawHeaders(ctx) {
  // Background
  ctx.fillStyle = P.header;
  ctx.fillRect(0, 0, TOTAL_W, COL_HEADER_H);
  ctx.fillRect(0, 0, ROW_HEADER_W, TOTAL_H);

  // Active column / row ranges for highlight
  const { c1, r1, c2, r2 } = _selectionBounds();

  ctx.font         = 'bold 11px system-ui, sans-serif';
  ctx.textBaseline = 'middle';

  // Column headers
  ctx.textAlign = 'center';
  for (let c = 0; c < COLS; c++) {
    const x = ROW_HEADER_W + c * COL_W;
    if (c >= c1 && c <= c2) {
      ctx.fillStyle = P.accent;
      ctx.fillRect(x, 0, COL_W, COL_HEADER_H);
      ctx.fillStyle = '#fff';
    } else {
      ctx.fillStyle = P.headerTxt;
    }
    ctx.fillText(colLetter(c), x + COL_W / 2, COL_HEADER_H / 2);
  }

  // Row headers
  ctx.textAlign = 'right';
  for (let r = 0; r < ROWS; r++) {
    const y = COL_HEADER_H + r * ROW_H;
    if (r >= r1 && r <= r2) {
      ctx.fillStyle = P.accent;
      ctx.fillRect(0, y, ROW_HEADER_W, ROW_H);
      ctx.fillStyle = '#fff';
    } else {
      ctx.fillStyle = P.headerTxt;
    }
    ctx.fillText(r + 1, ROW_HEADER_W - 5, y + ROW_H / 2);
  }

  // Corner cell
  ctx.fillStyle = P.header;
  ctx.fillRect(0, 0, ROW_HEADER_W, COL_HEADER_H);

  // Divider lines
  ctx.strokeStyle = P.border;
  ctx.lineWidth   = 1;
  ctx.beginPath();
  ctx.moveTo(0,          COL_HEADER_H + 0.5);
  ctx.lineTo(TOTAL_W,    COL_HEADER_H + 0.5);
  ctx.moveTo(ROW_HEADER_W + 0.5, 0);
  ctx.lineTo(ROW_HEADER_W + 0.5, TOTAL_H);
  ctx.stroke();
}

function _drawCells(ctx) {
  ctx.textBaseline = 'middle';

  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      const x     = ROW_HEADER_W + c * COL_W;
      const y     = COL_HEADER_H + r * ROW_H;
      const style = getCellStyle(c, r);
      const val   = displayValue(c, r);

      // Cell fill
      if (style.fillColor) {
        ctx.fillStyle = style.fillColor;
        ctx.fillRect(x, y, COL_W, ROW_H);
      }

      if (val === '' || val == null) continue;

      const bold      = style.bold   ? 'bold '   : '';
      const italic    = style.italic ? 'italic ' : '';
      const textColor = style.textColor ?? P.text;
      const align     = style.align ?? 'left';

      ctx.font      = `${italic}${bold}13px system-ui, sans-serif`;
      ctx.fillStyle = textColor;
      ctx.textAlign = align;

      const textX =
        align === 'right'  ? x + COL_W - 5 :
        align === 'center' ? x + COL_W / 2  :
                             x + 5;

      ctx.save();
      ctx.beginPath();
      ctx.rect(x + 1, y, COL_W - 2, ROW_H);
      ctx.clip();
      ctx.fillText(String(val), textX, y + ROW_H / 2);
      ctx.restore();
    }
  }
}

function _drawGridLines(ctx) {
  ctx.strokeStyle = P.border;
  ctx.lineWidth   = 0.5;
  ctx.beginPath();

  for (let c = 0; c <= COLS; c++) {
    const x = ROW_HEADER_W + c * COL_W + 0.5;
    ctx.moveTo(x, COL_HEADER_H);
    ctx.lineTo(x, TOTAL_H);
  }
  for (let r = 0; r <= ROWS; r++) {
    const y = COL_HEADER_H + r * ROW_H + 0.5;
    ctx.moveTo(ROW_HEADER_W, y);
    ctx.lineTo(TOTAL_W, y);
  }

  ctx.stroke();
}

function _drawSelection(ctx) {
  if (_editMode) return;

  const { c1, r1, c2, r2 } = _selectionBounds();

  // Range fill
  if (_rangeEnd) {
    ctx.fillStyle = P.selFill;
    ctx.fillRect(
      ROW_HEADER_W + c1 * COL_W,
      COL_HEADER_H + r1 * ROW_H,
      (c2 - c1 + 1) * COL_W,
      (r2 - r1 + 1) * ROW_H,
    );
  }

  // Active cell border
  ctx.strokeStyle = P.selBorder;
  ctx.lineWidth   = 2;
  const ax = ROW_HEADER_W + _sel.col * COL_W;
  const ay = COL_HEADER_H + _sel.row * ROW_H;
  ctx.strokeRect(ax + 1, ay + 1, COL_W - 2, ROW_H - 2);
}

// ── Selection bounds helper ────────────────────────────────────────────────

function _selectionBounds() {
  const ec = _rangeEnd?.col ?? _sel.col;
  const er = _rangeEnd?.row ?? _sel.row;
  return {
    c1: Math.min(_sel.col, ec),
    r1: Math.min(_sel.row, er),
    c2: Math.max(_sel.col, ec),
    r2: Math.max(_sel.row, er),
  };
}

// ── Hit testing ────────────────────────────────────────────────────────────

function _hitCell(offsetX, offsetY) {
  if (offsetX < ROW_HEADER_W || offsetY < COL_HEADER_H) return null;
  const col = Math.floor((offsetX - ROW_HEADER_W) / COL_W);
  const row = Math.floor((offsetY - COL_HEADER_H) / ROW_H);
  if (col < 0 || col >= COLS || row < 0 || row >= ROWS) return null;
  return { col, row };
}

/** Convert a document-level clientX/Y to a canvas-local hit, accounting for scroll. */
function _clientHitCell(clientX, clientY) {
  const rect = _canvas.getBoundingClientRect();
  return _hitCell(clientX - rect.left, clientY - rect.top);
}

// ── Mouse events ───────────────────────────────────────────────────────────

function _onMouseDown(e) {
  if (_editMode) {
    const editorValue = _editor.value;
    if (editorValue.trim().startsWith('=')) {
      const selectionStart = _editor.selectionStart ?? editorValue.length;
      const selectionEnd = _editor.selectionEnd ?? selectionStart;
      _editMode = false;
      _editor.style.display = 'none';
      _onFormulaEditReference?.({
        col: _sel.col,
        row: _sel.row,
        value: editorValue,
        selectionStart,
        selectionEnd,
      });
    } else {
      _commitEdit();
      return;
    }
  }
  const hit = _hitCell(e.offsetX, e.offsetY);
  if (!hit) return;

  if (e.shiftKey) {
    _rangeEnd = hit;
  } else {
    _sel      = hit;
    _rangeEnd = null;
    _dragging = true;
  }
  draw();
  _onSelect?.(_sel.col, _sel.row, _rangeEnd);
}

function _onMouseDrag(e) {
  if (!_dragging) return;
  const hit = _clientHitCell(e.clientX, e.clientY);
  if (!hit) return;
  // Only update if the cell actually changed
  const cur = _rangeEnd ?? _sel;
  if (hit.col === cur.col && hit.row === cur.row) return;
  _rangeEnd = (hit.col === _sel.col && hit.row === _sel.row) ? null : hit;
  draw();
  _onSelect?.(_sel.col, _sel.row, _rangeEnd);
}

function _onMouseUp(e) {
  if (!_dragging) return;
  _dragging = false;
  // Final hit in case the mouseup lands on a different cell than the last mousemove
  const hit = _clientHitCell(e.clientX, e.clientY);
  if (hit) {
    _rangeEnd = (hit.col === _sel.col && hit.row === _sel.row) ? null : hit;
    draw();
    _onSelect?.(_sel.col, _sel.row, _rangeEnd);
  }
}

function _onDblClick(e) {
  const hit = _hitCell(e.offsetX, e.offsetY);
  if (!hit) return;
  _sel      = hit;
  _rangeEnd = null;
  _enterEditMode();
}

// ── Keyboard navigation ────────────────────────────────────────────────────

function _onKeyDown(e) {
  if (_editMode) return;

  // Let focus-owning inputs handle their own keys
  const tag = document.activeElement?.tagName;
  if (tag && ['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;

  switch (e.key) {
    case 'ArrowRight': e.preventDefault(); _move( 1,  0, e.shiftKey); break;
    case 'ArrowLeft':  e.preventDefault(); _move(-1,  0, e.shiftKey); break;
    case 'ArrowDown':  e.preventDefault(); _move( 0,  1, e.shiftKey); break;
    case 'ArrowUp':    e.preventDefault(); _move( 0, -1, e.shiftKey); break;

    case 'Tab':
      e.preventDefault();
      _move(e.shiftKey ? -1 : 1, 0, false);
      break;

    case 'Enter':
      if (e.shiftKey) { _enterEditMode(); }
      else            { _move(0, 1, false); }
      break;

    case 'F2':
      e.preventDefault();
      _enterEditMode();
      break;

    case 'Delete':
    case 'Backspace':
      e.preventDefault();
      _onCommit?.(_sel.col, _sel.row, '');
      draw();
      _onSelect?.(_sel.col, _sel.row, _rangeEnd);
      break;

    default:
      // Printable character → start editing with it pre-filled
      if (!e.ctrlKey && !e.metaKey && !e.altKey && e.key.length === 1) {
        e.preventDefault(); // prevent the browser typing the same char into the now-focused input
        _enterEditMode(e.key);
      }
  }
}

function _move(dc, dr, shift) {
  const newCol = Math.max(0, Math.min(COLS - 1, _sel.col + dc));
  const newRow = Math.max(0, Math.min(ROWS - 1, _sel.row + dr));

  if (shift) {
    _rangeEnd = { col: newCol, row: newRow };
  } else {
    _sel      = { col: newCol, row: newRow };
    _rangeEnd = null;
  }

  draw();
  _onSelect?.(_sel.col, _sel.row, _rangeEnd);
  _scrollToActive();
}

function _scrollToActive() {
  const x = ROW_HEADER_W + _sel.col * COL_W;
  const y = COL_HEADER_H + _sel.row * ROW_H;
  const sl = _container.scrollLeft;
  const st = _container.scrollTop;
  const vw = _container.clientWidth;
  const vh = _container.clientHeight;

  if (x < sl + ROW_HEADER_W)    _container.scrollLeft = x - ROW_HEADER_W;
  if (x + COL_W > sl + vw)      _container.scrollLeft = x + COL_W - vw;
  if (y < st + COL_HEADER_H)    _container.scrollTop  = y - COL_HEADER_H;
  if (y + ROW_H > st + vh)      _container.scrollTop  = y + ROW_H - vh;
}

// ── Edit mode ──────────────────────────────────────────────────────────────

function _enterEditMode(initialChar = null) {
  _editMode = true;

  const x = ROW_HEADER_W + _sel.col * COL_W;
  const y = COL_HEADER_H + _sel.row * ROW_H;

  _editor.style.left    = x + 'px';
  _editor.style.top     = y + 'px';
  _editor.style.width   = COL_W + 'px';
  _editor.style.height  = ROW_H + 'px';
  _editor.style.display = 'block';

  if (initialChar !== null) {
    _editor.value = initialChar;
    _editor.selectionStart = _editor.selectionEnd = 1;
  } else {
    _editor.value = editValue(_sel.col, _sel.row);
    _editor.select();
  }

  _editor.focus();
}

function _commitEdit() {
  if (!_editMode) return;
  _editMode             = false;
  _editor.style.display = 'none';
  _onCommit?.(_sel.col, _sel.row, _editor.value);
  draw();
  _onSelect?.(_sel.col, _sel.row, _rangeEnd);
}

/**
 * Flush any in-progress cell edit into the model.
 * Call before saving drafts or navigating away — blur may not fire if the
 * tab-click handler called e.preventDefault() and focus never moved.
 */
export function commitEdit() { _commitEdit(); }

function _editorKeyDown(e) {
  switch (e.key) {
    case 'Escape':
      _editMode             = false;
      _editor.style.display = 'none';
      draw();
      break;
    case 'Enter':
      e.preventDefault();
      _commitEdit();
      _move(0, 1, false);
      break;
    case 'Tab':
      e.preventDefault();
      _commitEdit();
      _move(e.shiftKey ? -1 : 1, 0, false);
      break;
  }
}
