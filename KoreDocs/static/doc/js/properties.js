/**
 * properties.js — Document properties panel + document map for KoreDoc.
 *
 * Displays file name, YAML frontmatter fields, word/line/char counts,
 * and a live heading outline (document map) for navigating the document.
 */

import { parseFrontmatter } from './editor.js';

const _panel = document.getElementById('props-content');
const _map   = document.getElementById('map-content');

export function refresh(text, currentName) {
  const { meta } = parseFrontmatter(text);
  const lines    = text.split('\n').length;
  const words    = text.trim() ? text.trim().split(/\s+/).length : 0;
  const chars    = text.length;

  const metaRows = Object.entries(meta)
    .map(([k, v]) =>
      `<div class="prop-row"><label>${_esc(k)}</label><span class="hint">${_esc(v)}</span></div>`
    ).join('');

  _panel.innerHTML = `
    <div class="prop-row">
      <label>File</label>
      <span class="hint">${_esc(currentName ?? 'Unsaved')}</span>
    </div>
    ${metaRows}
    <div class="prop-row">
      <label>Stats</label>
      <span class="hint">${lines} lines · ${words} words · ${chars} chars</span>
    </div>
  `;

  _refreshMap(text);
}

// ── Document map ────────────────────────────────────────────────────────────

function _extractHeadings(text) {
  const lines   = text.split('\n');
  const out     = [];
  let inFm = false, fmDone = false;

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    if (i === 0 && raw === '---') { inFm = true; continue; }
    if (inFm && !fmDone) { if (raw === '---') { inFm = false; fmDone = true; } continue; }

    const m = raw.match(/^(#{1,6}) (.+)/);
    if (m) out.push({ level: m[1].length, text: m[2], lineIndex: i });
  }
  return out;
}

function _refreshMap(text) {
  if (!_map) return;
  const headings = _extractHeadings(text);

  if (!headings.length) {
    _map.innerHTML = '<span class="map-empty">No headings yet.</span>';
    return;
  }

  _map.innerHTML = headings.map((h, idx) =>
    `<button class="map-item" data-level="${h.level}" data-idx="${idx}"
             title="${_esc(h.text)}">${_esc(h.text)}</button>`
  ).join('');

  _map.querySelectorAll('.map-item').forEach(btn => {
    btn.addEventListener('click', () => {
      const idx = parseInt(btn.dataset.idx, 10);
      document.dispatchEvent(new CustomEvent('kd:goto-heading', { detail: idx }));
    });
  });
}

function _esc(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
