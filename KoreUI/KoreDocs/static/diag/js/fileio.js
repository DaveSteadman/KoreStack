/**
 * fileio.js — KoreFile-backed file state + autosave for KoreDiag, plus PNG export.
 */

import { createKorefileSyncController } from '/static/shared/js/korefileSyncController.js?v=20261117a';
import { initKorefileSaveStatus } from '/static/shared/js/saveStatus.js';
import { newDiagram, newNode, toGridInt } from './model.js';
import {
  getDiagram, isDirty, markDirty, markSaved,
  dispatch, cmdAddNode, selection,
} from './store.js';

const _controller = createKorefileSyncController({
  logLabel: 'KoreDiag',
  alertLabel: 'Diagram',
  legacyType: 'korediag',
  buildBlankContent: title => JSON.stringify(newDiagram(title)),
  applyLoadedContent: (content, file, loadContent) => {
    loadContent?.(normalizeDiagram(JSON.parse(content)));
  },
  onMarkDirty: () => markDirty(),
  onMarkSaved: () => markSaved(),
  hasExternalUnsavedChanges: () => isDirty(),
  onSaveSuccess: () => document.dispatchEvent(new CustomEvent('kd:autosaved')),
  onAfterApplyRemote: () => document.dispatchEvent(new CustomEvent('kd:autosaved')),
});

initKorefileSaveStatus(() => _controller.flushAutosave());

export const currentId = _controller.currentId;
export const currentName = _controller.currentName;
export const currentRevision = _controller.currentRevision;
export const autoOpenFromUrl = _controller.autoOpenFromUrl;
export const reloadCurrent = _controller.reloadCurrent;
export const queueAutosave = _controller.queueAutosave;
export const flushAutosave = _controller.flushAutosave;

/** Export all native objects and embedded SVG artwork as a standalone SVG file. */
export function exportSvg() {
  const diagram  = getDiagram();
  const bounds   = diagramBounds(diagram);
  if (!bounds) {
    void window.kcuiAlert('Export SVG', 'Nothing to export.');
    return;
  }

  const padding  = diagram.settings.gridSize || 20;
  const viewBox  = `${bounds.x - padding} ${bounds.y - padding} ${bounds.width + padding * 2} ${bounds.height + padding * 2}`;
  const svg = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<svg xmlns="http://www.w3.org/2000/svg" data-korediag-units="world" viewBox="${viewBox}" role="img" aria-label="${escapeXml(diagram.title || 'KoreDiag diagram')}">`,
    '<defs><marker id="korediag-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs>',
    ...svgEdges(diagram),
    ...flattenNodes(diagram.nodes).map(node => svgNode(node, diagram)),
    '</svg>',
  ].filter(Boolean).join('\n');
  downloadText(svg, `${safeFilename(diagram.title || 'diagram')}.svg`, 'image/svg+xml;charset=utf-8');
}

/** Import an SVG without rasterisation; its original markup remains embedded in the diagram. */
export async function importSvg() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.svg,image/svg+xml';
  input.click();
  const file = await new Promise(resolve => {
    input.addEventListener('change', () => resolve(input.files?.[0] || null), { once: true });
  });
  if (!file) return;

  const markup     = await file.text();
  const dimensions = svgDimensions(markup);
  if (!dimensions) {
    void window.kcuiAlert('Import SVG', 'That file does not contain a valid SVG document.');
    return;
  }

  const currentBounds = diagramBounds(getDiagram());
  const gridSize      = getDiagram().settings.gridSize || 20;
  const scale         = dimensions.usesDiagramUnits ? 1 : 1 / gridSize;
  const node = newNode(
    'svg',
    currentBounds ? currentBounds.x + currentBounds.width + 20 : 0,
    currentBounds?.y || 0,
    dimensions.width  * scale,
    dimensions.height * scale,
    '',
  );
  node.svg = { markup, viewBox: dimensions.viewBox };
  node.meta = { sourceName: file.name };
  dispatch(cmdAddNode(node));
  selection.clear();
  selection.add(node.id);
  document.dispatchEvent(new CustomEvent('kd:selection-change'));
}

function svgDimensions(markup) {
  const documentNode = new DOMParser().parseFromString(markup, 'image/svg+xml');
  const root         = documentNode.documentElement;
  if (root.localName !== 'svg' || documentNode.querySelector('parsererror')) return null;
  const viewBox = root.getAttribute('viewBox') || '';
  const parts   = viewBox.trim().split(/[\s,]+/).map(Number);
  const width   = svgNumber(root.getAttribute('width')) || (parts[2] > 0 ? parts[2] : 300);
  const height  = svgNumber(root.getAttribute('height')) || (parts[3] > 0 ? parts[3] : 200);
  return {
    width: Math.max(1, width),
    height: Math.max(1, height),
    viewBox,
    usesDiagramUnits: root.getAttribute('data-korediag-units') === 'world',
  };
}

function svgNumber(value) {
  const match = String(value || '').trim().match(/^([+-]?(?:\d+\.?\d*|\.\d+))/);
  return match ? Number(match[1]) : 0;
}

function diagramBounds(diagram) {
  const nodeMap = window._koreModel?.buildNodeMap(diagram.nodes);
  if (!nodeMap?.size) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const [id, entry] of nodeMap) {
    if (entry.node.type === 'waypoint') continue;
    const box = window._koreModel.worldBounds(id, nodeMap);
    minX = Math.min(minX, box.x);
    minY = Math.min(minY, box.y);
    maxX = Math.max(maxX, box.x + box.width);
    maxY = Math.max(maxY, box.y + box.height);
  }
  return Number.isFinite(minX) ? { x: minX, y: minY, width: maxX - minX, height: maxY - minY } : null;
}

function flattenNodes(nodes, offsetX = 0, offsetY = 0, result = []) {
  for (const node of nodes) {
    const x = offsetX + node.x;
    const y = offsetY + node.y;
    result.push({ ...node, x, y });
    if (node.children?.length) flattenNodes(node.children, x, y, result);
  }
  return result;
}

function svgEdges(diagram) {
  const nodeMap = window._koreModel.buildNodeMap(diagram.nodes);
  return diagram.edges.map(edge => {
    const from = window._koreModel.worldBounds(edge.from, nodeMap);
    const to = window._koreModel.worldBounds(edge.to, nodeMap);
    if (!from || !to) return '';
    const viaPoints = (edge.via || [])
      .map(id => window._koreModel.worldBounds(id, nodeMap))
      .filter(Boolean)
      .map(svgCentre);
    const points    = [svgCentre(from), ...viaPoints, svgCentre(to)];
    const style     = edge.style || {};
    const arrow     = edge.arrow ?? diagram.settings.defaultArrow;
    const markerStart = ['backward', 'both', 'bidirectional'].includes(arrow)
      ? ' marker-start="url(#korediag-arrow)"'
      : '';
    const markerEnd = ['forward', 'both', 'bidirectional'].includes(arrow)
      ? ' marker-end="url(#korediag-arrow)"'
      : '';
    const dash = style.dashed ? ' stroke-dasharray="6 4"' : '';
    const pointList = points.map(point => `${point.x},${point.y}`).join(' ');
    const path = `<polyline points="${pointList}" fill="none" stroke="${escapeXml(style.strokeColor || '#6f6f6f')}" stroke-width="${style.strokeWidth || 1.5}"${dash}${markerStart}${markerEnd}/>`;
    if (!edge.label) return path;
    const middle = points[Math.floor(points.length / 2)];
    return `${path}<text x="${middle.x}" y="${middle.y - 4}" fill="${escapeXml(style.strokeColor || '#6f6f6f')}" font-family="system-ui, sans-serif" font-size="${style.fontSize || 11}" text-anchor="middle">${escapeXml(edge.label)}</text>`;
  });
}

function svgNode(node, diagram) {
  if (node.type === 'waypoint') return '';
  const style = { ...(diagram.settings.defaultNodeStyle || {}), ...(node.style || {}) };
  const paint = `fill="${escapeXml(style.fillColor || '#1f1f1f')}" stroke="${escapeXml(style.strokeColor || '#6f6f6f')}" stroke-width="${style.strokeWidth || 1.5}"`;
  let shape = '';
  if (node.type === 'svg') shape = nestedSvg(node);
  else if (node.type === 'ellipse') {
    shape = `<ellipse cx="${node.x + node.width / 2}" cy="${node.y + node.height / 2}" rx="${node.width / 2}" ry="${node.height / 2}" ${paint}/>`;
  } else if (node.type === 'diamond') {
    shape = `<polygon points="${node.x + node.width / 2},${node.y} ${node.x + node.width},${node.y + node.height / 2} ${node.x + node.width / 2},${node.y + node.height} ${node.x},${node.y + node.height / 2}" ${paint}/>`;
  } else if (node.type === 'polygon' && node.points?.length >= 3) {
    const points = node.points
      .map(point => `${node.x + point.x},${node.y + point.y}`)
      .join(' ');
    shape = `<polygon points="${points}" ${paint}/>`;
  } else if (node.type === 'rounded') {
    shape = `<rect x="${node.x}" y="${node.y}" width="${node.width}" height="${node.height}" rx="${Math.min(node.width, node.height) * .14}" ${paint}/>`;
  } else if (node.type !== 'text') {
    shape = `<rect x="${node.x}" y="${node.y}" width="${node.width}" height="${node.height}" ${paint}/>`;
  }
  return shape + svgLabel(node, style);
}

function nestedSvg(node) {
  const markup = node.svg?.markup || node.svgMarkup;
  if (!markup) return '';
  const parsed = new DOMParser().parseFromString(markup, 'image/svg+xml');
  const root = parsed.documentElement;
  if (root.localName !== 'svg' || parsed.querySelector('parsererror')) return '';
  root.setAttribute('x', String(node.x));
  root.setAttribute('y', String(node.y));
  root.setAttribute('width', String(node.width));
  root.setAttribute('height', String(node.height));
  root.setAttribute('preserveAspectRatio', root.getAttribute('preserveAspectRatio') || 'xMidYMid meet');
  return new XMLSerializer().serializeToString(root);
}

function svgLabel(node, style) {
  if (!node.label) return '';
  const alignment = style.labelHAlign || 'center';
  const vertical  = style.labelVAlign || 'middle';
  const x = node.x + (alignment === 'left' ? 8 : alignment === 'right' ? node.width - 8 : node.width / 2);
  const y = node.y + (vertical === 'top' ? 8 : vertical === 'bottom' ? node.height - 8 : node.height / 2);
  const anchor = alignment === 'left' ? 'start' : alignment === 'right' ? 'end' : 'middle';
  const baseline = vertical === 'top' ? 'hanging' : vertical === 'bottom' ? 'text-after-edge' : 'middle';
  const fill = node.type === 'text'
    ? (style.fontColor || style.strokeColor || '#cdd6f4')
    : contrastText(style.fillColor || '#1f1f1f');
  const fontStyle = `${style.bold ? ' font-weight="bold"' : ''}${style.italic ? ' font-style="italic"' : ''}`;
  return `<text x="${x}" y="${y}" fill="${escapeXml(fill)}" font-family="system-ui, sans-serif" font-size="${style.fontSize || 13}" text-anchor="${anchor}" dominant-baseline="${baseline}"${fontStyle}>${String(node.label).split(/\r?\n/).map((line, index) => `<tspan x="${x}" dy="${index ? '1.2em' : '0'}">${escapeXml(line)}</tspan>`).join('')}</text>`;
}

function svgCentre(box) {
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

function contrastText(hex) {
  const color = String(hex).replace('#', '');
  const red   = parseInt(color.slice(0, 2), 16);
  const green = parseInt(color.slice(2, 4), 16);
  const blue  = parseInt(color.slice(4, 6), 16);
  return (red * 299 + green * 587 + blue * 114) / 1000 > 128 ? '#1e1e2e' : '#cdd6f4';
}

function escapeXml(value) {
  const escaped = { '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;', "'": '&apos;' };
  return String(value).replace(/[<>&"']/g, char => escaped[char]);
}

function safeFilename(value) {
  return value.replace(/[<>:"/\\|?*]+/g, '-').trim() || 'diagram';
}

function downloadText(text, name, type) {
  const url  = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function serialise() {
  return JSON.stringify(getDiagram(), null, 2);
}

function normalizeDiagram(diagram) {
  const normalized = { ...diagram };
  normalized.nodes = Array.isArray(normalized.nodes)
    ? normalized.nodes.map(normalizeNode)
    : [];
  normalized.edges = Array.isArray(normalized.edges)
    ? normalized.edges.map(normalizeEdge)
    : [];
  return normalized;
}

function normalizeNode(node) {
  const normalized = { ...node };
  if (typeof normalized.label === 'string') normalized.label = normalized.label.replace(/\\n/g, '\n');
  if (normalized.width == null && normalized.w != null) normalized.width = normalized.w;
  if (normalized.height == null && normalized.h != null) normalized.height = normalized.h;
  normalized.x = toGridInt(normalized.x);
  normalized.y = toGridInt(normalized.y);
  normalized.width = Math.max(1, toGridInt(normalized.width));
  normalized.height = Math.max(1, toGridInt(normalized.height));
  if (normalized.type === 'polygon') {
    normalized.points = Array.isArray(normalized.points)
      ? normalized.points
        .filter(point => point && Number.isFinite(Number(point.x)) && Number.isFinite(Number(point.y)))
        .map(point => ({ x: toGridInt(point.x), y: toGridInt(point.y) }))
      : [];
  }
  if (normalized.style) {
    normalized.style = { ...normalized.style };
    if (normalized.style.fillColor == null && normalized.style.fill != null) {
      normalized.style.fillColor = normalized.style.fill;
    }
  }
  normalized.children = Array.isArray(normalized.children)
    ? normalized.children.map(normalizeNode)
    : [];
  normalized.meta = normalized.meta || {};
  return normalized;
}

function normalizeEdge(edge) {
  const normalized = { ...edge };
  if (typeof normalized.label === 'string') normalized.label = normalized.label.replace(/\\n/g, '\n');
  if (normalized.from == null && normalized.fromNode != null) normalized.from = normalized.fromNode;
  if (normalized.to == null && normalized.toNode != null) normalized.to = normalized.toNode;
  normalized.via = Array.isArray(normalized.via) ? normalized.via : [];
  normalized.style = normalized.style || {};
  normalized.meta = normalized.meta || {};
  return normalized;
}

// ── PNG export ─────────────────────────────────────────────────────────────

export function exportPng() {
  const diagram = getDiagram();
  const { gridSize } = diagram.settings;

  // Compute bounding box of all nodes in world units
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  function visitNodes(nodes, offX = 0, offY = 0) {
    for (const n of nodes) {
      const wx = offX + n.x, wy = offY + n.y;
      if (n.type !== 'waypoint') {
        minX = Math.min(minX, wx);
        minY = Math.min(minY, wy);
        maxX = Math.max(maxX, wx + n.width);
        maxY = Math.max(maxY, wy + n.height);
      }
      if (n.children?.length) visitNodes(n.children, wx, wy);
    }
  }
  visitNodes(diagram.nodes);

  if (!isFinite(minX)) {
    void window.kcuiAlert('Export PNG', 'Nothing to export.');
    return;
  }

  const pad  = 2; // grid units padding
  const zoom = 2; // 2x resolution for retina
  const W    = (maxX - minX + pad * 2) * gridSize * zoom;
  const H    = (maxY - minY + pad * 2) * gridSize * zoom;

  // Create off-screen canvas
  const offCanvas = document.createElement('canvas');
  offCanvas.width  = Math.ceil(W);
  offCanvas.height = Math.ceil(H);
  const ctx = offCanvas.getContext('2d');

  // White background
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, offCanvas.width, offCanvas.height);

  // We re-use renderer draw primitives by temporarily overriding view state
  // Simple approach: render directly here without importing renderer state
  const gs = gridSize * zoom;
  const panX = -(minX - pad) * gs;
  const panY = -(minY - pad) * gs;

  drawDiagramToContext(ctx, diagram, gs, panX, panY, zoom);

  offCanvas.toBlob(blob => {
    const url = URL.createObjectURL(blob);
    const a   = document.createElement('a');
    a.href    = url;
    a.download = (diagram.title || 'diagram') + '.png';
    a.click();
    URL.revokeObjectURL(url);
  }, 'image/png');
}

// Minimal self-contained renderer for the export canvas
function drawDiagramToContext(ctx, diagram, gs, panX, panY, zoom) {
  const { buildNodeMap, worldBounds } = window._koreModel; // injected by main.js

  const nodeMap = buildNodeMap(diagram.nodes);

  // Draw edges
  for (const edge of diagram.edges) {
    const fb = worldBounds(edge.from, nodeMap);
    const tb = worldBounds(edge.to,   nodeMap);
    if (!fb || !tb) continue;

    const points = [centre(fb)];
    for (const wid of (edge.via || [])) {
      const wb = worldBounds(wid, nodeMap);
      if (wb) points.push({ x: wb.x + wb.width/2, y: wb.y + wb.height/2 });
    }
    points.push(centre(tb));

    const sp = points.map(p => ({ x: p.x * gs + panX, y: p.y * gs + panY }));

    ctx.save();
    ctx.strokeStyle = edge.style?.strokeColor || '#7c6af7';
    ctx.lineWidth   = (edge.style?.strokeWidth || 1.5) * zoom;
    ctx.beginPath();
    ctx.moveTo(sp[0].x, sp[0].y);
    for (let i = 1; i < sp.length; i++) ctx.lineTo(sp[i].x, sp[i].y);
    ctx.stroke();

    // Arrow
    const arrow = edge.arrow ?? diagram.settings.defaultArrow;
    if (arrow === 'forward' || arrow === 'bidirectional') {
      drawArrowCtx(ctx, sp[sp.length-2] ?? sp[0], sp[sp.length-1], ctx.strokeStyle, ctx.lineWidth, zoom);
    }
    if (arrow === 'bidirectional') {
      drawArrowCtx(ctx, sp[1] ?? sp[0], sp[0], ctx.strokeStyle, ctx.lineWidth, zoom);
    }

    // Label
    if (edge.label) {
      const mid = sp[Math.floor(sp.length / 2)];
      ctx.fillStyle    = '#333';
      ctx.font         = `${11 * zoom}px system-ui,sans-serif`;
      ctx.textAlign    = 'center';
      ctx.textBaseline = 'bottom';
      ctx.fillText(edge.label, mid.x, mid.y - 3 * zoom);
    }
    ctx.restore();
  }

  // Draw nodes
  function drawNodes(nodes) {
    for (const node of nodes) {
      if (node.type === 'waypoint') { if (node.children?.length) drawNodes(node.children); continue; }
      const bounds = worldBounds(node.id, nodeMap);
      if (!bounds) continue;
      const sx = bounds.x * gs + panX;
      const sy = bounds.y * gs + panY;
      const sw = bounds.width  * gs;
      const sh = bounds.height * gs;

      ctx.save();
      const defaults    = diagram.settings.defaultNodeStyle || {};
      const style       = { ...defaults, ...(node.style || {}) };
      ctx.fillStyle     = style.fillColor   || '#ffffff';
      ctx.strokeStyle   = style.strokeColor || '#5a5a8a';
      ctx.lineWidth     = (style.strokeWidth || 1.5) * zoom;

      if (node.type !== 'text') {
        exportNodePath(ctx, node.type, sx, sy, sw, sh);
        ctx.fill();
        ctx.stroke();
      }

      if (node.label) {
        const fill = style.fillColor || '#ffffff';
        const r = parseInt(fill.replace('#','').substr(0,2),16);
        const g = parseInt(fill.replace('#','').substr(2,2),16);
        const b2= parseInt(fill.replace('#','').substr(4,2),16);
        const hAlign = style.labelHAlign || 'center';
        const vAlign = style.labelVAlign || 'middle';
        const pad = 8 * zoom;
        ctx.fillStyle = node.type === 'text'
          ? (style.fontColor || style.strokeColor || '#1e1e2e')
          : (r*299+g*587+b2*114)/1000 > 128 ? '#1e1e2e' : '#ffffff';
        ctx.font         = `${style.italic ? 'italic ' : ''}${style.bold ? 'bold ' : ''}${(style.fontSize||13)*zoom}px system-ui,sans-serif`;
        ctx.textAlign    = hAlign;
        drawExportLabel(
          ctx,
          node.label,
          hAlign === 'left' ? sx + pad : hAlign === 'right' ? sx + sw - pad : sx + sw/2,
          vAlign === 'top' ? sy + pad : vAlign === 'bottom' ? sy + sh - pad : sy + sh/2,
          Math.max(0, sw - pad * 2),
          style,
          vAlign,
          zoom,
        );
      }
      ctx.restore();

      if (node.children?.length) drawNodes(node.children);
    }
  }
  drawNodes(diagram.nodes);
}

function centre(b) { return { x: b.x + b.width/2, y: b.y + b.height/2 }; }

function exportNodePath(ctx, type, x, y, w, h) {
  ctx.beginPath();
  if (type === 'ellipse') {
    ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
  } else if (type === 'diamond') {
    ctx.moveTo(x + w / 2, y);
    ctx.lineTo(x + w,     y + h / 2);
    ctx.lineTo(x + w / 2, y + h);
    ctx.lineTo(x,         y + h / 2);
    ctx.closePath();
  } else if (type === 'rounded') {
    ctx.roundRect(x, y, w, h, Math.min(w, h) * 0.14);
  } else {
    ctx.rect(x, y, w, h);
  }
}

function drawExportLabel(ctx, text, x, y, maxWidth, style, vAlign, zoom) {
  const lines = String(text).split(/\r?\n/);
  const lineHeight = (style.fontSize || 13) * zoom * 1.2;
  let yy = y;
  if (vAlign === 'middle') {
    yy -= ((lines.length - 1) * lineHeight) / 2;
  } else if (vAlign === 'bottom') {
    yy -= (lines.length - 1) * lineHeight;
  }

  ctx.textBaseline = 'middle';
  for (const line of lines) {
    ctx.fillText(line, x, yy, maxWidth);
    yy += lineHeight;
  }
}

function drawArrowCtx(ctx, from, to, color, lw, zoom) {
  const angle = Math.atan2(to.y - from.y, to.x - from.x);
  const size  = Math.max(8, lw * 4);
  ctx.save();
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(to.x, to.y);
  ctx.lineTo(to.x - size * Math.cos(angle - 0.4), to.y - size * Math.sin(angle - 0.4));
  ctx.lineTo(to.x - size * Math.cos(angle + 0.4), to.y - size * Math.sin(angle + 0.4));
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}
