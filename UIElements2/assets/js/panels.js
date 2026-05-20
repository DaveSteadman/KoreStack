/**
 * panels.js — draggable split-panel layout utility.
 *
 * Usage:
 *   import { initPanels } from '/ui-elements-2/assets/js/panels.js';
 *
 *   initPanels({
 *     panelsEl:   document.getElementById('my-panels'),   // .kcui-panels grid container
 *     leftEl:     document.getElementById('my-sidebar'),  // left panel element
 *     splitterEl: document.getElementById('my-splitter'), // .kcui-splitter divider element
 *     minLeft:    160,           // minimum left panel width in px (default 160)
 *     maxLeft:    600,           // maximum left panel width in px (default 600)
 *     storageKey: 'my-sidebar',  // localStorage key to persist width (optional)
 *   });
 *
 * Works alongside .kcui-panels / .kcui-splitter from panels.css.
 */

export function initPanels({
  panelsEl,
  leftEl,
  splitterEl,
  minLeft = 160,
  maxLeft = 600,
  storageKey = null,
}) {
  let dragStartX = null;
  let dragStartW = null;

  function applyWidth(w) {
    panelsEl.style.gridTemplateColumns =
      `${w}px var(--kcui-splitter-w, 4px) minmax(0, 1fr)`;
    if (storageKey) {
      try { localStorage.setItem(storageKey, String(w)); } catch (_) { /* ignore */ }
    }
  }

  // Restore persisted width from a previous session.
  if (storageKey) {
    try {
      const saved = parseInt(localStorage.getItem(storageKey), 10);
      if (!Number.isNaN(saved) && saved >= minLeft && saved <= maxLeft) {
        applyWidth(saved);
      }
    } catch (_) { /* ignore */ }
  }

  splitterEl.addEventListener('mousedown', (e) => {
    e.preventDefault();
    dragStartX = e.clientX;
    dragStartW = leftEl.getBoundingClientRect().width;
    splitterEl.classList.add('is-dragging');
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
  });

  document.addEventListener('mousemove', (e) => {
    if (dragStartX === null) { return; }
    const delta = e.clientX - dragStartX;
    const newW = Math.max(minLeft, Math.min(maxLeft, dragStartW + delta));
    applyWidth(newW);
  });

  document.addEventListener('mouseup', () => {
    if (dragStartX === null) { return; }
    dragStartX = null;
    dragStartW = null;
    splitterEl.classList.remove('is-dragging');
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
  });
}
