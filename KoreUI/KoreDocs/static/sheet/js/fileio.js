/**
 * fileio.js — KoreFile-backed file state + autosave for KoreSheet.
 */

import { createKorefileSyncController } from '/static/shared/js/korefileSyncController.js';
import { fromJSON } from './cell.js';

function _isCsvFile(fileOrName) {
  const name = typeof fileOrName === 'string' ? fileOrName : fileOrName?.name;
  return String(name || '').toLowerCase().endsWith('.csv');
}

function _csvCellValue(raw) {
  const text = String(raw ?? '');
  const trimmed = text.trim();
  if (trimmed === '') return '';
  const numeric = Number(trimmed);
  return Number.isNaN(numeric) ? text : numeric;
}

function _parseCsvRows(text) {
  const rows = [];
  let row = [];
  let value = '';
  let inQuotes = false;
  const normalized = String(text || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');

  for (let index = 0; index < normalized.length; index += 1) {
    const ch = normalized[index];
    if (inQuotes) {
      if (ch === '"') {
        if (normalized[index + 1] === '"') {
          value += '"';
          index += 1;
        } else {
          inQuotes = false;
        }
      } else {
        value += ch;
      }
      continue;
    }

    if (ch === '"') {
      inQuotes = true;
      continue;
    }
    if (ch === ',') {
      row.push(value);
      value = '';
      continue;
    }
    if (ch === '\n') {
      row.push(value);
      rows.push(row);
      row = [];
      value = '';
      continue;
    }
    value += ch;
  }

  if (value !== '' || row.length) {
    row.push(value);
    rows.push(row);
  }
  return rows;
}

function _csvToSheetJson(text, fileName) {
  const rows = _parseCsvRows(text);
  const cells = {};
  for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
    const row = rows[rowIndex];
    for (let colIndex = 0; colIndex < row.length; colIndex += 1) {
      const raw = row[colIndex];
      if (raw === '') continue;
      const colName = String.fromCharCode(65 + colIndex);
      cells[`${colName}${rowIndex + 1}`] = { value: _csvCellValue(raw) };
    }
  }
  return JSON.stringify({
    version: 1,
    meta: { title: String(fileName || '').replace(/\.[^.]+$/, '') || 'Untitled' },
    cols: 26,
    rows: 100,
    cells,
  });
}

function _sheetJsonToCsv(text) {
  const obj = JSON.parse(text || '{}');
  const cells = obj?.cells && typeof obj.cells === 'object' ? obj.cells : {};
  let maxRow = 0;
  let maxCol = 0;
  for (const addr of Object.keys(cells)) {
    const match = /^([A-Z]+)(\d+)$/.exec(addr);
    if (!match) continue;
    let col = 0;
    for (const ch of match[1]) col = (col * 26) + (ch.charCodeAt(0) - 64);
    const row = parseInt(match[2], 10);
    maxCol = Math.max(maxCol, col);
    maxRow = Math.max(maxRow, row);
  }
  if (!maxRow || !maxCol) return '';

  const csvRows = [];
  for (let row = 1; row <= maxRow; row += 1) {
    const values = [];
    for (let col = 1; col <= maxCol; col += 1) {
      let index = col;
      let letters = '';
      while (index > 0) {
        const rem = (index - 1) % 26;
        letters = String.fromCharCode(65 + rem) + letters;
        index = Math.floor((index - 1) / 26);
      }
      const cell = cells[`${letters}${row}`];
      let value = '';
      if (cell && typeof cell === 'object') {
        if (cell.formula != null) value = cell.formula;
        else if (cell.value != null) value = cell.value;
      }
      const textValue = String(value ?? '');
      values.push(/[",\n]/.test(textValue) ? `"${textValue.replace(/"/g, '""')}"` : textValue);
    }
    csvRows.push(values.join(','));
  }
  return csvRows.join('\n');
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
      fromJSON(source ? _csvToSheetJson(source, file.name) : _blankSheet(file.name));
    } else {
      fromJSON(content);
    }
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
export function queueAutosave(text) {
  if (_isCsvFile(_controller.currentName())) {
    _controller.queueAutosave(_sheetJsonToCsv(text));
    return;
  }
  _controller.queueAutosave(text);
}

export const flushAutosave = _controller.flushAutosave;
