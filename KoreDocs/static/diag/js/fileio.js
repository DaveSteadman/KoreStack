/**
 * fileio.js — KoreFile-backed file state + autosave for KoreDiag, plus PNG export.
 */

import { createKfSyncController } from '/static/shared/js/kfSyncController.js';
import { newDiagram } from './model.js';
import { getDiagram, isDirty, markDirty, markSaved } from './store.js';

const _controller = createKfSyncController({
  logLabel: 'KoreDiag',
  alertLabel: 'Diagram',
  legacyType: 'kodiag',
  buildBlankContent: title => JSON.stringify(newDiagram(title)),
  applyLoadedContent: (content, file, loadContent) => {
    loadContent?.(JSON.parse(content));
  },
  onMarkDirty: () => markDirty(),
  onMarkSaved: () => markSaved(),
  hasExternalUnsavedChanges: () => isDirty(),
  onSaveSuccess: () => document.dispatchEvent(new CustomEvent('kd:autosaved')),
  onAfterApplyRemote: () => document.dispatchEvent(new CustomEvent('kd:autosaved')),
});

export const currentId = _controller.currentId;
export const currentName = _controller.currentName;
export const currentRevision = _controller.currentRevision;
export const autoOpenFromUrl = _controller.autoOpenFromUrl;
export const queueAutosave = _controller.queueAutosave;
export const flushAutosave = _controller.flushAutosave;

function serialise() {
  return JSON.stringify(getDiagram(), null, 2);
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
    alert('Nothing to export.');
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
      ctx.fillStyle   = node.style.fillColor   || '#ffffff';
      ctx.strokeStyle = node.style.strokeColor || '#5a5a8a';
      ctx.lineWidth   = 1.5 * zoom;

      if (node.type === 'ellipse') {
        ctx.beginPath();
        ctx.ellipse(sx + sw/2, sy + sh/2, sw/2, sh/2, 0, 0, Math.PI*2);
        ctx.fill(); ctx.stroke();
      } else {
        ctx.fillRect(sx, sy, sw, sh);
        ctx.strokeRect(sx, sy, sw, sh);
      }

      if (node.label) {
        const r = parseInt((node.style.fillColor||'#ffffff').replace('#','').substr(0,2),16);
        const g = parseInt((node.style.fillColor||'#ffffff').replace('#','').substr(2,2),16);
        const b2= parseInt((node.style.fillColor||'#ffffff').replace('#','').substr(4,2),16);
        const hAlign = node.style.labelHAlign || 'center';
        const vAlign = node.style.labelVAlign || 'middle';
        const pad = 8 * zoom;
        ctx.fillStyle = (r*299+g*587+b2*114)/1000 > 128 ? '#1e1e2e' : '#ffffff';
        ctx.font         = `${node.style.italic ? 'italic ' : ''}${node.style.bold ? 'bold ' : ''}${(node.style.fontSize||13)*zoom}px system-ui,sans-serif`;
        ctx.textAlign    = hAlign;
        ctx.textBaseline = vAlign;
        ctx.fillText(
          node.label,
          hAlign === 'left' ? sx + pad : hAlign === 'right' ? sx + sw - pad : sx + sw/2,
          vAlign === 'top' ? sy + pad : vAlign === 'bottom' ? sy + sh - pad : sy + sh/2,
          Math.max(0, sw - pad * 2),
        );
      }
      ctx.restore();

      if (node.children?.length) drawNodes(node.children);
    }
  }
  drawNodes(diagram.nodes);
}

function centre(b) { return { x: b.x + b.width/2, y: b.y + b.height/2 }; }

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
