/**
 * cell.js — KoreSheet cell model and sheet state.
 *
 * All mutations go through the exported setter functions.
 * Serialisation / deserialisation is also here.
 */

import { addrOf, evaluate, isFormula } from './formula.js';

export const COLS         = 26;
export const ROWS         = 100;
export const COL_W        = 80;
export const ROW_H        = 24;
export const ROW_HEADER_W = 40;
export const COL_HEADER_H = 24;

// ── Sheet state ────────────────────────────────────────────────────────────

let _cells = {};  // { "A1": { value, formula?, computed?, style? }, … }
let _meta  = { title: 'Untitled', created: '' };

export function newSheet() {
  _cells = {};
  _meta  = { title: 'Untitled', created: new Date().toISOString().slice(0, 10) };
}

// ── Accessors ──────────────────────────────────────────────────────────────

export function getCell(col, row)     { return _cells[addrOf(col, row)] ?? null; }
export function getCellByAddr(addr)   { return _cells[addr]              ?? null; }
export function getAllCells()         { return _cells; }
export function getMeta()             { return _meta; }

/** Display value: computed result if formula, otherwise the stored value. */
export function displayValue(col, row) {
  const c = getCell(col, row);
  if (!c) return '';
  const raw = c.formula != null ? (c.computed ?? '') : (c.value ?? '');
  return _formatDisplayValue(raw, c.style ?? {});
}

/** Edit value: formula string if formula, else raw value as a string. */
export function editValue(col, row) {
  const c = getCell(col, row);
  if (!c) return '';
  return c.formula ?? String(c.value ?? '');
}

export function getCellStyle(col, row) {
  return getCell(col, row)?.style ?? {};
}

// ── Mutators ───────────────────────────────────────────────────────────────

/**
 * Set a cell from a raw string (as typed in the editor input or formula bar).
 * Detects formulas, parses numbers, otherwise stores as a string.
 * After writing, recomputes all formula cells so dependents stay current.
 */
export function setCellRaw(col, row, raw) {
  const addr = addrOf(col, row);
  const trimmed = (raw ?? '').trim();

  if (trimmed === '') {
    // Preserve style if present
    const existing = _cells[addr];
    if (existing?.style) {
      _cells[addr] = { value: '', style: existing.style };
    } else {
      delete _cells[addr];
    }
    _recomputeAll();
    return;
  }

  if (isFormula(trimmed)) {
    const computed = evaluate(trimmed, _cells);
    _cells[addr] = { ..._cells[addr], formula: trimmed, computed, value: trimmed };
    _recomputeAll();
    return;
  }

  const n = Number(trimmed);
  _cells[addr] = { ..._cells[addr], value: isNaN(n) ? trimmed : n };
  // Clear any previous formula if overwriting with a plain value
  if (_cells[addr].formula) {
    delete _cells[addr].formula;
    delete _cells[addr].computed;
  }
  _recomputeAll();
}

/** Set a formula directly (e.g. from a toolbar aggregate button). */
export function setCellFormula(col, row, formula) {
  const addr     = addrOf(col, row);
  const computed = evaluate(formula, _cells);
  _cells[addr]   = { ..._cells[addr], formula, computed, value: formula };
  _recomputeAll();
}

/** Merge a style patch into a cell's style object. */
export function setCellStyle(col, row, patch) {
  const addr = addrOf(col, row);
  if (!_cells[addr]) _cells[addr] = { value: '' };
  _cells[addr].style = _mergeStylePatch(_cells[addr].style ?? {}, patch);
}

export function setRangeStyle(startCol, startRow, endCol, endRow, patch) {
  const c1 = Math.min(startCol, endCol);
  const r1 = Math.min(startRow, endRow);
  const c2 = Math.max(startCol, endCol);
  const r2 = Math.max(startRow, endRow);
  for (let row = r1; row <= r2; row++) {
    for (let col = c1; col <= c2; col++) {
      setCellStyle(col, row, patch);
    }
  }
}

export function adjustRangeDecimalPlaces(startCol, startRow, endCol, endRow, delta) {
  const c1 = Math.min(startCol, endCol);
  const r1 = Math.min(startRow, endRow);
  const c2 = Math.max(startCol, endCol);
  const r2 = Math.max(startRow, endRow);
  for (let row = r1; row <= r2; row++) {
    for (let col = c1; col <= c2; col++) {
      const current = _coerceDecimalPlaces(getCellStyle(col, row).decimalPlaces);
      const next = Math.max(0, (current ?? 0) + delta);
      setCellStyle(col, row, { decimalPlaces: next });
    }
  }
}

export function clearRangeDecimalPlaces(startCol, startRow, endCol, endRow) {
  setRangeStyle(startCol, startRow, endCol, endRow, { decimalPlaces: null });
}

// ── Internal helpers ───────────────────────────────────────────────────────

/**
 * Re-evaluate every formula cell against the current _cells state.
 * Must be called after any value mutation so dependents stay in sync.
 */
function _recomputeAll() {
  for (const c of Object.values(_cells)) {
    if (c.formula) {
      c.computed = evaluate(c.formula, _cells);
    }
  }
}

function _formatDisplayValue(raw, style) {
  const decimalPlaces = _coerceDecimalPlaces(style?.decimalPlaces);
  if (decimalPlaces == null) return raw;
  if (typeof raw !== 'number' || !Number.isFinite(raw)) return raw;
  return raw.toFixed(decimalPlaces);
}

function _coerceDecimalPlaces(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 0) return null;
  return Math.min(parsed, 12);
}

function _mergeStylePatch(style, patch) {
  const merged = { ...style };
  for (const [key, value] of Object.entries(patch ?? {})) {
    if (value === null || value === undefined || value === '') {
      delete merged[key];
      continue;
    }
    if (key === 'decimalPlaces') {
      const normalized = _coerceDecimalPlaces(value);
      if (normalized == null) {
        delete merged[key];
      } else {
        merged[key] = normalized;
      }
      continue;
    }
    merged[key] = value;
  }
  return merged;
}

// ── Serialisation ──────────────────────────────────────────────────────────

export function toJSON() {
  return JSON.stringify(
    { version: 1, meta: _meta, cols: COLS, rows: ROWS, cells: _cells },
    null, 2
  );
}

export function fromJSON(text) {
  const obj = JSON.parse(text);
  _meta  = obj.meta  ?? { title: 'Untitled' };
  _cells = obj.cells ?? {};

  _recomputeAll();
}
