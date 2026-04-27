/**
 * model.js — Pure data helpers. No DOM, no state.
 * Creates blank diagrams, nodes, edges and provides UUID generation.
 */

export function uuid() {
  // crypto.randomUUID is available in all modern browsers
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  // Fallback (older browsers)
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = Math.random() * 16 | 0;
    return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
  });
}

export function newDiagram(title = 'Untitled') {
  const now = new Date().toISOString();
  return {
    koreDiag: '1.0',
    id: uuid(),
    title,
    created: now,
    modified: now,
    settings: {
      gridSize: 20,
      defaultArrow: 'forward',
      showGrid: true,
      defaultNodeStyle: {
        fillColor:   '#ffffff',
        strokeColor: '#5a5a8a',
        strokeWidth: 1.5,
        fontSize:    13,
        bold:        false,
        italic:      false,
        labelHAlign: 'center',
        labelVAlign: 'middle',
      },
      customColors: [],
    },
    nodes: [],
    edges: [],
  };
}

export function newNode(type, x, y, w, h, label = '') {
  return {
    id: uuid(),
    type,           // 'rect' | 'ellipse' | 'waypoint'
    label,
    x, y,           // grid units, relative to parent (or world if root)
    width: w,
    height: h,
    style: type === 'waypoint' ? { fillColor: 'transparent' } : {},
    meta: {},
    children: [],
  };
}

export function newEdge(fromId, toId, fromPort = null, toPort = null) {
  return {
    id: uuid(),
    from: fromId,
    to: toId,
    fromPort,           // port key ('n'|'ne'|'e'|'se'|'s'|'sw'|'w'|'nw') or null
    toPort,             // port key on target node, or null
    via: [],
    label: '',
    arrow: 'forward',   // inherit from diagram.settings.defaultArrow at render
    routing: 'straight',
    style: {},          // overrides only; falls back to diagram.settings.defaultEdgeStyle
    meta: {},
  };
}

/**
 * Flatten the nested node tree into a map: id → node
 * Also records each node's parentId (null for root nodes).
 */
export function buildNodeMap(nodes, parentId = null, map = new Map()) {
  for (const n of nodes) {
    map.set(n.id, { node: n, parentId });
    if (n.children?.length) buildNodeMap(n.children, n.id, map);
  }
  return map;
}

/**
 * Compute world-space bounding box {x,y,width,height} in grid units
 * for a node, walking up its ancestor chain.
 */
export function worldBounds(nodeId, nodeMap) {
  const entry = nodeMap.get(nodeId);
  if (!entry) return null;
  const { node, parentId } = entry;
  if (!parentId) return { x: node.x, y: node.y, width: node.width, height: node.height };
  const pb = worldBounds(parentId, nodeMap);
  return {
    x: pb.x + node.x,
    y: pb.y + node.y,
    width: node.width,
    height: node.height,
  };
}

/**
 * Return the 8 port positions (world grid units) for a node.
 * Ports: n, ne, e, se, s, sw, w, nw
 */
export function getPorts(bounds) {
  const { x, y, width: w, height: h } = bounds;
  const cx = x + w / 2, cy = y + h / 2;
  return {
    n:  { x: cx,     y: y },
    ne: { x: x + w,  y: y },
    e:  { x: x + w,  y: cy },
    se: { x: x + w,  y: y + h },
    s:  { x: cx,     y: y + h },
    sw: { x: x,      y: y + h },
    w:  { x: x,      y: cy },
    nw: { x: x,      y: y },
  };
}

/** Nearest port key to a given world point */
export function nearestPort(bounds, wx, wy) {
  const ports = getPorts(bounds);
  let best = null, bestDist = Infinity;
  for (const [key, pt] of Object.entries(ports)) {
    const d = Math.hypot(pt.x - wx, pt.y - wy);
    if (d < bestDist) { bestDist = d; best = key; }
  }
  return best;
}

/**
 * Removes a node (by id) from the nodes tree; returns the removed node or null.
 * Mutates the array in the caller's diagram — use within a cloned diagram for undo.
 */
export function removeNodeById(nodes, id) {
  for (let i = 0; i < nodes.length; i++) {
    if (nodes[i].id === id) return nodes.splice(i, 1)[0];
    const found = removeNodeById(nodes[i].children, id);
    if (found) return found;
  }
  return null;
}

/** Deep-clone a plain JSON-serialisable object */
export function deepClone(obj) {
  return JSON.parse(JSON.stringify(obj));
}

/**
 * Recalculate parent size so it wraps all direct children with `pad` grid units
 * of padding on each side. Mutates node.width/height/children[*].x/y.
 */
export function repackParent(parent, pad = 1) {
  if (!parent.children.length) return;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const c of parent.children) {
    minX = Math.min(minX, c.x);
    minY = Math.min(minY, c.y);
    maxX = Math.max(maxX, c.x + c.width);
    maxY = Math.max(maxY, c.y + c.height);
  }
  // Shift children so they sit `pad` units from parent's top-left
  const dx = pad - minX, dy = pad - minY;
  for (const c of parent.children) { c.x += dx; c.y += dy; }
  parent.width  = maxX - minX + pad * 2;
  parent.height = maxY - minY + pad * 2;
}
