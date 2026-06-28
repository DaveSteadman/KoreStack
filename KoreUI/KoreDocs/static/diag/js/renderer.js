/**
 * renderer.js — All canvas drawing.
 *
 * Coordinate model:
 *   screen = (world * gridSize * zoom) + pan
 * where world coords are in grid units.
 */

import { getDiagram, getNodeMap, view, selection } from './store.js';
import { worldBounds, getPorts } from './model.js';

const GRID_COLOR       = '#2e2e42';
const WAYPOINT_RADIUS  = 5;   // px
const PORT_RADIUS      = 5;   // px – shown when hovering in connect mode
const HANDLE_SIZE      = 8;   // px resize handle size
const EDGE_EP_RADIUS   = 7;   // px – edge endpoint drag handles
const RUBBER_COLOR     = 'rgba(120,120,150,0.20)';
const RUBBER_STROKE    = '#8888aa';

// ── Coordinate helpers ─────────────────────────────────────────────────────

export function worldToScreen(wx, wy) {
  const { gridSize } = getDiagram().settings;
  return {
    x: wx * gridSize * view.zoom + view.pan.x,
    y: wy * gridSize * view.zoom + view.pan.y,
  };
}

export function screenToWorld(sx, sy) {
  const { gridSize } = getDiagram().settings;
  return {
    x: (sx - view.pan.x) / (gridSize * view.zoom),
    y: (sy - view.pan.y) / (gridSize * view.zoom),
  };
}

export function snapToGrid(worldVal) {
  return Math.round(worldVal);
}

/** Convert a world-space bounding box to screen-space pixels */
function boundsToScreen(bounds) {
  const { gridSize } = getDiagram().settings;
  const gs = gridSize * view.zoom;
  return {
    x: bounds.x * gs + view.pan.x,
    y: bounds.y * gs + view.pan.y,
    w: bounds.width  * gs,
    h: bounds.height * gs,
  };
}

// ── Main render ────────────────────────────────────────────────────────────

let _canvas, _ctx;
let _portHints      = [];    // { x, y } screen positions to highlight (connect mode)
let _activePortHint = null;  // { x, y } the nearest/preferred port — drawn distinctly
let _rubberBand = null; // { x1,y1,x2,y2 } screen coords
let _ghostEdge  = null; // { fromX,fromY,toX,toY } screen coords (during connect drag)
let _ghostRect  = null; // { x,y,w,h } screen coords (during shape draw drag)

export function init(canvas) {
  _canvas = canvas;
  _ctx    = canvas.getContext('2d');
  window.addEventListener('resize', resize);
  resize();
}

export function resize() {
  const container = _canvas.parentElement;
  _canvas.width  = container.clientWidth;
  _canvas.height = container.clientHeight;
  draw();
}

export function setPortHints(hints) { _portHints = hints; _activePortHint = null; }
export function setActivePortHint(pt) { _activePortHint = pt ?? null; }
export function setRubberBand(r)    { _rubberBand = r; }
export function setGhostEdge(g)     { _ghostEdge = g; }
export function setGhostRect(r)     { _ghostRect = r; }

export function draw() {
  if (!_canvas || !_ctx) return;
  const ctx = _ctx;
  const W = _canvas.width, H = _canvas.height;
  ctx.clearRect(0, 0, W, H);

  const diagram = getDiagram();
  if (diagram.settings.showGrid) drawGrid(ctx, W, H, diagram.settings.gridSize);

  const nodeMap = getNodeMap();

  // Draw edges
  for (const edge of diagram.edges) drawEdge(ctx, edge, nodeMap);

  // Ghost edge (during connect drag or endpoint rewire)
  if (_ghostEdge) {
    ctx.save();
    ctx.strokeStyle = '#8888aa';
    ctx.lineWidth   = 1.5;
    ctx.setLineDash([5, 3]);
    ctx.beginPath();
    ctx.moveTo(_ghostEdge.fromX, _ghostEdge.fromY);
    ctx.lineTo(_ghostEdge.toX,   _ghostEdge.toY);
    ctx.stroke();
    ctx.restore();
  }

  // Draw nodes (recursive)
  drawNodes(ctx, diagram.nodes, nodeMap);

  // Ghost rect (during shape draw)
  if (_ghostRect) {
    ctx.save();
    ctx.strokeStyle = '#8888aa';
    ctx.lineWidth   = 1;
    ctx.setLineDash([4, 3]);
    ctx.strokeRect(_ghostRect.x, _ghostRect.y, _ghostRect.w, _ghostRect.h);
    ctx.restore();
  }

  // Port hints (connect mode / endpoint drag)
  if (_portHints.length) {
    ctx.save();
    ctx.fillStyle   = 'rgba(180,180,220,0.45)';
    ctx.strokeStyle = 'rgba(180,180,220,0.70)';
    ctx.lineWidth   = 1;
    for (const p of _portHints) {
      // the active port is drawn separately below
      if (_activePortHint && Math.hypot(p.x - _activePortHint.x, p.y - _activePortHint.y) < 1) continue;
      ctx.beginPath();
      ctx.arc(p.x, p.y, PORT_RADIUS, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
    ctx.restore();
  }

  // Active (nearest) port — drawn prominent
  if (_activePortHint) {
    ctx.save();
    ctx.fillStyle   = 'rgba(230,230,255,0.95)';
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth   = 2;
    ctx.shadowColor = '#ffffff';
    ctx.shadowBlur  = 6;
    ctx.beginPath();
    ctx.arc(_activePortHint.x, _activePortHint.y, PORT_RADIUS * 1.7, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  // Rubber-band
  if (_rubberBand) {
    const { x1, y1, x2, y2 } = _rubberBand;
    ctx.save();
    ctx.fillStyle   = RUBBER_COLOR;
    ctx.strokeStyle = RUBBER_STROKE;
    ctx.lineWidth   = 1;
    ctx.fillRect  (Math.min(x1,x2), Math.min(y1,y2), Math.abs(x2-x1), Math.abs(y2-y1));
    ctx.strokeRect(Math.min(x1,x2), Math.min(y1,y2), Math.abs(x2-x1), Math.abs(y2-y1));
    ctx.restore();
  }

  // Edge endpoint drag handles — on top of everything (single selected edge)
  if (selection.size === 1) {
    const [sid] = selection;
    const selEdge = diagram.edges.find(e => e.id === sid);
    if (selEdge) {
      const handles = getEdgeEndpointHandles(sid);
      if (handles) {
        const nodeDefs = diagram.settings.defaultNodeStyle || {};
        const eff      = { strokeColor: nodeDefs.strokeColor, strokeWidth: nodeDefs.strokeWidth, ...selEdge.style };
        drawEdgeEndpointHandles(ctx, handles.from, handles.to, eff.strokeColor || '#5a5a8a');
      }
    }
  }
}

// ── Grid ───────────────────────────────────────────────────────────────────

function drawGrid(ctx, W, H, gridSize) {
  const step = gridSize * view.zoom;
  if (step < 4) return; // too dense — skip

  // Offset so grid lines track pan
  const offX = ((view.pan.x % step) + step) % step;
  const offY = ((view.pan.y % step) + step) % step;

  ctx.save();
  ctx.strokeStyle = GRID_COLOR;
  ctx.lineWidth   = 1;
  ctx.beginPath();

  for (let x = offX; x < W; x += step) {
    ctx.moveTo(x + 0.5, 0);
    ctx.lineTo(x + 0.5, H);
  }
  for (let y = offY; y < H; y += step) {
    ctx.moveTo(0, y + 0.5);
    ctx.lineTo(W, y + 0.5);
  }
  ctx.stroke();
  ctx.restore();
}

// ── Nodes ──────────────────────────────────────────────────────────────────

function drawNodes(ctx, nodes, nodeMap) {
  for (const node of nodes) {
    const bounds = worldBounds(node.id, nodeMap);
    if (!bounds) continue;
    if (node.type === 'waypoint') {
      drawWaypoint(ctx, node, bounds);
    } else {
      drawNode(ctx, node, bounds);
    }
    if (node.children?.length) drawNodes(ctx, node.children, nodeMap);
  }
}

function drawNode(ctx, node, bounds) {
  const { x, y, w, h } = boundsToScreen(bounds);
  // Resolve style: node overrides take precedence over diagram defaults
  const defaults    = getDiagram().settings.defaultNodeStyle || {};
  const style       = { ...defaults, ...node.style };
  const selected    = selection.has(node.id);
  const strokeColor = style.strokeColor || '#5a5a8a';
  const strokeWidth = style.strokeWidth || 1.5;

  ctx.save();

  // Fill
  ctx.fillStyle = style.fillColor || '#ffffff';
  if (node.type === 'ellipse') {
    ellipsePath(ctx, x, y, w, h);
    ctx.fill();
  } else {
    ctx.fillRect(x, y, w, h);
  }
  // Stroke
  ctx.strokeStyle = strokeColor;
  ctx.lineWidth   = strokeWidth;
  if (node.type === 'ellipse') {
    ellipsePath(ctx, x, y, w, h);
    ctx.stroke();
  } else {
    ctx.strokeRect(x, y, w, h);
  }

  // Label
  if (node.label) {
    const labelLayout = nodeLabelLayout(style, x, y, w, h);
    ctx.fillStyle   = contrastColor(style.fillColor || '#ffffff');
    ctx.font        = labelFont(style, view.zoom);
    ctx.textAlign   = labelLayout.textAlign;
    ctx.textBaseline= labelLayout.textBaseline;
    drawMultilineText(ctx, node.label, labelLayout, style);
  }

  ctx.restore();

  // Resize handles (only when selected and single selection)
  if (selected && selection.size === 1) drawResizeHandles(ctx, x, y, w, h, strokeColor);
}

function labelFont(style, zoom) {
  const weight = style.bold ? 'bold ' : '';
  const italic = style.italic ? 'italic ' : '';
  return `${italic}${weight}${(style.fontSize || 13) * zoom}px system-ui,sans-serif`;
}

function nodeLabelLayout(style, x, y, w, h) {
  const padding = 8 * view.zoom;
  const hAlign = style.labelHAlign || 'center';
  const vAlign = style.labelVAlign || 'middle';
  const maxWidth = Math.max(0, w - padding * 2);
  return {
    x: hAlign === 'left' ? x + padding : hAlign === 'right' ? x + w - padding : x + w / 2,
    y: vAlign === 'top' ? y + padding : vAlign === 'bottom' ? y + h - padding : y + h / 2,
    textAlign: hAlign,
    textBaseline: vAlign,
    maxWidth,
  };
}

function drawMultilineText(ctx, text, layout, style) {
  const lines = String(text).split(/\r?\n/);
  if (lines.length === 1) {
    ctx.fillText(lines[0], layout.x, layout.y, layout.maxWidth);
    return;
  }

  const fontSize = (style.fontSize || 13) * view.zoom;
  const lineHeight = fontSize * 1.2;
  const oldBaseline = ctx.textBaseline;
  let y = layout.y;

  if (layout.textBaseline === 'middle') {
    y -= ((lines.length - 1) * lineHeight) / 2;
  } else if (layout.textBaseline === 'bottom') {
    y -= (lines.length - 1) * lineHeight;
  }

  ctx.textBaseline = 'middle';
  for (const line of lines) {
    ctx.fillText(line, layout.x, y, layout.maxWidth);
    y += lineHeight;
  }
  ctx.textBaseline = oldBaseline;
}

function drawWaypoint(ctx, node, bounds) {
  const sc = worldToScreen(bounds.x + 0.5, bounds.y + 0.5); // centre of 1×1 waypoint
  const selected = selection.has(node.id);
  ctx.save();
  ctx.fillStyle   = selected ? 'rgba(180,180,220,0.9)' : 'rgba(150,150,180,0.4)';
  ctx.strokeStyle = selected ? '#ccccee' : '#aaaacc';
  ctx.lineWidth   = 1.5;
  ctx.beginPath();
  ctx.arc(sc.x, sc.y, WAYPOINT_RADIUS, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function drawResizeHandles(ctx, x, y, w, h, strokeColor) {
  const hs = HANDLE_SIZE;
  const positions = [
    [x, y], [x + w/2 - hs/2, y], [x + w - hs, y],
    [x, y + h/2 - hs/2],                           [x + w - hs, y + h/2 - hs/2],
    [x, y + h - hs], [x + w/2 - hs/2, y + h - hs],[x + w - hs, y + h - hs],
  ];
  ctx.save();
  ctx.fillStyle   = '#fff';
  ctx.strokeStyle = strokeColor || '#888';
  ctx.lineWidth   = 1.5;
  for (const [hx, hy] of positions) {
    ctx.fillRect(hx, hy, hs, hs);
    ctx.strokeRect(hx, hy, hs, hs);
  }
  ctx.restore();
}

// ── Edges ──────────────────────────────────────────────────────────────────

function drawEdge(ctx, edge, nodeMap) {
  const fromBounds = worldBounds(edge.from, nodeMap);
  const toBounds   = worldBounds(edge.to,   nodeMap);
  if (!fromBounds || !toBounds) return;

  // Build via-waypoint world points
  const viaPoints = [];
  for (const wid of (edge.via || [])) {
    const wb = worldBounds(wid, nodeMap);
    if (wb) viaPoints.push({ x: wb.x + wb.width / 2, y: wb.y + wb.height / 2 });
  }

  // Compute anchor endpoint for each end
  const startPt = edgeEndpoint(edge.from, fromBounds, nodeMap,
    edge.fromPort, viaPoints.length > 0 ? viaPoints[0]            : centre(toBounds));
  const endPt   = edgeEndpoint(edge.to,   toBounds,   nodeMap,
    edge.toPort,   viaPoints.length > 0 ? viaPoints[viaPoints.length - 1] : centre(fromBounds));

  const points = [startPt, ...viaPoints, endPt];

  // Convert all to screen
  const sp = points.map(p => worldToScreen(p.x, p.y));

  const selected = selection.has(edge.id);
  // Resolve edge style: edge overrides → shared defaultNodeStyle stroke values
  const nodeDefaults = getDiagram().settings.defaultNodeStyle || {};
  const effEdgeStyle = { strokeColor: nodeDefaults.strokeColor, strokeWidth: nodeDefaults.strokeWidth, ...edge.style };
  const edgeStroke   = effEdgeStyle.strokeColor || '#5a5a8a';
  const edgeWidth    = effEdgeStyle.strokeWidth  || 1.5;

  ctx.save();
  ctx.strokeStyle = edgeStroke;
  ctx.lineWidth   = edgeWidth * (selected ? 2 : 1) * view.zoom;
  if (selected) { ctx.shadowColor = edgeStroke; ctx.shadowBlur = 8; }

  ctx.beginPath();
  ctx.moveTo(sp[0].x, sp[0].y);
  for (let i = 1; i < sp.length; i++) ctx.lineTo(sp[i].x, sp[i].y);
  ctx.stroke();
  ctx.shadowBlur = 0;

  // Arrowhead at last point
  const arrow = edge.arrow ?? getDiagram().settings.defaultArrow;
  if (arrow === 'forward' || arrow === 'bidirectional') {
    const last = sp[sp.length - 1];
    const prev = sp[sp.length - 2];
    drawArrow(ctx, prev, last, ctx.strokeStyle, ctx.lineWidth);
  }
  if (arrow === 'bidirectional') {
    drawArrow(ctx, sp[1] ?? sp[0], sp[0], ctx.strokeStyle, ctx.lineWidth);
  }

  // Label at midpoint
  if (edge.label) {
    const mid = sp[Math.floor(sp.length / 2)];
    ctx.fillStyle    = '#cdd6f4';
    ctx.font         = `${11 * view.zoom}px system-ui,sans-serif`;
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'bottom';
    ctx.fillText(edge.label, mid.x, mid.y - 3 * view.zoom);
  }

  ctx.restore();
}

function drawEdgeEndpointHandles(ctx, fromSP, toSP, edgeStroke) {
  ctx.save();
  ctx.fillStyle   = edgeStroke;
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth   = 1.5;
  for (const pt of [fromSP, toSP]) {
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, EDGE_EP_RADIUS, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

function drawArrow(ctx, from, to, color, lw) {
  const angle = Math.atan2(to.y - from.y, to.x - from.x);
  const size  = Math.max(8, lw * 4) * view.zoom;
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

// ── Utilities ──────────────────────────────────────────────────────────────

/**
 * Return the world-space endpoint for one side of an edge.
 *
 * - rect/waypoint nodes: use the stored port key if present, else centre.
 * - ellipse nodes: always use the ellipse boundary intersection toward
 *   `directionPt` (keeps the arrow visible regardless of port choice).
 */
function edgeEndpoint(nodeId, bounds, nodeMap, portKey, directionPt) {
  const nodeType = nodeMap.get(nodeId)?.node?.type;
  if (nodeType === 'ellipse') {
    return ellipseBoundaryPoint(bounds, directionPt);
  }
  // rect or waypoint
  if (portKey) {
    const ports = getPorts(bounds);
    if (ports[portKey]) return ports[portKey];
  }
  return centre(bounds);
}

/**
 * Point on an axis-aligned ellipse boundary that faces `towards`.
 * angle = atan2(towards.y − cy, towards.x − cx)
 */
function ellipseBoundaryPoint(bounds, towards) {
  const cx = bounds.x + bounds.width  / 2;
  const cy = bounds.y + bounds.height / 2;
  const rx = bounds.width  / 2;
  const ry = bounds.height / 2;
  const angle = Math.atan2(towards.y - cy, towards.x - cx);
  return { x: cx + rx * Math.cos(angle), y: cy + ry * Math.sin(angle) };
}

function ellipsePath(ctx, x, y, w, h) {
  ctx.beginPath();
  ctx.ellipse(x + w/2, y + h/2, w/2, h/2, 0, 0, Math.PI * 2);
}

function centre(bounds) {
  return { x: bounds.x + bounds.width / 2, y: bounds.y + bounds.height / 2 };
}

function contrastColor(hex) {
  // Very simple luminance check
  const c = hex.replace('#','');
  if (c.length < 6) return '#000';
  const r = parseInt(c.substr(0,2),16);
  const g = parseInt(c.substr(2,2),16);
  const b = parseInt(c.substr(4,2),16);
  return (r*299 + g*587 + b*114) / 1000 > 128 ? '#1e1e2e' : '#cdd6f4';
}

// ── Hit testing ────────────────────────────────────────────────────────────
const HIT_TOL = 5; // px tolerance for edge clicks

/**
 * Returns the topmost node or edge id at screen position (sx, sy), or null.
 * Nodes take priority over edges.
 */
export function hitTest(sx, sy) {
  const diagram = getDiagram();
  const nodeMap = getNodeMap();

  // Test nodes in reverse paint order (top-most first)
  const flatNodes = [];
  function collect(nodes) {
    for (const n of [...nodes].reverse()) {
      if (n.children?.length) collect(n.children);
      flatNodes.push(n);
    }
  }
  collect(diagram.nodes);

  for (const node of flatNodes) {
    const bounds = worldBounds(node.id, nodeMap);
    if (!bounds) continue;
    const s = boundsToScreen(bounds);
    if (node.type === 'waypoint') {
      const sc = worldToScreen(bounds.x + 0.5, bounds.y + 0.5);
      if (Math.hypot(sx - sc.x, sy - sc.y) <= WAYPOINT_RADIUS + 3) return node.id;
    } else {
      if (sx >= s.x && sx <= s.x + s.w && sy >= s.y && sy <= s.y + s.h) return node.id;
    }
  }

  // Test edges
  for (const edge of [...diagram.edges].reverse()) {
    if (edgeHitTest(edge, sx, sy, nodeMap)) return edge.id;
  }

  return null;
}

function edgeHitTest(edge, sx, sy, nodeMap) {
  const fromB = worldBounds(edge.from, nodeMap);
  const toB   = worldBounds(edge.to,   nodeMap);
  if (!fromB || !toB) return false;
  const viaPoints = [];
  for (const wid of (edge.via || [])) {
    const wb = worldBounds(wid, nodeMap);
    if (wb) viaPoints.push({ x: wb.x + wb.width/2, y: wb.y + wb.height/2 });
  }
  const startPt = edgeEndpoint(edge.from, fromB, nodeMap,
    edge.fromPort, viaPoints.length > 0 ? viaPoints[0]                    : centre(toB));
  const endPt   = edgeEndpoint(edge.to,   toB,   nodeMap,
    edge.toPort,   viaPoints.length > 0 ? viaPoints[viaPoints.length - 1] : centre(fromB));
  const points = [startPt, ...viaPoints, endPt];
  const sp = points.map(p => worldToScreen(p.x, p.y));
  for (let i = 0; i < sp.length - 1; i++) {
    if (distToSegment(sx, sy, sp[i].x, sp[i].y, sp[i+1].x, sp[i+1].y) <= HIT_TOL) return true;
  }
  return false;
}

function distToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const lenSq = dx*dx + dy*dy;
  if (lenSq === 0) return Math.hypot(px-ax, py-ay);
  const t = Math.max(0, Math.min(1, ((px-ax)*dx + (py-ay)*dy) / lenSq));
  return Math.hypot(px - (ax + t*dx), py - (ay + t*dy));
}

/**
 * Return resize handle index (0–7, NW→NE→E…) if screen point (sx,sy) is over
 * a handle of the given node, or -1.
 * Handle order: NW(0) N(1) NE(2) W(3) E(4) SW(5) S(6) SE(7)
 */
export function resizeHandleAt(nodeId, sx, sy) {
  const nodeMap = getNodeMap();
  const bounds  = worldBounds(nodeId, nodeMap);
  if (!bounds) return -1;
  const { x, y, w, h } = boundsToScreen(bounds);
  const hs = HANDLE_SIZE;
  const positions = [
    [x, y], [x + w/2 - hs/2, y], [x + w - hs, y],
    [x, y + h/2 - hs/2],                           [x + w - hs, y + h/2 - hs/2],
    [x, y + h - hs], [x + w/2 - hs/2, y + h - hs],[x + w - hs, y + h - hs],
  ];
  for (let i = 0; i < positions.length; i++) {
    const [hx, hy] = positions[i];
    if (sx >= hx && sx <= hx + hs && sy >= hy && sy <= hy + hs) return i;
  }
  return -1;
}

/**
 * Return all 8 port screen positions for a node.
 */
export function getScreenPorts(nodeId) {
  const nodeMap = getNodeMap();
  const bounds  = worldBounds(nodeId, nodeMap);
  if (!bounds) return [];
  const ports   = getPorts(bounds);
  return Object.values(ports).map(p => worldToScreen(p.x, p.y));
}

/**
 * Return the nearest port screen position to (sx,sy) for a node,
 * and the port world-position.
 */
export function nearestScreenPort(nodeId, sx, sy) {
  const nodeMap = getNodeMap();
  const bounds  = worldBounds(nodeId, nodeMap);
  if (!bounds) return null;
  const ports = getPorts(bounds);
  let best = null, bestDist = Infinity;
  for (const [key, wp] of Object.entries(ports)) {
    const sp = worldToScreen(wp.x, wp.y);
    const d  = Math.hypot(sx - sp.x, sy - sp.y);
    if (d < bestDist) { bestDist = d; best = { key, screen: sp, world: wp }; }
  }
  return best;
}

/**
 * Get the screen-space positions of the two endpoint handles for an edge.
 * Returns { from: {x,y}, to: {x,y} } or null.
 */
export function getEdgeEndpointHandles(edgeId) {
  const diagram = getDiagram();
  const nodeMap = getNodeMap();
  const edge    = diagram.edges.find(e => e.id === edgeId);
  if (!edge) return null;
  const fromBounds = worldBounds(edge.from, nodeMap);
  const toBounds   = worldBounds(edge.to,   nodeMap);
  if (!fromBounds || !toBounds) return null;
  const viaPoints = [];
  for (const wid of (edge.via || [])) {
    const wb = worldBounds(wid, nodeMap);
    if (wb) viaPoints.push({ x: wb.x + wb.width / 2, y: wb.y + wb.height / 2 });
  }
  const startW = edgeEndpoint(edge.from, fromBounds, nodeMap,
    edge.fromPort, viaPoints.length > 0 ? viaPoints[0] : centre(toBounds));
  const endW   = edgeEndpoint(edge.to,   toBounds,   nodeMap,
    edge.toPort,   viaPoints.length > 0 ? viaPoints[viaPoints.length - 1] : centre(fromBounds));
  return {
    from: worldToScreen(startW.x, startW.y),
    to:   worldToScreen(endW.x,   endW.y),
  };
}

/** Returns 'from'|'to' if (sx,sy) is over an endpoint handle of edgeId, else null. */
export function edgeEndpointHandleAt(edgeId, sx, sy) {
  const handles = getEdgeEndpointHandles(edgeId);
  if (!handles) return null;
  if (Math.hypot(sx - handles.from.x, sy - handles.from.y) <= EDGE_EP_RADIUS + 3) return 'from';
  if (Math.hypot(sx - handles.to.x,   sy - handles.to.y)   <= EDGE_EP_RADIUS + 3) return 'to';
  return null;
}
