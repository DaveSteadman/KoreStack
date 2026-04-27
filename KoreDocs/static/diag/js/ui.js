/**
 * ui.js — Menu bar, Properties panel, Hierarchy panel, title bar.
 */

import {
  getDiagram, getNodeMap, selection,
  dispatch, canUndo, canRedo,
  cmdSetLabel, cmdSetStyle, cmdSetMeta, cmdDeleteSelected,
  cmdSetDiagramSetting,
  undo, redo, isDirty,
} from './store.js';
import { worldBounds, buildNodeMap } from './model.js';
import { draw } from './renderer.js';
import { setTool, reparentNode } from './interaction.js';
import { initAppMenuEvents } from '/static/commonui/js/appMenu.js';

const CUSTOM_COLOR_MIGRATION_KEY = 'korediag.customColors.v1';
const CUSTOM_COLOR_LIMIT = 7;
let _nodePropsTab = 'text';

// ── Menu bar ───────────────────────────────────────────────────────────────

export function initMenus() {
  initAppMenuEvents(handleMenuAction);

  // Diagram title rename (double-click)
  const titleEl = document.getElementById('diag-title');
  titleEl.addEventListener('dblclick', () => {
    const val = prompt('Diagram title:', getDiagram().title);
    if (val !== null && val.trim()) {
      dispatch({
        apply(d) { d.title = val.trim(); return d; },
        undo(d)  { return d; },
      });
      titleEl.textContent = val.trim();
    }
  });
}

function handleMenuAction(action) {
  switch (action) {
    case 'export-png':   document.dispatchEvent(new CustomEvent('kd:export-png')); break;
    case 'undo':         undo(); draw(); notifyUI(); break;
    case 'redo':         redo(); draw(); notifyUI(); break;
    case 'select-all': {
      const nodeMap = getNodeMap();
      selection.clear();
      for (const id of nodeMap.keys()) selection.add(id);
      for (const edge of getDiagram().edges) selection.add(edge.id);
      draw(); notifyUI(); break;
    }
    case 'delete':
      if (selection.size) {
        dispatch(cmdDeleteSelected());
        selection.clear();
        draw(); notifyUI();
      }
      break;
    case 'zoom-in':    document.dispatchEvent(new CustomEvent('kd:zoom', { detail: 1.2  })); break;
    case 'zoom-out':   document.dispatchEvent(new CustomEvent('kd:zoom', { detail: 0.8  })); break;
    case 'reset-view': document.dispatchEvent(new CustomEvent('kd:reset-view')); break;
    case 'toggle-grid':
      dispatch({
        apply(d) { d.settings.showGrid = !d.settings.showGrid; return d; },
        undo(d)  { d.settings.showGrid = !d.settings.showGrid; return d; },
      });
      draw();
      break;
  }
}

function notifyUI() {
  document.dispatchEvent(new CustomEvent('kd:selection-change'));
}

// ── Tool sidebar ───────────────────────────────────────────────────────────

export function initToolbar() {
  document.querySelectorAll('.tool-btn').forEach(btn => {
    btn.addEventListener('click', () => setTool(btn.dataset.tool));
  });
}

// ── Diagram-level properties (shown when nothing is selected) ─────────────

function renderDiagramProps(panel) {
  panel.innerHTML = '';
  const diagram = getDiagram();
  const ns = { fillColor: '#ffffff', strokeColor: '#5a5a8a', strokeWidth: 1.5, fontSize: 13,
                bold: false, italic: false, labelHAlign: 'center', labelVAlign: 'middle',
                ...(diagram.settings.defaultNodeStyle ?? {}) };
  const body = createPropertiesTabs(panel, {
    text: 'Text',
    shape: 'Shape',
  }, _nodePropsTab, tab => {
    _nodePropsTab = tab;
    renderDiagramProps(panel);
  });

  addSectionLabel(body, 'Default style');
  if (_nodePropsTab === 'text') {
    addPropRow(body, 'Font size', 'number', ns.fontSize, val => {
      dispatch(cmdSetDiagramSetting('defaultNodeStyle', { fontSize: parseInt(val) || 13 })); draw();
    }, { min: 8, max: 72 });
    addToggleRow(body, 'Style', [
      { key: 'bold', label: 'B', active: Boolean(ns.bold), onToggle: () => { dispatch(cmdSetDiagramSetting('defaultNodeStyle', { bold: !ns.bold })); draw(); renderDiagramProps(panel); } },
      { key: 'italic', label: 'I', active: Boolean(ns.italic), onToggle: () => { dispatch(cmdSetDiagramSetting('defaultNodeStyle', { italic: !ns.italic })); draw(); renderDiagramProps(panel); } },
    ]);
    addSelectRow(body, 'Horizontal', ns.labelHAlign, [
      ['left', 'Left'], ['center', 'Middle'], ['right', 'Right'],
    ], val => { dispatch(cmdSetDiagramSetting('defaultNodeStyle', { labelHAlign: val })); draw(); });
    addSelectRow(body, 'Vertical', ns.labelVAlign, [
      ['top', 'Top'], ['middle', 'Middle'], ['bottom', 'Bottom'],
    ], val => { dispatch(cmdSetDiagramSetting('defaultNodeStyle', { labelVAlign: val })); draw(); });
    return;
  }

  addPropRow(body, 'Fill', 'color', ns.fillColor, val => { dispatch(cmdSetDiagramSetting('defaultNodeStyle', { fillColor: val })); draw(); });
  addPropRow(body, 'Stroke color', 'color', ns.strokeColor, val => { dispatch(cmdSetDiagramSetting('defaultNodeStyle', { strokeColor: val })); draw(); });
  addPropRow(body, 'Line width', 'number', ns.strokeWidth, val => { dispatch(cmdSetDiagramSetting('defaultNodeStyle', { strokeWidth: parseFloat(val) || 1.5 })); draw(); }, { min: 0.5, max: 20, step: 0.5 });
}

function addSectionLabel(panel, text) {
  const el = document.createElement('label');
  el.style.cssText = 'font-size:11px;color:var(--text-dim);display:block;margin-top:10px;margin-bottom:4px;';
  el.textContent = text;
  panel.appendChild(el);
}

// ── Properties panel ───────────────────────────────────────────────────────

export function refreshProperties() {
  const panel = document.getElementById('props-content');
  const title = document.getElementById('props-title');

  if (selection.size === 0) {
    if (title) title.textContent = 'Diagram Properties';
    renderDiagramProps(panel);
    return;
  }
  if (title) title.textContent = 'Properties';

  if (selection.size > 1) {
    panel.innerHTML = `<p class="hint">${selection.size} items selected</p>`;
    return;
  }

  const [id] = selection;
  const nodeMap = getNodeMap();
  const diagram = getDiagram();

  if (nodeMap.has(id)) {
    const node = nodeMap.get(id).node;
    renderNodeProps(panel, node);
  } else {
    const edge = diagram.edges.find(e => e.id === id);
    if (edge) renderEdgeProps(panel, edge);
  }
}

function renderNodeProps(panel, node) {
  const isWaypoint = node.type === 'waypoint';
  const nodeMap = getNodeMap();
  panel.innerHTML = '';

  if (!isWaypoint) {
    const body = createPropertiesTabs(panel, {
      text: 'Text',
      shape: 'Shape',
    }, _nodePropsTab, tab => {
      _nodePropsTab = tab;
      renderNodeProps(panel, node);
    });

    const nodeDefs = getDiagram().settings.defaultNodeStyle || {};
    const effStyle = { bold: false, italic: false, labelHAlign: 'center', labelVAlign: 'middle', ...nodeDefs, ...node.style };

    if (_nodePropsTab === 'text') {
      addPropRow(body, 'Label', 'text', node.label, val => {
        dispatch(cmdSetLabel(node.id, val));
        draw();
      });
      addPropRow(body, 'Font size', 'number', effStyle.fontSize || 13, val => {
        dispatch(cmdSetStyle(node.id, { fontSize: parseInt(val) || 13 })); draw();
      }, { min: 8, max: 72 }, node.style && 'fontSize' in node.style ? () => {
        dispatch({ apply(d) { const e = buildNodeMap(d.nodes).get(node.id); if (e) delete e.node.style.fontSize; return d; }, undo(d) { return d; } });
        draw(); refreshProperties();
      } : null);
      addToggleRow(body, 'Style', [
        { key: 'bold', label: 'B', active: Boolean(effStyle.bold), inherited: !(node.style && 'bold' in node.style), onToggle: () => { dispatch(cmdSetStyle(node.id, { bold: !effStyle.bold })); draw(); refreshProperties(); }, onClear: node.style && 'bold' in node.style ? () => { dispatch({ apply(d) { const e = buildNodeMap(d.nodes).get(node.id); if (e) delete e.node.style.bold; return d; }, undo(d) { return d; } }); draw(); refreshProperties(); } : null },
        { key: 'italic', label: 'I', active: Boolean(effStyle.italic), inherited: !(node.style && 'italic' in node.style), onToggle: () => { dispatch(cmdSetStyle(node.id, { italic: !effStyle.italic })); draw(); refreshProperties(); }, onClear: node.style && 'italic' in node.style ? () => { dispatch({ apply(d) { const e = buildNodeMap(d.nodes).get(node.id); if (e) delete e.node.style.italic; return d; }, undo(d) { return d; } }); draw(); refreshProperties(); } : null },
      ]);
      addSelectRow(body, 'Horizontal', effStyle.labelHAlign, [
        ['left', 'Left'], ['center', 'Middle'], ['right', 'Right'],
      ], val => { dispatch(cmdSetStyle(node.id, { labelHAlign: val })); draw(); refreshProperties(); }, node.style && 'labelHAlign' in node.style ? () => {
        dispatch({ apply(d) { const e = buildNodeMap(d.nodes).get(node.id); if (e) delete e.node.style.labelHAlign; return d; }, undo(d) { return d; } });
        draw(); refreshProperties();
      } : null);
      addSelectRow(body, 'Vertical', effStyle.labelVAlign, [
        ['top', 'Top'], ['middle', 'Middle'], ['bottom', 'Bottom'],
      ], val => { dispatch(cmdSetStyle(node.id, { labelVAlign: val })); draw(); refreshProperties(); }, node.style && 'labelVAlign' in node.style ? () => {
        dispatch({ apply(d) { const e = buildNodeMap(d.nodes).get(node.id); if (e) delete e.node.style.labelVAlign; return d; }, undo(d) { return d; } });
        draw(); refreshProperties();
      } : null);
    } else {
      const sizeRow = document.createElement('div');
      sizeRow.className = 'prop-row-inline';
      sizeRow.innerHTML = `
        <div class="prop-row"><label>Width</label><input type="number" min="1" value="${node.width}"/></div>
        <div class="prop-row"><label>Height</label><input type="number" min="1" value="${node.height}"/></div>`;
      body.appendChild(sizeRow);
      const [wInput, hInput] = sizeRow.querySelectorAll('input');
      const commitSize = () => {
        const nw = Math.max(1, parseInt(wInput.value) || 1);
        const nh = Math.max(1, parseInt(hInput.value) || 1);
        dispatch({ apply(d) {
          const nm = buildNodeMap(d.nodes);
          const e = nm.get(node.id);
          if (e) { e.node.width = nw; e.node.height = nh; }
          return d;
        }, undo(d) { return d; } });
        draw();
      };
      wInput.addEventListener('change', commitSize);
      hInput.addEventListener('change', commitSize);

      addPropRow(body, 'Fill', 'color', effStyle.fillColor || '#ffffff', val => {
        dispatch(cmdSetStyle(node.id, { fillColor: val })); draw();
      }, {}, node.style && 'fillColor' in node.style ? () => {
        dispatch({ apply(d) { const e = buildNodeMap(d.nodes).get(node.id); if (e) delete e.node.style.fillColor; return d; }, undo(d) { return d; } });
        draw(); refreshProperties();
      } : null);
      addPropRow(body, 'Stroke', 'color', effStyle.strokeColor || '#5a5a8a', val => {
        dispatch(cmdSetStyle(node.id, { strokeColor: val })); draw();
      }, {}, node.style && 'strokeColor' in node.style ? () => {
        dispatch({ apply(d) { const e = buildNodeMap(d.nodes).get(node.id); if (e) delete e.node.style.strokeColor; return d; }, undo(d) { return d; } });
        draw(); refreshProperties();
      } : null);
      addPropRow(body, 'Line width', 'number', effStyle.strokeWidth || 1.5, val => {
        dispatch(cmdSetStyle(node.id, { strokeWidth: parseFloat(val) || 1.5 })); draw();
      }, { min: 0.5, max: 20, step: 0.5 }, node.style && 'strokeWidth' in node.style ? () => {
        dispatch({ apply(d) { const e = buildNodeMap(d.nodes).get(node.id); if (e) delete e.node.style.strokeWidth; return d; }, undo(d) { return d; } });
        draw(); refreshProperties();
      } : null);
    }
  }

  // Meta
  renderMeta(panel, node.id, node.meta, nodeMap);
}

function createPropertiesTabs(panel, tabs, activeTab, onChange) {
  const tabBar = document.createElement('div');
  tabBar.className = 'prop-tabs';
  panel.appendChild(tabBar);
  for (const [key, label] of Object.entries(tabs)) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'prop-tab-btn';
    if (key === activeTab) btn.classList.add('active');
    btn.textContent = label;
    btn.addEventListener('click', () => onChange(key));
    tabBar.appendChild(btn);
  }
  const body = document.createElement('div');
  body.className = 'prop-tab-panel';
  panel.appendChild(body);
  return body;
}

function addToggleRow(panel, label, actions) {
  const row = document.createElement('div');
  row.className = 'prop-row';
  const lbl = document.createElement('label');
  lbl.textContent = label;
  row.appendChild(lbl);
  const group = document.createElement('div');
  group.className = 'prop-toggle-group';
  row.appendChild(group);
  for (const action of actions) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'prop-toggle-btn';
    if (action.active) btn.classList.add('active');
    if (action.inherited) btn.classList.add('inherited');
    btn.textContent = action.label;
    btn.addEventListener('click', action.onToggle);
    group.appendChild(btn);
    if (action.onClear) {
      const clearBtn = document.createElement('button');
      clearBtn.type = 'button';
      clearBtn.className = 'prop-clear-btn';
      clearBtn.textContent = '×';
      clearBtn.title = 'Reset to default';
      clearBtn.addEventListener('click', action.onClear);
      group.appendChild(clearBtn);
    }
  }
  panel.appendChild(row);
}

function addSelectRow(panel, label, value, options, onChange, onClear = null) {
  const row = document.createElement('div');
  row.className = 'prop-row';
  const lbl = document.createElement('label');
  lbl.textContent = label;
  row.appendChild(lbl);
  const select = document.createElement('select');
  for (const [optionValue, optionLabel] of options) {
    const option = document.createElement('option');
    option.value = optionValue;
    option.textContent = optionLabel;
    if (optionValue === value) option.selected = true;
    select.appendChild(option);
  }
  select.addEventListener('change', () => onChange(select.value));
  if (onClear) {
    const wrap = document.createElement('div');
    wrap.className = 'prop-input-row';
    wrap.appendChild(select);
    const clearBtn = document.createElement('button');
    clearBtn.type = 'button';
    clearBtn.className = 'prop-clear-btn';
    clearBtn.textContent = '×';
    clearBtn.title = 'Reset to default';
    clearBtn.addEventListener('click', onClear);
    wrap.appendChild(clearBtn);
    row.appendChild(wrap);
  } else {
    row.appendChild(select);
  }
  panel.appendChild(row);
  return select;
}

function renderEdgeProps(panel, edge) {
  panel.innerHTML = '';

  // Label
  addPropRow(panel, 'Label', 'text', edge.label, val => {
    dispatch(cmdSetLabel(edge.id, val)); draw();
  });

  // Arrow
  const arrowRow = document.createElement('div');
  arrowRow.className = 'prop-row';
  arrowRow.innerHTML = `<label>Arrow</label>
    <select>
      <option value="none" ${edge.arrow==='none'?'selected':''}>None</option>
      <option value="forward" ${edge.arrow==='forward'?'selected':''}>Forward →</option>
      <option value="bidirectional" ${edge.arrow==='bidirectional'?'selected':''}>Bidirectional ↔</option>
    </select>`;
  panel.appendChild(arrowRow);
  arrowRow.querySelector('select').addEventListener('change', e => {
    dispatch({ apply(d) {
      const ed = d.edges.find(x => x.id === edge.id);
      if (ed) ed.arrow = e.target.value;
      return d;
    }, undo(d) { return d; } });
    draw();
  });

  // Resolve effective edge style — falls back to shared node defaults
  const nodeDefs     = getDiagram().settings.defaultNodeStyle || {};
  const effEdgeStyle = { strokeColor: nodeDefs.strokeColor, strokeWidth: nodeDefs.strokeWidth, ...edge.style };

  // Stroke color
  addPropRow(panel, 'Stroke', 'color', effEdgeStyle.strokeColor || '#5a5a8a', val => {
    dispatch({ apply(d) {
      const ed = d.edges.find(x => x.id === edge.id);
      if (ed) { ed.style = ed.style || {}; ed.style.strokeColor = val; }
      return d;
    }, undo(d) { return d; } });
    draw();
  }, {}, edge.style && 'strokeColor' in edge.style ? () => {
    dispatch({ apply(d) { const ed = d.edges.find(x => x.id === edge.id); if (ed && ed.style) delete ed.style.strokeColor; return d; }, undo(d) { return d; } });
    draw(); refreshProperties();
  } : null);

  // Stroke width
  addPropRow(panel, 'Line width', 'number', effEdgeStyle.strokeWidth || 1.5, val => {
    dispatch({ apply(d) {
      const ed = d.edges.find(x => x.id === edge.id);
      if (ed) { ed.style = ed.style || {}; ed.style.strokeWidth = parseFloat(val) || 1.5; }
      return d;
    }, undo(d) { return d; } });
    draw();
  }, { min: 0.5, max: 20, step: 0.5 }, edge.style && 'strokeWidth' in edge.style ? () => {
    dispatch({ apply(d) { const ed = d.edges.find(x => x.id === edge.id); if (ed && ed.style) delete ed.style.strokeWidth; return d; }, undo(d) { return d; } });
    draw(); refreshProperties();
  } : null);

  // Meta
  renderMeta(panel, edge.id, edge.meta, null);
}

function addPropRow(panel, label, type, value, onChange, attrs = {}, onClear = null) {
  const row = document.createElement('div');
  row.className = 'prop-row';
  const input = document.createElement('input');
  input.type  = type;
  input.value = value ?? '';
  for (const [k, v] of Object.entries(attrs)) input.setAttribute(k, v);
  const lbl = document.createElement('label');
  lbl.textContent = label;
  row.appendChild(lbl);

  if (type === 'color') {
    input.className = 'kd-color-native';
    input.tabIndex = -1;

    const control = document.createElement('button');
    control.type = 'button';
    control.className = 'kd-color-control';
    control.innerHTML = `<span class="kd-color-preview"></span><span class="kd-color-value"></span>`;
    row.appendChild(input);

    const setControlValue = color => {
      const normalized = normalizeColor(color);
      input.value = normalized;
      control.querySelector('.kd-color-preview').style.background = normalized;
      control.querySelector('.kd-color-value').textContent = normalized;
    };

    setControlValue(input.value);
    input._kdApplyColor = color => {
      setControlValue(color);
      onChange(color);
    };

    control.addEventListener('click', () => showCustomColorPalette(input, input._kdApplyColor, control));
    control.addEventListener('keydown', e => {
      if (e.key === 'Escape') hideCustomColorPalette();
    });
    input.addEventListener('input', () => {
      setControlValue(input.value);
      onChange(input.value);
      renderCustomColorPalette();
    });
    input.addEventListener('change', () => {
      setControlValue(input.value);
      onChange(input.value);
      rememberCustomColor(input.value);
    });

    if (onClear) {
      const wrap = document.createElement('div');
      wrap.className = 'prop-input-row';
      wrap.appendChild(control);
      const clearBtn = document.createElement('button');
      clearBtn.className = 'prop-clear-btn';
      clearBtn.textContent = '×';
      clearBtn.title = 'Reset to default';
      clearBtn.addEventListener('click', () => onClear());
      wrap.appendChild(clearBtn);
      row.appendChild(wrap);
    } else {
      row.appendChild(control);
    }

    panel.appendChild(row);
    return input;
  }

  if (onClear) {
    const wrap = document.createElement('div');
    wrap.className = 'prop-input-row';
    wrap.appendChild(input);
    const clearBtn = document.createElement('button');
    clearBtn.className = 'prop-clear-btn';
    clearBtn.textContent = '×';
    clearBtn.title = 'Reset to default';
    clearBtn.addEventListener('click', () => onClear());
    wrap.appendChild(clearBtn);
    row.appendChild(wrap);
  } else {
    row.appendChild(input);
  }
  panel.appendChild(row);
  input.addEventListener('change', () => onChange(input.value));
  return input;
}

let _customColorPalette = null;
let _customColorInput = null;
let _customColorAnchor = null;
let _customColorApply = null;

function showCustomColorPalette(input, onChange, anchor = input) {
  _customColorInput = input;
  _customColorAnchor = anchor;
  _customColorApply = onChange;

  if (!_customColorPalette) {
    _customColorPalette = document.createElement('div');
    _customColorPalette.className = 'custom-color-popover';
    _customColorPalette.setAttribute('aria-label', 'Custom colors');
    document.body.appendChild(_customColorPalette);
    document.addEventListener('mousedown', onCustomColorOutsideClick, true);
  }

  renderCustomColorPalette();
  positionCustomColorPalette();
}

function renderCustomColorPalette() {
  if (!_customColorPalette || !_customColorInput) return;
  const colors = getCustomColors();
  const targetInput = _customColorInput;
  const applyColor = _customColorApply;
  _customColorPalette.innerHTML = '';

  const chooser = document.createElement('input');
  chooser.type = 'color';
  chooser.className = 'custom-color-picker';
  chooser.value = targetInput.value;
  chooser.title = 'Choose color';
  const commitChosenColor = color => {
    const normalized = normalizeColor(color);
    if (targetInput) targetInput.value = normalized;
    applyColor?.(normalized);
    return normalized;
  };
  chooser.addEventListener('input', () => {
    commitChosenColor(chooser.value);
  });
  chooser.addEventListener('change', () => {
    const normalized = commitChosenColor(chooser.value);
    rememberCustomColor(normalized);
    hideCustomColorPalette();
  });
  _customColorPalette.appendChild(chooser);

  const swatches = document.createElement('div');
  swatches.className = 'custom-color-swatches';
  _customColorPalette.appendChild(swatches);

  for (let i = 0; i < CUSTOM_COLOR_LIMIT; i++) {
    const swatch = colors[i] || null;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'custom-color-swatch';
    btn.dataset.index = String(i);
    btn.title = swatch
      ? `${swatch.color}${swatch.locked ? ' (locked)' : ''}`
      : 'Empty custom color';
    btn.setAttribute('aria-label', btn.title);

    if (swatch) {
      btn.style.setProperty('--swatch-color', swatch.color);
      btn.classList.add('occupied');
      btn.classList.toggle('locked', swatch.locked);
      btn.classList.toggle('selected', normalizeColor(targetInput?.value) === swatch.color);
      const applySwatch = () => {
        if (targetInput) targetInput.value = swatch.color;
        applyColor?.(swatch.color);
        touchCustomColor(swatch.color);
        hideCustomColorPalette();
      };
      btn.addEventListener('mouseup', e => {
        if (e.button !== 0) return;
        applySwatch();
      });
      btn.addEventListener('keydown', e => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        applySwatch();
      });
    }

    btn.addEventListener('contextmenu', e => {
      e.preventDefault();
      toggleCustomColorLock(i);
      renderCustomColorPalette();
    });

    swatches.appendChild(btn);
  }
}

function positionCustomColorPalette() {
  if (!_customColorPalette || !_customColorAnchor) return;
  const rect = _customColorAnchor.getBoundingClientRect();
  const width = _customColorPalette.offsetWidth || 196;
  const left = Math.min(window.innerWidth - width - 8, Math.max(8, rect.right - width));
  _customColorPalette.style.left = `${left}px`;
  _customColorPalette.style.top = `${rect.bottom + 4}px`;
}

function hideCustomColorPalette() {
  if (_customColorPalette) _customColorPalette.remove();
  _customColorPalette = null;
  _customColorInput = null;
  _customColorAnchor = null;
  _customColorApply = null;
  document.removeEventListener('mousedown', onCustomColorOutsideClick, true);
}

function onCustomColorOutsideClick(e) {
  if (_customColorPalette?.contains(e.target) || _customColorAnchor?.contains(e.target)) return;
  hideCustomColorPalette();
}

function getCustomColors() {
  const settings = getDiagram().settings || {};
  const colors = Array.isArray(settings.customColors)
    ? settings.customColors
    : getMigratedCustomColors();
  return sanitizeCustomColors(colors);
}

function saveCustomColors(colors) {
  const customColors = sanitizeCustomColors(colors);
  dispatch({
    apply(d) {
      d.settings ??= {};
      d.settings.customColors = customColors;
      return d;
    },
    undo(d) { return d; },
  });
}

function getMigratedCustomColors() {
  try {
    return sanitizeCustomColors(JSON.parse(localStorage.getItem(CUSTOM_COLOR_MIGRATION_KEY) || '[]'));
  } catch {
    return [];
  }
}

function sanitizeCustomColors(colors) {
  if (!Array.isArray(colors)) return [];
  return colors
    .filter(item => item && isColorHex(item.color))
    .slice(0, CUSTOM_COLOR_LIMIT)
    .map((item, index) => ({
      color: normalizeColor(item.color),
      locked: Boolean(item.locked),
      lastUsed: Number(item.lastUsed) || index,
      created: Number(item.created) || index,
    }));
}

function rememberCustomColor(color) {
  const normalized = normalizeColor(color);
  if (!isColorHex(normalized)) return;

  const now = Date.now();
  const colors = getCustomColors();
  const existing = colors.find(item => item.color === normalized);
  if (existing) {
    existing.lastUsed = now;
    saveCustomColors(colors);
    renderCustomColorPalette();
    return;
  }

  const emptyCount = CUSTOM_COLOR_LIMIT - colors.length;
  if (emptyCount > 0) {
    colors.push({ color: normalized, locked: false, created: now, lastUsed: now });
    saveCustomColors(colors);
    renderCustomColorPalette();
    return;
  }

  const replaceIndex = colors
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => !item.locked)
    .sort((a, b) => a.item.lastUsed - b.item.lastUsed || a.item.created - b.item.created)[0]?.index;

  if (replaceIndex === undefined) return;
  colors[replaceIndex] = { color: normalized, locked: false, created: now, lastUsed: now };
  saveCustomColors(colors);
  renderCustomColorPalette();
}

function touchCustomColor(color) {
  const normalized = normalizeColor(color);
  const colors = getCustomColors();
  const item = colors.find(c => c.color === normalized);
  if (!item) return;
  item.lastUsed = Date.now();
  saveCustomColors(colors);
}

function toggleCustomColorLock(index) {
  const colors = getCustomColors();
  if (!colors[index]) return;
  colors[index].locked = !colors[index].locked;
  saveCustomColors(colors);
}

function normalizeColor(color) {
  return String(color || '').trim().toLowerCase();
}

function isColorHex(color) {
  return /^#[0-9a-f]{6}$/i.test(color);
}

function renderMeta(panel, id, meta, nodeMap) {
  const heading = document.createElement('label');
  heading.style.cssText = 'font-size:11px;color:var(--text-dim);display:block;margin-top:10px;margin-bottom:4px;';
  heading.textContent = 'Meta';
  panel.appendChild(heading);

  const table = document.createElement('table');
  table.className = 'meta-table';
  panel.appendChild(table);

  const rebuildRows = () => {
    table.innerHTML = '';
    for (const [k, v] of Object.entries(meta)) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><input type="text" value="${escHtml(k)}" placeholder="key"/></td>
        <td><input type="text" value="${escHtml(String(v))}" placeholder="value"/></td>
        <td><button class="meta-del-btn" title="Remove">×</button></td>`;
      table.appendChild(tr);

      const [kIn, vIn] = tr.querySelectorAll('input');
      const delBtn     = tr.querySelector('.meta-del-btn');

      const commit = () => {
        const newKey = kIn.value.trim();
        const newVal = vIn.value;
        if (k !== newKey) {
          dispatch(cmdSetMeta(id, k, null));
          if (newKey) dispatch(cmdSetMeta(id, newKey, newVal));
          meta = getDiagram().nodes ? getMetaById(id) : meta; // refresh
        } else if (newVal !== String(v)) {
          dispatch(cmdSetMeta(id, k, newVal));
        }
      };
      kIn.addEventListener('change', commit);
      vIn.addEventListener('change', commit);
      delBtn.addEventListener('click', () => {
        dispatch(cmdSetMeta(id, k, null));
        delete meta[k];
        rebuildRows();
      });
    }
  };

  rebuildRows();

  const addBtn = document.createElement('button');
  addBtn.className = 'meta-add-btn';
  addBtn.textContent = '+ Add';
  panel.appendChild(addBtn);
  addBtn.addEventListener('click', () => {
    const k = `key${Object.keys(meta).length + 1}`;
    meta[k] = '';
    dispatch(cmdSetMeta(id, k, ''));
    rebuildRows();
  });
}

function getMetaById(id) {
  const nm = getNodeMap();
  if (nm.has(id)) return nm.get(id).node.meta;
  const edge = getDiagram().edges.find(e => e.id === id);
  return edge ? edge.meta : {};
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Hierarchy panel ────────────────────────────────────────────────────────
let _hierarchyDragId = null;

/**
 * Wire tree-level drop zone (promote to root). Call once on init.
 */
export function initHierarchy() {
  const tree = document.getElementById('hierarchy-tree');

  tree.addEventListener('dragover', e => {
    if (!_hierarchyDragId) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    tree.classList.add('drag-over-root');
  });

  tree.addEventListener('dragleave', e => {
    if (!tree.contains(e.relatedTarget)) tree.classList.remove('drag-over-root');
  });

  tree.addEventListener('drop', e => {
    tree.classList.remove('drag-over-root');
    const srcId = _hierarchyDragId;
    _hierarchyDragId = null;
    if (!srcId) return;
    if (e.target.closest('.h-node')) return; // row handles its own drop
    reparentNode(srcId, null);
  });
}
export function refreshHierarchy() {
  const tree = document.getElementById('hierarchy-tree');
  const diagram = getDiagram();
  tree.innerHTML = '';
  renderHierarchyNodes(tree, diagram.nodes);
}

function renderHierarchyNodes(parent, nodes) {
  for (const node of nodes) {
    if (node.type === 'waypoint') continue;

    const li = document.createElement('li');
    const row = document.createElement('div');
    row.className = 'h-node' + (selection.has(node.id) ? ' selected' : '');
    row.dataset.id = node.id;
    row.draggable = true;

    const icon = document.createElement('span');
    icon.className = 'h-icon';
    icon.textContent = node.type === 'ellipse' ? '○' : '□';

    const name = document.createElement('span');
    name.textContent = node.label || '(unlabelled)';
    name.style.overflow = 'hidden';
    name.style.textOverflow = 'ellipsis';

    row.appendChild(icon);
    row.appendChild(name);
    li.appendChild(row);

    // Click to select
    row.addEventListener('click', () => {
      selection.clear();
      selection.add(node.id);
      draw();
      refreshProperties();
      refreshHierarchy();
      document.dispatchEvent(new CustomEvent('kd:scroll-to', { detail: node.id }));
    });

    // Drag-and-drop reparenting
    row.addEventListener('dragstart', e => {
      _hierarchyDragId = node.id;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', node.id);
    });

    row.addEventListener('dragend', () => {
      _hierarchyDragId = null;
      document.querySelectorAll('.h-node.drag-over').forEach(el => el.classList.remove('drag-over'));
    });

    row.addEventListener('dragover', e => {
      if (_hierarchyDragId === node.id) return;
      e.preventDefault();
      e.stopPropagation(); // prevent tree root zone from firing
      e.dataTransfer.dropEffect = 'move';
      document.querySelectorAll('.h-node.drag-over').forEach(el => el.classList.remove('drag-over'));
      row.classList.add('drag-over');
    });

    row.addEventListener('dragleave', e => {
      if (!row.contains(e.relatedTarget)) row.classList.remove('drag-over');
    });

    row.addEventListener('drop', e => {
      e.preventDefault();
      e.stopPropagation();
      row.classList.remove('drag-over');
      const srcId = _hierarchyDragId;
      _hierarchyDragId = null;
      if (!srcId || srcId === node.id) return;
      // Prevent dropping onto own descendant
      const nm = getNodeMap();
      let entry = nm.get(node.id);
      while (entry) {
        if (entry.parentId === srcId) return;
        entry = nm.get(entry.parentId);
      }
      reparentNode(srcId, node.id);
    });

    if (node.children?.length) {
      const ul = document.createElement('ul');
      ul.className = 'h-children';
      renderHierarchyNodes(ul, node.children);
      li.appendChild(ul);
    }

    parent.appendChild(li);
  }
}

// ── Title bar update ───────────────────────────────────────────────────────

export function refreshTitle() {
  const d = getDiagram();
  const dirty = isDirty();
  document.getElementById('diag-title').textContent = d.title;
  document.getElementById('diag-dirty').classList.toggle('hidden', !dirty);
  document.title = (dirty ? '● ' : '') + d.title + ' — KoreDiag';
}
