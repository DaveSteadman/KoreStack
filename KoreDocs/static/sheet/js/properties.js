/**
 * properties.js — Properties panel for KoreSheet.
 *
 * Single cell: shows address, formula/value, and style controls.
 * Range selected: shows live aggregate preview (SUM, AVG, COUNT, MIN, MAX).
 */

import { getCell, displayValue, getCellStyle, getAllCells } from './cell.js';
import { colLetter, addrOf, evaluate }                     from './formula.js';

const _panel        = document.getElementById('props-content');
const _rangePreview = document.getElementById('range-preview');

export function refresh(col, row, rangeEnd) {
  _refreshPanel(col, row, rangeEnd);
  _refreshRangePreview(col, row, rangeEnd);
}

// ── Panel ──────────────────────────────────────────────────────────────────

function _refreshPanel(col, row, rangeEnd) {
  if (rangeEnd) {
    _showRangePanel(col, row, rangeEnd);
    return;
  }

  const addr  = addrOf(col, row);
  const cell  = getCell(col, row);
  const val   = displayValue(col, row);
  const style = cell?.style ?? {};

  const formulaRow = cell?.formula
    ? `<div class="prop-row"><label>Formula</label><span class="hint">${_esc(cell.formula)}</span></div>`
    : '';

  _panel.innerHTML = `
    <div class="prop-row"><label>Cell</label><span class="hint">${addr}</span></div>
    ${formulaRow}
    <div class="prop-row"><label>Value</label><span class="hint">${_esc(String(val))}</span></div>
    <div class="prop-row">
      <label>Style</label>
      <div class="style-row">
        <button class="style-btn ${style.bold   ? 'active' : ''}" data-style="bold"   title="Bold">  <b>B</b></button>
        <button class="style-btn ${style.italic ? 'active' : ''}" data-style="italic" title="Italic"><i>I</i></button>
        <select data-style="align" title="Align">
          <option value="left"   ${(!style.align || style.align === 'left')   ? 'selected' : ''}>Left</option>
          <option value="center" ${style.align === 'center' ? 'selected' : ''}>Center</option>
          <option value="right"  ${style.align === 'right'  ? 'selected' : ''}>Right</option>
        </select>
      </div>
      <div class="style-row" style="margin-top:5px;gap:6px">
        <span style="font-size:10px;color:var(--text-dim)">Fill</span>
        <input type="color" data-style="fillColor" value="${style.fillColor ?? '#1e1e2e'}" />
        <span style="font-size:10px;color:var(--text-dim)">Text</span>
        <input type="color" data-style="textColor" value="${style.textColor ?? '#cdd6f4'}" />
      </div>
      <div class="style-row" style="margin-top:5px;gap:6px">
        <span style="font-size:10px;color:var(--text-dim)">Decimals</span>
        <button class="style-btn" data-decimal-action="decrease" title="Decrease decimals">-</button>
        <input class="style-input" type="number" min="0" max="12" step="1" data-style="decimalPlaces" value="${Number.isInteger(style.decimalPlaces) ? style.decimalPlaces : ''}" placeholder="auto" />
        <button class="style-btn" data-decimal-action="increase" title="Increase decimals">+</button>
        <button class="style-btn style-btn-wide" data-decimal-action="clear" title="Automatic decimals">Auto</button>
      </div>
    </div>
  `;

  _wirePanelControls({ col, row, rangeEnd: null });
}

function _showRangePanel(col, row, rangeEnd) {
  const c1 = Math.min(col, rangeEnd.col);
  const r1 = Math.min(row, rangeEnd.row);
  const c2 = Math.max(col, rangeEnd.col);
  const r2 = Math.max(row, rangeEnd.row);

  const rangeStr = `${colLetter(c1)}${r1 + 1}:${colLetter(c2)}${r2 + 1}`;
  const cells    = getAllCells();

  const fns = ['SUM', 'AVERAGE', 'COUNT', 'MIN', 'MAX'];
  const rows = fns.map(fn => {
    const result = evaluate(`=${fn}(${rangeStr})`, cells);
    return `<div class="prop-row"><label>${fn}</label><span class="hint">${result}</span></div>`;
  }).join('');

  _panel.innerHTML = `
    <div class="prop-row"><label>Range</label><span class="hint">${rangeStr}</span></div>
    <div class="prop-row">
      <label>Decimals</label>
      <div class="style-row">
        <button class="style-btn" data-decimal-action="decrease" title="Decrease decimals for selection">-</button>
        <button class="style-btn" data-decimal-action="increase" title="Increase decimals for selection">+</button>
        <button class="style-btn style-btn-wide" data-decimal-action="clear" title="Automatic decimals for selection">Auto</button>
      </div>
    </div>
    ${rows}
  `;

  _wirePanelControls({ col, row, rangeEnd });
}

// ── Range preview in toolbar ───────────────────────────────────────────────

function _refreshRangePreview(col, row, rangeEnd) {
  if (!_rangePreview) return;
  if (!rangeEnd) { _rangePreview.textContent = ''; return; }

  const c1    = Math.min(col, rangeEnd.col);
  const r1    = Math.min(row, rangeEnd.row);
  const c2    = Math.max(col, rangeEnd.col);
  const r2    = Math.max(row, rangeEnd.row);
  const count = (c2 - c1 + 1) * (r2 - r1 + 1);

  _rangePreview.textContent =
    `${colLetter(c1)}${r1 + 1}:${colLetter(c2)}${r2 + 1}  (${count} cells)`;
}

// ── Helpers ────────────────────────────────────────────────────────────────

function _esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function _wirePanelControls(context) {
  _panel.querySelectorAll('[data-style]').forEach(el => {
    const evtName = el.tagName === 'INPUT'
      ? (el.type === 'color' ? 'input' : 'change')
      : el.tagName === 'SELECT'
        ? 'change'
        : 'click';
    el.addEventListener(evtName, () => {
      _panel.dispatchEvent(new CustomEvent('ks:style-change', {
        bubbles: true,
        detail: {
          col: context.col,
          row: context.row,
          rangeEnd: context.rangeEnd,
          prop:  el.dataset.style,
          value: _styleControlValue(el),
          toggle: el.classList.contains('style-btn'),
        },
      }));
    });
  });

  _panel.querySelectorAll('[data-decimal-action]').forEach(el => {
    el.addEventListener('click', () => {
      _panel.dispatchEvent(new CustomEvent('ks:decimal-action', {
        bubbles: true,
        detail: {
          col: context.col,
          row: context.row,
          rangeEnd: context.rangeEnd,
          action: el.dataset.decimalAction,
        },
      }));
    });
  });
}

function _styleControlValue(el) {
  if (el.tagName === 'SELECT') return el.value;
  if (el.tagName === 'INPUT' && el.type === 'color') return el.value;
  if (el.tagName === 'INPUT' && el.type === 'number') return el.value;
  return true;
}
