/**
 * interaction.js — All mouse & keyboard handling on the canvas.
 *
 * Tools: select | rect | ellipse | connect | waypoint | pan
 */

import {
  getDiagram, getNodeMap, view, selection,
  dispatch, undo, redo, canUndo, canRedo,
  cmdAddNode, cmdAddEdge, cmdDeleteSelected,
  cmdMoveNodes, cmdResizeNode, cmdSetLabel,
  cmdRewireEdge,
  loadDiagramSilent,
} from './store.js';
import {
  newNode, newEdge, worldBounds, buildNodeMap,
  removeNodeById, repackParent, deepClone,
} from './model.js';
import {
  draw, screenToWorld, worldToScreen, snapToGrid,
  hitTest, resizeHandleAt, getScreenPorts, nearestScreenPort,
  getEdgeEndpointHandles, edgeEndpointHandleAt,
  setRubberBand, setGhostEdge, setGhostRect, setPortHints, setActivePortHint,
} from './renderer.js';

// ── State ──────────────────────────────────────────────────────────────────
let activeTool = 'select';
let _canvas;
let _mouseDown   = false;
let _spaceDown   = false;
let _middlePan   = false;
let _lastMouse   = { x: 0, y: 0 };
let _dragState   = null; // see startDrag()

// ── Init ───────────────────────────────────────────────────────────────────
export function init(canvas) {
  _canvas = canvas;

  canvas.addEventListener('mousedown',  onMouseDown);
  canvas.addEventListener('mousemove',  onMouseMove);
  canvas.addEventListener('mouseup',    onMouseUp);
  canvas.addEventListener('dblclick',   onDblClick);
  canvas.addEventListener('wheel',      onWheel, { passive: false });
  canvas.addEventListener('contextmenu', e => e.preventDefault());

  window.addEventListener('keydown',  onKeyDown);
  window.addEventListener('keyup',    onKeyUp);
  window.addEventListener('mouseup',  onMouseUp); // catch release outside canvas
}

export function setTool(tool) {
  activeTool = tool;
  setPortHints([]);
  setActivePortHint(null);
  setGhostEdge(null);
  setGhostRect(null);
  draw();
  // Update body attribute for cursor CSS
  document.body.setAttribute('data-active-tool', tool);

  // Highlight active button
  document.querySelectorAll('.tool-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.tool === tool);
  });
}

export function getActiveTool() { return activeTool; }

// ── Mouse ──────────────────────────────────────────────────────────────────

function onMouseDown(e) {
  e.preventDefault();
  _mouseDown = true;
  _lastMouse = { x: e.clientX, y: e.clientY };

  const rect = _canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  // Middle-mouse pan always available
  if (e.button === 1) { _middlePan = true; return; }

  if (activeTool === 'pan' || _spaceDown) {
    _dragState = { type: 'pan', lastX: sx, lastY: sy };
    return;
  }

  if (activeTool === 'select') {
    handleSelectDown(sx, sy, e);
    return;
  }

  if (activeTool === 'rect' || activeTool === 'ellipse') {
    const w = screenToWorld(sx, sy);
    _dragState = {
      type: 'draw-shape',
      toolType: activeTool,
      startW: snapToGrid(w.x),
      startH: snapToGrid(w.y),
    };
    return;
  }

  if (activeTool === 'connect') {
    handleConnectDown(sx, sy);
    return;
  }

  if (activeTool === 'waypoint') {
    // Place a waypoint node at click position
    const w = screenToWorld(sx, sy);
    const node = newNode('waypoint', snapToGrid(w.x), snapToGrid(w.y), 1, 1);
    dispatch(cmdAddNode(node));
    selection.clear();
    selection.add(node.id);
    draw();
    return;
  }
}

function handleSelectDown(sx, sy, e) {
  // 0. Check edge endpoint handle on single selected edge
  if (selection.size === 1) {
    const [id] = selection;
    if (!getNodeMap().has(id)) {
      const end = edgeEndpointHandleAt(id, sx, sy);
      if (end !== null) {
        const handles = getEdgeEndpointHandles(id);
        if (handles) {
          const fixedScreen = end === 'from' ? handles.to : handles.from;
          _dragState = { type: 'drag-edge-endpoint', edgeId: id, end, fixedScreen };
          draw();
          return;
        }
      }
    }
  }

  // 1. Check resize handle on single selected node
  if (selection.size === 1) {
    const [id] = selection;
    const nodeMap = getNodeMap();
    if (nodeMap.has(id)) {
      const hi = resizeHandleAt(id, sx, sy);
      if (hi >= 0) {
        const bounds = worldBounds(id, nodeMap);
        _dragState = { type: 'resize', id, handleIdx: hi, origBounds: { ...bounds }, sx, sy };
        return;
      }
    }
  }

  const hit = hitTest(sx, sy);

  if (hit) {
    if (!e.shiftKey && !selection.has(hit)) selection.clear();
    selection.add(hit);

    // Prepare for a move drag
    const nodeMap = getNodeMap();
    const moves = [];
    for (const id of selection) {
      if (nodeMap.has(id)) {
        const b = worldBounds(id, nodeMap);
        moves.push({ id, startX: b.x, startY: b.y });
      }
    }
    const w = screenToWorld(sx, sy);
    _dragState = { type: 'move', moves, startWX: w.x, startWY: w.y, moved: false };
  } else {
    if (!e.shiftKey) selection.clear();
    // Start rubber-band
    _dragState = { type: 'rubber', sx, sy };
  }

  draw();
  notifySelectionChange();
}

function handleConnectDown(sx, sy) {
  const hit = hitTest(sx, sy);
  if (hit) {
    const nodeMap = getNodeMap();
    if (!nodeMap.has(hit)) return; // must be a node, not an edge
    const sp = nearestScreenPort(hit, sx, sy);
    _dragState = {
      type: 'connect',
      fromId: hit,
      fromPort: sp ? sp.key : null,
      fromScreen: sp ? sp.screen : { x: sx, y: sy },
    };
    // Show port hints
    setPortHints(getScreenPorts(hit));
    draw();
  }
}

function onMouseMove(e) {
  const rect = _canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  const dx = e.clientX - _lastMouse.x;
  const dy = e.clientY - _lastMouse.y;
  _lastMouse = { x: e.clientX, y: e.clientY };

  // Middle-mouse pan
  if (_middlePan) {
    view.pan.x += dx;
    view.pan.y += dy;
    draw();
    document.dispatchEvent(new CustomEvent('kd:view-changed'));
    return;
  }

  if (!_dragState && activeTool === 'connect') {
    // Hover: show port hints for whatever node is under cursor
    const hit = hitTest(sx, sy);
    const nodeMap = getNodeMap();
    if (hit && nodeMap.has(hit)) {
      setPortHints(getScreenPorts(hit));
      const nearest = nearestScreenPort(hit, sx, sy);
      setActivePortHint(nearest ? nearest.screen : null);
    } else {
      setPortHints([]);
      setActivePortHint(null);
    }
    draw();
    return;
  }

  if (!_dragState) return;

  if (_dragState.type === 'pan') {
    view.pan.x += sx - _dragState.lastX;
    view.pan.y += sy - _dragState.lastY;
    _dragState.lastX = sx;
    _dragState.lastY = sy;
    draw();
    document.dispatchEvent(new CustomEvent('kd:view-changed'));
    return;
  }

  if (_dragState.type === 'move') {
    const w = screenToWorld(sx, sy);
    const rawDX = w.x - _dragState.startWX;
    const rawDY = w.y - _dragState.startWY;

    // Keep drag aligned to grid units so nodes/edges remain crisp and predictable.
    const dx = snapToGrid(rawDX);
    const dy = snapToGrid(rawDY);

    if (dx === (_dragState.liveDX ?? 0) && dy === (_dragState.liveDY ?? 0)) return;

    if (!_preDragSnapshot) _preDragSnapshot = deepClone(getDiagram());

    // Directly nudge the in-memory diagram (not via undo stack) for live feel
    // We restore via _preDragSnapshot on mouseup
    const prevDX = _dragState.liveDX ?? 0;
    const prevDY = _dragState.liveDY ?? 0;
    const deltaDX = dx - prevDX;
    const deltaDY = dy - prevDY;
    _dragState.liveDX = dx;
    _dragState.liveDY = dy;
    _dragState.moved  = true;

    // Apply incremental nudge directly to store's diagram object
    const diagram  = getDiagram();
    const nodeMapR = buildNodeMap(diagram.nodes);
    for (const { id } of _dragState.moves) {
      const entry = nodeMapR.get(id);
      if (entry) { entry.node.x += deltaDX; entry.node.y += deltaDY; }
    }

    draw();
    return;
  }

  if (_dragState.type === 'resize') {
    if (!_preDragSnapshot) _preDragSnapshot = deepClone(getDiagram());
    const w = screenToWorld(sx, sy);
    updateResizeGhost(w.x, w.y);
    draw();
    return;
  }

  if (_dragState.type === 'rubber') {
    setRubberBand({ x1: _dragState.sx, y1: _dragState.sy, x2: sx, y2: sy });
    draw();
    return;
  }

  if (_dragState.type === 'draw-shape') {
    const w = screenToWorld(sx, sy);
    const sx2 = snapToGrid(w.x), sy2 = snapToGrid(w.y);
    const x = Math.min(_dragState.startW, sx2);
    const y = Math.min(_dragState.startH, sy2);
    const rw = Math.abs(sx2 - _dragState.startW) || 1;
    const rh = Math.abs(sy2 - _dragState.startH) || 1;
    const tl = worldToScreen(x, y);
    const br = worldToScreen(x + rw, y + rh);
    setGhostRect({ x: tl.x, y: tl.y, w: br.x - tl.x, h: br.y - tl.y });
    _dragState.previewX = x; _dragState.previewY = y;
    _dragState.previewW = rw; _dragState.previewH = rh;
    draw();
    return;
  }

  if (_dragState.type === 'drag-edge-endpoint') {
    setGhostEdge({ fromX: _dragState.fixedScreen.x, fromY: _dragState.fixedScreen.y, toX: sx, toY: sy });
    const hit = hitTest(sx, sy);
    if (hit && getNodeMap().has(hit)) {
      setPortHints(getScreenPorts(hit));
      const nearest = nearestScreenPort(hit, sx, sy);
      setActivePortHint(nearest ? nearest.screen : null);
    } else {
      setPortHints([]);
      setActivePortHint(null);
    }
    draw();
    return;
  }

  if (_dragState.type === 'connect') {
    setGhostEdge({ fromX: _dragState.fromScreen.x, fromY: _dragState.fromScreen.y, toX: sx, toY: sy });
    // Show ports on target node (if hover)
    const hit = hitTest(sx, sy);
    const nodeMap = getNodeMap();
    if (hit && nodeMap.has(hit) && hit !== _dragState.fromId) {
      setPortHints(getScreenPorts(hit));
      const nearest = nearestScreenPort(hit, sx, sy);
      setActivePortHint(nearest ? nearest.screen : null);
    } else {
      setPortHints(getScreenPorts(_dragState.fromId));
      setActivePortHint(null);
    }
    draw();
    return;
  }
}

function onMouseUp(e) {
  if (!_mouseDown && !_middlePan) return;

  if (_middlePan) { _middlePan = false; return; }
  _mouseDown = false;

  const rect = _canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  if (!_dragState) { draw(); return; }

  const ds = _dragState;
  _dragState = null;

  setRubberBand(null);
  setGhostEdge(null);
  setGhostRect(null);
  setPortHints([]);
  setActivePortHint(null);

  if (ds.type === 'pan') { draw(); return; }

  if (ds.type === 'move' && ds.moved) {
    commitMove(ds);
    return;
  }

  if (ds.type === 'resize') {
    commitResize(ds, sx, sy);
    return;
  }

  if (ds.type === 'rubber') {
    commitRubberBand(ds, sx, sy);
    return;
  }

  if (ds.type === 'draw-shape') {
    commitDrawShape(ds, sx, sy);
    return;
  }

  if (ds.type === 'drag-edge-endpoint') {
    commitEdgeEndpoint(ds, sx, sy);
    return;
  }

  if (ds.type === 'connect') {
    commitConnect(ds, sx, sy);
    return;
  }

  draw();
  notifySelectionChange();
}

// ── Drag commit helpers ────────────────────────────────────────────────────

// Patch renderer to apply live offset — we override worldBounds temporarily
// by injecting an offset table into model calls.
// Simpler approach: we directly mutate + revert the diagram nodes during drag.
// Instead we patch for render only via a module-level offset in renderer:
// Actually, the cleanest approach for vanilla JS is to directly mutate + redraw,
// and revert if the user cancels. We do the full revert via undo stack.
// So: mutate immediately for live feel, commit to undo stack on mouseup.

let _preDragSnapshot = null;

function commitMove(ds) {
  const dx = ds.liveDX ?? 0;
  const dy = ds.liveDY ?? 0;

  if (dx === 0 && dy === 0) { draw(); return; }

  // The nodes have already been mutated live. Restore to pre-drag, then
  // dispatch a clean command so the undo stack gets the correct before/after.
  if (_preDragSnapshot) {
    loadDiagramSilent(_preDragSnapshot);
    _preDragSnapshot = null;
  }

  const nodeMap = getNodeMap();

  // Build move list: only root-level ids in selection (children move implicitly)
  const rootMoves = [];
  for (const id of ds.moves) {
    if (nodeMap.has(id.id)) {
      // Check none of the id's ancestors are also selected
      let parentId = nodeMap.get(id.id)?.parentId;
      let dominated = false;
      while (parentId) {
        if (selection.has(parentId)) { dominated = true; break; }
        parentId = nodeMap.get(parentId)?.parentId;
      }
      if (!dominated) rootMoves.push({ id: id.id, dx, dy });
    }
  }

  dispatch(cmdMoveNodes(rootMoves));

  // Check containment promotion/demotion after move
  checkReparenting(rootMoves);

  draw();
  notifySelectionChange();
}

function checkReparenting(moves) {
  const diagram = getDiagram();
  const nodeMap  = buildNodeMap(diagram.nodes);
  let changed = false;

  for (const { id } of moves) {
    const entry = nodeMap.get(id);
    if (!entry) continue;
    const node   = entry.node;
    const bounds = worldBounds(id, nodeMap);
    if (!bounds) continue;
    const cx = bounds.x + bounds.width  / 2;
    const cy = bounds.y + bounds.height / 2;

    // Find deepest node whose bounds contain (cx,cy), skipping self and descendants
    let bestCandidate = null;
    for (const [cid, ce] of nodeMap) {
      if (cid === id) continue;
      if (isDescendant(id, cid, nodeMap)) continue;
      const cb = worldBounds(cid, nodeMap);
      if (!cb) continue;
      if (cx > cb.x && cx < cb.x + cb.width && cy > cb.y && cy < cb.y + cb.height) {
        if (!bestCandidate) { bestCandidate = cid; continue; }
        // Prefer deeper (smaller area) match
        const ba = worldBounds(bestCandidate, nodeMap);
        if (cb.width * cb.height < ba.width * ba.height) bestCandidate = cid;
      }
    }

    const currentParent = entry.parentId;
    if (bestCandidate !== currentParent) {
      // Reparent: remove from current location, add to new parent (or root)
      reparentNode(id, bestCandidate);
      changed = true;
    }
  }
  if (changed) draw();
}

function isDescendant(ancestorId, nodeId, nodeMap) {
  let entry = nodeMap.get(nodeId);
  while (entry) {
    if (entry.parentId === ancestorId) return true;
    entry = nodeMap.get(entry.parentId);
  }
  return false;
}

export function reparentNode(nodeId, newParentId) {
  // Dispatch a compound command: snapshot-based
  dispatch({
    apply(d) {
      const nm = buildNodeMap(d.nodes);
      const srcEntry = nm.get(nodeId);
      if (!srcEntry) return d;
      const node = srcEntry.node;
      const worldB = worldBounds(nodeId, nm);

      // Remove from current parent
      if (srcEntry.parentId) {
        const parentNode = nm.get(srcEntry.parentId).node;
        parentNode.children = parentNode.children.filter(c => c.id !== nodeId);
        repackParent(parentNode);
      } else {
        d.nodes = d.nodes.filter(n => n.id !== nodeId);
      }

      if (newParentId) {
        const newParent = buildNodeMap(d.nodes).get(newParentId)?.node;
        if (!newParent) { d.nodes.push(node); return d; }
        // Convert world coords to parent-relative
        const parentB = worldBounds(newParentId, buildNodeMap(d.nodes));
        node.x = worldB.x - parentB.x;
        node.y = worldB.y - parentB.y;
        newParent.children.push(node);
        repackParent(newParent);
      } else {
        // Promote to root — keep world coords
        node.x = worldB.x;
        node.y = worldB.y;
        d.nodes.push(node);
      }
      return d;
    },
    undo(d) { return d; }, // snapshot-based
  });
}

// ── Resize helpers ─────────────────────────────────────────────────────────

function calcResizeBounds(origBounds, handleIdx, snapWX, snapWY) {
  const { x, y, width, height } = origBounds;
  let nx = x, ny = y, nw = width, nh = height;
  const right  = x + width;
  const bottom = y + height;

  const moveLeft   = [0,3,5].includes(handleIdx);
  const moveTop    = [0,1,2].includes(handleIdx);
  const moveRight  = [2,4,7].includes(handleIdx);
  const moveBottom = [5,6,7].includes(handleIdx);

  if (moveLeft)   { nx = Math.min(snapWX, right - 1); nw = right - nx; }
  if (moveTop)    { ny = Math.min(snapWY, bottom - 1); nh = bottom - ny; }
  if (moveRight)  { nw = Math.max(1, snapWX - x); }
  if (moveBottom) { nh = Math.max(1, snapWY - y); }

  return { nx, ny, nw, nh };
}

function updateResizeGhost(wx, wy) {
  const { id, handleIdx, origBounds } = _dragState;
  const { nx, ny, nw, nh } = calcResizeBounds(origBounds, handleIdx, snapToGrid(wx), snapToGrid(wy));

  // Directly mutate for live preview (restored from snapshot on commit)
  const nm = buildNodeMap(getDiagram().nodes);
  const entry = nm.get(id);
  if (!entry) return;

  let storedX = nx, storedY = ny;
  if (entry.parentId) {
    const pb = worldBounds(entry.parentId, nm);
    if (pb) { storedX = nx - pb.x; storedY = ny - pb.y; }
  }
  entry.node.x = storedX;
  entry.node.y = storedY;
  entry.node.width  = nw;
  entry.node.height = nh;
}

function commitResize(ds, sx, sy) {
  const w = screenToWorld(sx, sy);
  const { id, handleIdx, origBounds } = ds;
  const { nx, ny, nw, nh } = calcResizeBounds(origBounds, handleIdx, snapToGrid(w.x), snapToGrid(w.y));

  // Convert world position to parent-relative stored coords
  const nm = getNodeMap();
  const entry = nm.get(id);
  let storedX = nx, storedY = ny;
  if (entry?.parentId) {
    const pb = worldBounds(entry.parentId, nm);
    if (pb) { storedX = nx - pb.x; storedY = ny - pb.y; }
  }

  // Restore pre-drag state so the undo stack gets a clean before snapshot
  if (_preDragSnapshot) {
    loadDiagramSilent(_preDragSnapshot);
    _preDragSnapshot = null;
  }

  dispatch(cmdResizeNode(id, nw, nh, storedX, storedY));
  draw();
  notifySelectionChange();
}

function commitRubberBand(ds, endSX, endSY) {
  const r1 = screenToWorld(Math.min(ds.sx, endSX), Math.min(ds.sy, endSY));
  const r2 = screenToWorld(Math.max(ds.sx, endSX), Math.max(ds.sy, endSY));
  const nodeMap = getNodeMap();
  const diagram = getDiagram();

  selection.clear();
  for (const [id, entry] of nodeMap) {
    const b = worldBounds(id, nodeMap);
    if (!b) continue;
    const cx = b.x + b.width/2, cy = b.y + b.height/2;
    if (cx >= r1.x && cx <= r2.x && cy >= r1.y && cy <= r2.y) selection.add(id);
  }
  // Also select edges whose both endpoints are in the band
  for (const edge of diagram.edges) {
    if (selection.has(edge.from) && selection.has(edge.to)) selection.add(edge.id);
  }

  draw();
  notifySelectionChange();
}

function commitDrawShape(ds, endSX, endSY) {
  // Mousemove can be missed/coalesced on some systems; finalize from mouseup.
  if (!ds.previewW || !ds.previewH) {
    const w = screenToWorld(endSX, endSY);
    const sx2 = snapToGrid(w.x), sy2 = snapToGrid(w.y);
    ds.previewX = Math.min(ds.startW, sx2);
    ds.previewY = Math.min(ds.startH, sy2);
    ds.previewW = Math.abs(sx2 - ds.startW) || 1;
    ds.previewH = Math.abs(sy2 - ds.startH) || 1;
  }

  if (!ds.previewW || !ds.previewH) { draw(); return; }
  const node = newNode(ds.toolType, ds.previewX, ds.previewY, ds.previewW, ds.previewH);
  dispatch(cmdAddNode(node));
  selection.clear();
  selection.add(node.id);
  setTool('select');
  draw();
  notifySelectionChange();
}

function commitConnect(ds, sx, sy) {
  const hit = hitTest(sx, sy);
  const nodeMap = getNodeMap();
  if (hit && nodeMap.has(hit) && hit !== ds.fromId) {
    const tp = nearestScreenPort(hit, sx, sy);
    const edge = newEdge(ds.fromId, hit, ds.fromPort, tp ? tp.key : null);
    dispatch(cmdAddEdge(edge));
    selection.clear();
    selection.add(edge.id);
  }
  draw();
  notifySelectionChange();
}

function commitEdgeEndpoint(ds, sx, sy) {
  const hit = hitTest(sx, sy);
  const nodeMap = getNodeMap();
  if (hit && nodeMap.has(hit)) {
    const tp = nearestScreenPort(hit, sx, sy);
    dispatch(cmdRewireEdge(ds.edgeId, ds.end, hit, tp ? tp.key : null));
  }
  setGhostEdge(null);
  setPortHints([]);
  draw();
  notifySelectionChange();
}

// ── Double-click to edit label ─────────────────────────────────────────────

function onDblClick(e) {
  const rect = _canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  const hit = hitTest(sx, sy);
  if (!hit) return;

  const nodeMap = getNodeMap();
  const diagram = getDiagram();
  let item, bounds;

  if (nodeMap.has(hit)) {
    const entry = nodeMap.get(hit);
    item   = entry.node;
    bounds = worldBounds(hit, nodeMap);
  } else {
    item = diagram.edges.find(e => e.id === hit);
    if (!item) return;
    // Position editor at midpoint of edge
    const fromB = worldBounds(item.from, nodeMap);
    const toB   = worldBounds(item.to,   nodeMap);
    if (!fromB || !toB) return;
    bounds = {
      x: (fromB.x + fromB.width/2 + toB.x + toB.width/2) / 2 - 2,
      y: (fromB.y + fromB.height/2 + toB.y + toB.height/2) / 2 - 0.5,
      width: 4, height: 1,
    };
  }

  showLabelEditor(item, bounds);
}

function showLabelEditor(item, worldBounds) {
  const editor = document.getElementById('label-editor');
  const { gridSize } = getDiagram().settings;
  const gs = gridSize * view.zoom;

  const sx = worldBounds.x * gs + view.pan.x;
  const sy = worldBounds.y * gs + view.pan.y;
  const sw = worldBounds.width  * gs;
  const sh = worldBounds.height * gs;

  editor.style.display = 'block';
  editor.style.left    = sx + 'px';
  editor.style.top     = sy + 'px';
  editor.style.width   = Math.max(sw, 80) + 'px';
  editor.style.height  = Math.max(sh, 24) + 'px';
  editor.value         = item.label || '';
  editor.focus();
  editor.select();

  const commit = () => {
    const val = editor.value;
    editor.style.display = 'none';
    if (val !== item.label) dispatch(cmdSetLabel(item.id, val));
    draw();
    notifySelectionChange();
    editor.removeEventListener('blur',    commit);
    editor.removeEventListener('keydown', onKey);
  };

  const onKey = (e) => {
    if (e.key === 'Escape') { editor.value = item.label || ''; commit(); }
    if (e.key === 'Enter' && !e.shiftKey) commit();
    e.stopPropagation();
  };

  editor.addEventListener('blur',    commit,  { once: true });
  editor.addEventListener('keydown', onKey);
}

// ── Scroll to zoom ─────────────────────────────────────────────────────────

const ZOOM_STEP = 0.1;
const ZOOM_MIN  = 0.1;
const ZOOM_MAX  = 5;

function onWheel(e) {
  e.preventDefault();
  const rect = _canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  const factor = e.deltaY < 0 ? (1 + ZOOM_STEP) : (1 - ZOOM_STEP);
  const newZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, view.zoom * factor));

  // Zoom centred on cursor
  view.pan.x = sx - (sx - view.pan.x) * (newZoom / view.zoom);
  view.pan.y = sy - (sy - view.pan.y) * (newZoom / view.zoom);
  view.zoom  = newZoom;

  draw();
  document.dispatchEvent(new CustomEvent('kd:view-changed'));
}

// ── Keyboard ───────────────────────────────────────────────────────────────

function onKeyDown(e) {
  // Don't intercept while editing label
  if (document.activeElement?.id === 'label-editor') return;
  if (document.activeElement?.tagName === 'INPUT' ||
      document.activeElement?.tagName === 'TEXTAREA' ||
      document.activeElement?.tagName === 'SELECT') return;

  const ctrl = e.ctrlKey || e.metaKey;

  if (e.code === 'Space') { _spaceDown = true; e.preventDefault(); return; }

  if (ctrl && e.key === 'z') { e.preventDefault(); undo(); draw(); notifySelectionChange(); return; }
  if (ctrl && (e.key === 'y' || (e.shiftKey && e.key === 'Z'))) {
    e.preventDefault(); redo(); draw(); notifySelectionChange(); return;
  }
  if (ctrl && e.key === 'a') {
    e.preventDefault();
    const nodeMap = getNodeMap();
    selection.clear();
    for (const id of nodeMap.keys()) selection.add(id);
    for (const edge of getDiagram().edges) selection.add(edge.id);
    draw(); notifySelectionChange(); return;
  }
  if (ctrl && e.shiftKey && e.key === 'E') { e.preventDefault(); document.dispatchEvent(new CustomEvent('kd:export-png')); return; }
  if (ctrl && e.shiftKey && e.key === 'H') {
    e.preventDefault();
    view.pan.x = _canvas.width  / 2;
    view.pan.y = _canvas.height / 2;
    view.zoom  = 1;
    draw(); return;
  }

  // Tool shortcuts
  if (!ctrl) {
    if (e.key === 'v' || e.key === 'V') { setTool('select');   return; }
    if (e.key === 'r' || e.key === 'R') { setTool('rect');     return; }
    if (e.key === 'e' || e.key === 'E') { setTool('ellipse');  return; }
    if (e.key === 'c' || e.key === 'C') { setTool('connect');  return; }
    if (e.key === 'w' || e.key === 'W') { setTool('waypoint'); return; }
    if (e.key === 'h' || e.key === 'H') { setTool('pan');      return; }
  }

  if (e.key === 'Delete' || e.key === 'Backspace') {
    if (selection.size) {
      dispatch(cmdDeleteSelected());
      selection.clear();
      draw();
      notifySelectionChange();
    }
    return;
  }

  if (e.key === 'Escape') {
    selection.clear();
    draw();
    notifySelectionChange();
    return;
  }
}

function onKeyUp(e) {
  if (e.code === 'Space') _spaceDown = false;
}

// ── Selection change notification ──────────────────────────────────────────

function notifySelectionChange() {
  document.dispatchEvent(new CustomEvent('kd:selection-change'));
}


