/**
 * workspace.js — data-attribute-driven multi-region grid layout with draggable splitters.
 *
 * Reads `data-kcui-workspace`, `data-kcui-workspace-region`, and
 * `data-kcui-workspace-splitter` attributes to build a CSS grid layout with resizable
 * column and/or row tracks.  Pointer-capture drag on splitter elements resizes adjacent
 * tracks proportionally; layout fractions are persisted and restored via localStorage.
 *
 * Key class  WorkspaceLayout:
 *   init()                — parse data attributes and build initial grid state
 *   bindSplitters()       — attach pointerdown drag handlers to all splitter elements
 *   startDrag(e, axis)    — begin a pointer-capture drag for a splitter
 *   applyTracks(axis)     — write computed track sizes back to the grid element
 *   loadState() / saveState() — persist and restore track fractions via localStorage
 *   reapply()             — re-apply saved fractions (e.g. after a window resize)
 *
 * Usage:
 *   The module auto-initialises on DOMContentLoaded; all instances are in `workspaces`.
 *
 * Related modules:
 *   - workspace.css   — .kcui-workspace, .kcui-workspace__region, splitter styles
 */

const DRAG_CLASS_X = 'kcui-workspace-drag--x';
const DRAG_CLASS_Y = 'kcui-workspace-drag--y';

const workspaces = new Map();

function parseIndexList(raw) {
  if (!raw) return [];
  return raw
    .split(',')
    .map((value) => Number.parseInt(value.trim(), 10))
    .filter((value) => Number.isInteger(value) && value >= 0);
}

function parseNumberList(raw) {
  if (!raw) return [];
  return raw
    .split(',')
    .map((value) => Number.parseFloat(value.trim()))
    .filter((value) => Number.isFinite(value) && value >= 0);
}

function parseTrackPixels(template) {
  const matches = String(template || '').match(/-?\d+(?:\.\d+)?px/g) || [];
  return matches.map((value) => Number.parseFloat(value));
}

function sum(values) {
  return values.reduce((acc, value) => acc + value, 0);
}

function getPadding(style, axis) {
  if (axis === 'columns') {
    return (
      Number.parseFloat(style.paddingLeft || '0') +
      Number.parseFloat(style.paddingRight || '0')
    );
  }
  return (
    Number.parseFloat(style.paddingTop || '0') +
    Number.parseFloat(style.paddingBottom || '0')
  );
}

function buildTemplate(values) {
  return values.map((value) => `${Math.max(0, Math.round(value))}px`).join(' ');
}

class WorkspaceLayout {
  constructor(element) {
    this.element = element;
    this.key = element.dataset.kcuiLayoutKey || '';
    this.disableBelow = Number.parseInt(element.dataset.kcuiDisableBelow || '', 10);
    this.columnTracks = parseIndexList(element.dataset.kcuiColumns);
    this.rowTracks = parseIndexList(element.dataset.kcuiRows);
    this.columnMins = parseNumberList(element.dataset.kcuiColumnsMin);
    this.rowMins = parseNumberList(element.dataset.kcuiRowsMin);
    this.baseColumns = [];
    this.baseRows = [];
    this._resizeHandler = () => this.reapply();
  }

  isResponsiveDisabled() {
    return Number.isInteger(this.disableBelow) && window.innerWidth < this.disableBelow;
  }

  init() {
    this.captureBaseTracks();
    this.bindSplitters();
    this.reapply();
    window.addEventListener('resize', this._resizeHandler);
  }

  captureBaseTracks() {
    const style = getComputedStyle(this.element);
    this.baseColumns = parseTrackPixels(style.gridTemplateColumns);
    this.baseRows = parseTrackPixels(style.gridTemplateRows);
  }

  bindSplitters() {
    for (const splitter of this.element.querySelectorAll('[data-kcui-splitter]')) {
      if (splitter.dataset.kcuiBound === 'true') continue;
      splitter.dataset.kcuiBound = 'true';
      splitter.addEventListener('pointerdown', (event) => this.startDrag(splitter, event));
    }
  }

  startDrag(splitter, event) {
    const axis = splitter.dataset.kcuiAxis === 'rows' ? 'rows' : 'columns';
    const beforeIndex = Number.parseInt(splitter.dataset.kcuiBefore || '', 10);
    const afterIndex = Number.parseInt(splitter.dataset.kcuiAfter || '', 10);
    if (!Number.isInteger(beforeIndex) || !Number.isInteger(afterIndex)) return;

    const startTracks = this.getCurrentTracks(axis);
    const startPointer = axis === 'columns' ? event.clientX : event.clientY;
    const pointerClass = axis === 'columns' ? DRAG_CLASS_X : DRAG_CLASS_Y;

    splitter.classList.add('is-dragging');
    document.body.classList.add(pointerClass);
    splitter.setPointerCapture(event.pointerId);

    const move = (moveEvent) => {
      const delta = (axis === 'columns' ? moveEvent.clientX : moveEvent.clientY) - startPointer;
      const tracks = [...startTracks];
      const minBefore = this.getTrackMin(axis, beforeIndex);
      const minAfter = this.getTrackMin(axis, afterIndex);
      const proposedBefore = Math.max(minBefore, startTracks[beforeIndex] + delta);
      const proposedAfter = Math.max(minAfter, startTracks[afterIndex] - delta);
      tracks[beforeIndex] = proposedBefore;
      tracks[afterIndex] = proposedAfter;
      this.applyTracks(axis, tracks);
      this.saveFractions(axis, tracks);
    };

    const end = () => {
      splitter.classList.remove('is-dragging');
      document.body.classList.remove(pointerClass);
      splitter.removeEventListener('pointermove', move);
      splitter.removeEventListener('pointerup', end);
      splitter.removeEventListener('pointercancel', end);
    };

    splitter.addEventListener('pointermove', move);
    splitter.addEventListener('pointerup', end);
    splitter.addEventListener('pointercancel', end);
  }

  getCurrentTracks(axis) {
    const style = getComputedStyle(this.element);
    return axis === 'columns'
      ? parseTrackPixels(style.gridTemplateColumns)
      : parseTrackPixels(style.gridTemplateRows);
  }

  getTrackMin(axis, trackIndex) {
    const indices = axis === 'columns' ? this.columnTracks : this.rowTracks;
    const mins = axis === 'columns' ? this.columnMins : this.rowMins;
    const position = indices.indexOf(trackIndex);
    if (position === -1) return 48;
    return mins[position] ?? 48;
  }

  applyTracks(axis, tracks) {
    if (!tracks.length) return;
    if (axis === 'columns') {
      this.element.style.gridTemplateColumns = buildTemplate(tracks);
      return;
    }
    this.element.style.gridTemplateRows = buildTemplate(tracks);
  }

  loadState() {
    if (!this.key) return null;
    try {
      const raw = window.localStorage.getItem(`kcui_workspace_${this.key}`);
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  saveState(state) {
    if (!this.key) return;
    try {
      window.localStorage.setItem(`kcui_workspace_${this.key}`, JSON.stringify(state));
    } catch (_) {
      // Ignore storage errors.
    }
  }

  defaultFractions(axis) {
    const baseTracks = axis === 'columns' ? this.baseColumns : this.baseRows;
    const contentTracks = axis === 'columns' ? this.columnTracks : this.rowTracks;
    if (!contentTracks.length) return null;
    const total = sum(contentTracks.map((index) => baseTracks[index] || 0));
    if (!total) return null;
    return contentTracks.map((index) => (baseTracks[index] || 0) / total);
  }

  saveFractions(axis, tracks) {
    const contentTracks = axis === 'columns' ? this.columnTracks : this.rowTracks;
    if (!contentTracks.length) return;
    const total = sum(contentTracks.map((index) => tracks[index] || 0));
    if (!total) return;
    const state = this.loadState() || {};
    state[axis] = contentTracks.map((index) => (tracks[index] || 0) / total);
    this.saveState(state);
  }

  reapplyAxis(axis) {
    const contentTracks = axis === 'columns' ? this.columnTracks : this.rowTracks;
    const mins = axis === 'columns' ? this.columnMins : this.rowMins;
    const baseTracks = axis === 'columns' ? [...this.baseColumns] : [...this.baseRows];
    if (!contentTracks.length || !baseTracks.length) return;

    const state = this.loadState() || {};
    const fractions = state[axis] || this.defaultFractions(axis);
    if (!fractions || fractions.length !== contentTracks.length) return;

    const style = getComputedStyle(this.element);
    const clientSize = axis === 'columns' ? this.element.clientWidth : this.element.clientHeight;
    const trackSpace = Math.max(0, clientSize - getPadding(style, axis));
    const fixedSum = sum(baseTracks.filter((_, index) => !contentTracks.includes(index)));
    const available = Math.max(0, trackSpace - fixedSum);
    const minimumSum = sum(contentTracks.map((_, index) => mins[index] ?? 48));
    const target = available > minimumSum ? available : minimumSum;

    let used = 0;
    for (let index = 0; index < contentTracks.length; index += 1) {
      const trackIndex = contentTracks[index];
      const min = mins[index] ?? 48;
      if (index === contentTracks.length - 1) {
        baseTracks[trackIndex] = Math.max(min, target - used);
      } else {
        const size = Math.max(min, Math.round((fractions[index] || 0) * target));
        baseTracks[trackIndex] = size;
        used += size;
      }
    }

    this.applyTracks(axis, baseTracks);
  }

  reapply() {
    if (this.isResponsiveDisabled()) {
      this.element.style.removeProperty('grid-template-columns');
      this.element.style.removeProperty('grid-template-rows');
      return;
    }
    this.reapplyAxis('columns');
    this.reapplyAxis('rows');
  }

  reset() {
    if (this.key) {
      try {
        window.localStorage.removeItem(`kcui_workspace_${this.key}`);
      } catch (_) {
        // Ignore storage errors.
      }
    }
    this.captureBaseTracks();
    this.element.style.removeProperty('grid-template-columns');
    this.element.style.removeProperty('grid-template-rows');
    this.captureBaseTracks();
    this.reapply();
  }
}

function initWorkspaceLayouts(root = document) {
  const elements = root.querySelectorAll('[data-kcui-workspace]');
  for (const element of elements) {
    if (element.dataset.kcuiWorkspaceReady === 'true') continue;
    element.dataset.kcuiWorkspaceReady = 'true';
    const workspace = new WorkspaceLayout(element);
    workspace.init();
    if (workspace.key) {
      workspaces.set(workspace.key, workspace);
    }
  }
}

function resetWorkspaceLayout(keyOrElement) {
  if (!keyOrElement) {
    for (const workspace of workspaces.values()) {
      workspace.reset();
    }
    return;
  }

  if (typeof keyOrElement === 'string') {
    workspaces.get(keyOrElement)?.reset();
    return;
  }

  for (const workspace of workspaces.values()) {
    if (workspace.element === keyOrElement) {
      workspace.reset();
      return;
    }
  }
}

window.resetWorkspaceLayout = resetWorkspaceLayout;
window.initWorkspaceLayouts = initWorkspaceLayouts;

export { initWorkspaceLayouts, resetWorkspaceLayout };