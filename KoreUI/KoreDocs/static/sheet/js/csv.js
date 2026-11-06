import { COLS, ROWS } from './cell.js';

function _csvCellValue(raw) {
  const text = String(raw ?? '');
  const trimmed = text.trim();
  if (trimmed === '') return '';
  const numeric = Number(trimmed);
  return Number.isFinite(numeric) && String(numeric) === trimmed ? numeric : text;
}

function _parseCsvRows(text) {
  const rows = [];
  let row = [];
  let value = '';
  let inQuotes = false;
  const normalized = String(text || '').replace(/^\uFEFF/, '').replace(/\r\n/g, '\n').replace(/\r/g, '\n');

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
  if (inQuotes) throw new Error('CSV contains an unterminated quoted field.');
  return rows;
}

function _columnName(column) {
  let index = column;
  let name = '';
  while (index > 0) {
    const remainder = (index - 1) % 26;
    name = String.fromCharCode(65 + remainder) + name;
    index = Math.floor((index - 1) / 26);
  }
  return name;
}

export function csvToSheetJson(text, fileName) {
  const rows = _parseCsvRows(text);
  const widestRow = rows.reduce((width, currentRow) => Math.max(width, currentRow.length), 0);
  const previewRows = Math.min(rows.length, ROWS);
  const previewCols = Math.min(widestRow, COLS);
  const cells = {};
  for (let rowIndex = 0; rowIndex < previewRows; rowIndex += 1) {
    const currentRow = rows[rowIndex];
    for (let colIndex = 0; colIndex < Math.min(currentRow.length, previewCols); colIndex += 1) {
      const raw = currentRow[colIndex];
      if (raw === '') continue;
      cells[`${_columnName(colIndex + 1)}${rowIndex + 1}`] = { value: _csvCellValue(raw) };
    }
  }
  return JSON.stringify({
    version: 1,
    meta: {
      title: String(fileName || '').replace(/\.[^.]+$/, '') || 'Untitled',
      csvPreview: {
        sourceRows: rows.length,
        sourceCols: widestRow,
        previewRows,
        previewCols,
      },
    },
    cols: COLS,
    rows: ROWS,
    cells,
  });
}

export function limitCsvPreview(text, rows, cols) {
  const rowLimit = Number(rows);
  const colLimit = Number(cols);
  if (!Number.isInteger(rowLimit) || rowLimit < 1 || rowLimit > ROWS
      || !Number.isInteger(colLimit) || colLimit < 1 || colLimit > COLS) {
    throw new Error(`Choose between 1-${ROWS} rows and 1-${COLS} columns.`);
  }
  const sheet = JSON.parse(text);
  for (const address of Object.keys(sheet.cells || {})) {
    const match = /^([A-Z]+)(\d+)$/.exec(address);
    if (!match) continue;
    let column = 0;
    for (const letter of match[1]) column = column * 26 + letter.charCodeAt(0) - 64;
    if (column > colLimit || Number(match[2]) > rowLimit) delete sheet.cells[address];
  }
  if (sheet.meta?.csvPreview) {
    sheet.meta.csvPreview.selectedRows = rowLimit;
    sheet.meta.csvPreview.selectedCols = colLimit;
  }
  return JSON.stringify(sheet);
}
