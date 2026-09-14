/**
 * properties.js — Document properties panel + document map for KoreDoc.
 *
 * Displays file name, YAML frontmatter fields, word/line/char counts,
 * and a live heading outline (document map) for navigating the document.
 */

import { parseFrontmatter } from './editor.js?v=20260911a';

const _panel = document.getElementById('props-content');
const _map   = document.getElementById('map-content');
let _lastText = '';
let _lastName = null;
let _artefact = { metadata: {}, revision: null, history: [] };

export function refresh(text, currentName, artefact = {}) {
  _lastText = text;
  _lastName = currentName;
  _artefact = {
    ..._artefact,
    ...artefact,
    metadata: artefact.metadata && typeof artefact.metadata === 'object'
      ? artefact.metadata
      : _artefact.metadata,
  };
  const { meta } = parseFrontmatter(text);
  const bodyText = typeof _artefact.bodyText === 'string' ? _artefact.bodyText : text;
  const lines    = bodyText.split('\n').length;
  const words    = bodyText.trim() ? bodyText.trim().split(/\s+/).length : 0;
  const chars    = bodyText.length;

  const metaRows = Object.entries(meta)
    .map(([k, v]) =>
      `<div class="prop-row"><label>${_esc(k)}</label><span class="hint">${_esc(v)}</span></div>`
    ).join('');

  const provenanceRows = _provenanceRows(_artefact.metadata, _artefact.revision, _artefact.history);

  _panel.innerHTML = `
    <div class="prop-row">
      <label>File</label>
      <span class="hint">${_esc(currentName ?? 'Unsaved')}</span>
    </div>
    ${metaRows}
    ${provenanceRows}
    <div class="prop-row">
      <label>Stats</label>
      <span class="hint">${lines} lines &middot; ${words} words &middot; ${chars} chars</span>
    </div>
  `;

  _refreshMap(bodyText);
}

export function setHistory(history) {
  _artefact = { ..._artefact, history: Array.isArray(history) ? history : [] };
  refresh(_lastText, _lastName);
}

function _provenanceRows(metadata, revision, history) {
  const producer = metadata?.producer?.service || metadata?.producer || '';
  const sourceRefs = Array.isArray(metadata?.source_refs) ? metadata.source_refs : [];
  const latest = history[0] || null;
  const rows = [];

  if (producer) rows.push(_row('Produced by', typeof producer === 'string' ? producer : JSON.stringify(producer)));
  if (sourceRefs.length) rows.push(_row('Sources', `${sourceRefs.length} linked artefact${sourceRefs.length === 1 ? '' : 's'}`));
  if (revision) rows.push(_row('Revision', revision));
  if (latest) rows.push(_row('Latest change', `${latest.action || 'update'} | ${_formatDate(latest.recorded_at)}`));
  if (history.length) rows.push(_row('History', `${history.length} revision${history.length === 1 ? '' : 's'} available`));
  return rows.join('');
}

function _row(label, value) {
  return `<div class="prop-row"><label>${_esc(label)}</label><span class="hint">${_esc(value)}</span></div>`;
}

function _formatDate(value) {
  if (!value) return 'unknown time';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString();
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
