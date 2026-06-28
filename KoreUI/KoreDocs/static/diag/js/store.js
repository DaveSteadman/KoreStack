/**
 * store.js — Central application state + command-pattern undo/redo.
 *
 * All mutations go through `dispatch(command)` so they can be undone.
 * Listeners subscribed via `on(event, fn)` get notified after each change.
 */

import { deepClone, buildNodeMap, newDiagram } from './model.js';

const MAX_UNDO = 200;

// ── State ──────────────────────────────────────────────────────────────────
let _diagram = newDiagram();
let _undoStack = [];   // each entry: { before, after } snapshots of _diagram
let _redoStack = [];
let _dirty = false;    // unsaved changes flag

// Derived / transient state (not persisted)
export const view = { pan: { x: 0, y: 0 }, zoom: 1 };
export const selection = new Set(); // node/edge ids

// ── Event bus ──────────────────────────────────────────────────────────────
const _listeners = {};
export function on(event, fn) {
  (_listeners[event] ??= []).push(fn);
}
function emit(event, data) {
  for (const fn of (_listeners[event] ?? [])) fn(data);
}

// ── Diagram accessors ──────────────────────────────────────────────────────
export function getDiagram() { return _diagram; }
export function isDirty()    { return _dirty; }

export function getNodeMap() { return buildNodeMap(_diagram.nodes); }

// ── Replace entire diagram (load / new) ───────────────────────────────────
export function loadDiagram(diagram) {
  _diagram    = diagram;
  _undoStack  = [];
  _redoStack  = [];
  _dirty      = false;
  selection.clear();
  emit('diagram-loaded', _diagram);
  emit('change', _diagram);
}

/** Restore diagram silently (no view reset, no event) — used by live-drag revert */
export function loadDiagramSilent(diagram) {
  _diagram = diagram;
}

// ── Command dispatch ───────────────────────────────────────────────────────
/**
 * Dispatch a mutating command. `command` is an object with:
 *   { apply(diagram), undo(diagram) }
 * where apply/undo mutate a *clone* of the diagram and return it.
 *
 * Simple usage: just pass `{ apply, undo }` functions.
 */
export function dispatch(command) {
  const before = deepClone(_diagram);
  const after  = command.apply(deepClone(_diagram));
  _diagram     = after;
  after.modified = new Date().toISOString();

  _undoStack.push({ before, after, undo: command.undo });
  if (_undoStack.length > MAX_UNDO) _undoStack.shift();
  _redoStack = [];
  _dirty = true;

  emit('change', _diagram);
}

export function undo() {
  if (!_undoStack.length) return;
  const entry = _undoStack.pop();
  _diagram = entry.before;
  _redoStack.push(entry);
  _dirty = true;
  // Re-validate selection
  const nm = getNodeMap();
  for (const id of [...selection]) {
    if (!nm.has(id) && !_diagram.edges.find(e => e.id === id)) selection.delete(id);
  }
  emit('change', _diagram);
}

export function redo() {
  if (!_redoStack.length) return;
  const entry = _redoStack.pop();
  _diagram = entry.after;
  _undoStack.push(entry);
  _dirty = true;
  emit('change', _diagram);
}

export function canUndo() { return _undoStack.length > 0; }
export function canRedo() { return _redoStack.length > 0; }

export function markSaved()  { _dirty = false; }
export function markDirty()  { _dirty = true; }

// ── Convenience command factories ─────────────────────────────────────────

/** Add a root-level node */
export function cmdAddNode(node) {
  return {
    apply(d) { d.nodes.push(node); return d; },
    undo(d)  { d.nodes = d.nodes.filter(n => n.id !== node.id); return d; },
  };
}

/** Add an edge */
export function cmdAddEdge(edge) {
  return {
    apply(d) { d.edges.push(edge); return d; },
    undo(d)  { d.edges = d.edges.filter(e => e.id !== edge.id); return d; },
  };
}

/** Delete selected nodes and edges */
export function cmdDeleteSelected() {
  const ids = new Set(selection);
  return {
    apply(d) {
      // Remove edges that touch deleted nodes or are themselves selected
      d.edges = d.edges.filter(e => !ids.has(e.id) && !ids.has(e.from) && !ids.has(e.to));
      // Remove nodes (recursive)
      function removeNodes(arr) {
        return arr.filter(n => {
          if (ids.has(n.id)) return false;
          n.children = removeNodes(n.children);
          return true;
        });
      }
      d.nodes = removeNodes(d.nodes);
      return d;
    },
    undo(d) { /* undo is handled by snapshot restore — no-op here */ return d; },
  };
}

/** Move a set of nodes by (dx, dy) in grid units */
export function cmdMoveNodes(moves) {
  // moves: [ { id, dx, dy } ]
  return {
    apply(d) {
      const nm = buildNodeMap(d.nodes);
      for (const { id, dx, dy } of moves) {
        const entry = nm.get(id);
        if (entry) { entry.node.x += dx; entry.node.y += dy; }
      }
      return d;
    },
    undo(d) {
      const nm = buildNodeMap(d.nodes);
      for (const { id, dx, dy } of moves) {
        const entry = nm.get(id);
        if (entry) { entry.node.x -= dx; entry.node.y -= dy; }
      }
      return d;
    },
  };
}

/** Resize a node */
export function cmdResizeNode(id, newW, newH, newX, newY) {
  return {
    apply(d) {
      const nm = buildNodeMap(d.nodes);
      const e = nm.get(id);
      if (e) { e.node.width = newW; e.node.height = newH; e.node.x = newX; e.node.y = newY; }
      return d;
    },
    undo(d) { return d; }, // snapshot-based
  };
}

/** Set a node's or edge's label */
export function cmdSetLabel(id, newLabel) {
  return {
    apply(d) {
      const nm = buildNodeMap(d.nodes);
      const e = nm.get(id);
      if (e) { e.node.label = newLabel; return d; }
      const edge = d.edges.find(x => x.id === id);
      if (edge) edge.label = newLabel;
      return d;
    },
    undo(d) { return d; },
  };
}

/** Update a node's style */
export function cmdSetStyle(id, stylePatch) {
  return {
    apply(d) {
      const nm = buildNodeMap(d.nodes);
      const e = nm.get(id);
      if (e) {
        e.node.style = e.node.style || {};
        Object.assign(e.node.style, stylePatch);
      }
      return d;
    },
    undo(d) { return d; },
  };
}

/** Update a node's or edge's meta key */
export function cmdSetMeta(id, key, value) {
  return {
    apply(d) {
      const nm = buildNodeMap(d.nodes);
      const e = nm.get(id);
      const obj = e ? e.node : d.edges.find(x => x.id === id);
      if (obj) {
        if (value === null) delete obj.meta[key];
        else obj.meta[key] = value;
      }
      return d;
    },
    undo(d) { return d; },
  };
}

/** Reparent: move node to new parent (or null = root) */
export function cmdReparent(nodeId, newParentId) {
  return {
    apply(d) { return d; }, // full snapshot undo — actual reparenting done by interaction.js
    undo(d) { return d; },
  };
}

/**
 * Patch a sub-object inside diagram.settings.
 * e.g. cmdSetDiagramSetting('defaultNodeStyle', { fillColor: '#ff0000' })
 */
export function cmdSetDiagramSetting(key, patch) {
  return {
    apply(d) {
      d.settings[key] = { ...(d.settings[key] ?? {}), ...patch };
      return d;
    },
    undo(d) { return d; },
  };
}

/** Rewire one end of an edge to a different node + port */
export function cmdRewireEdge(edgeId, end, newNodeId, newPort) {
  return {
    apply(d) {
      const edge = d.edges.find(e => e.id === edgeId);
      if (!edge) return d;
      if (end === 'from') { edge.from = newNodeId; edge.fromPort = newPort ?? null; }
      else                { edge.to   = newNodeId; edge.toPort   = newPort ?? null; }
      return d;
    },
    undo(d) { return d; },
  };
}
