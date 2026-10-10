import { initServiceShell } from '/ui-elements/assets/js/chrome.js';
import { initWorkspaceLayouts } from '/ui-elements/assets/js/workspace.js';
import { stepNumberInput } from '/ui-elements/assets/js/steppers.js';

const state = { network: null, selectedNodeId: null, pendingOutput: null, run: null, edits: {}, dragging: null };
const $ = (selector) => document.querySelector(selector);
const graphStage = $('#graph-stage');
const nodeLayer  = $('#nodes');
const edgeLayer  = $('#edges');
const TEMPLATE_RE = /\{([A-Za-z0-9_-]+)\}/g;
const BLOCK_HELP = {
  python: 'Available: inputs, outputs, NoValue (None), top-level return to stop early, api_get, api_post, trigger(name, payload) (run the linked block now and wait; returns {ok, error, outputs}), disable_auto_trigger(name=None) (stop trigger links firing when this block finishes), llm, llm_result, decide, judge, json, math, re. Safe stdlib imports (datetime, time, collections, itertools, csv, random, hashlib, base64, ...). Files (cwd and root = datauser folder): open, read_text, write_text, append_text, read_json, write_json, exists, list_files, delete_file.',
  llm: 'Use {input_name} placeholders in the prompt. Inputs are merged into the prompt, and this block can output response, prompt, model, prompt_tokens, completion_tokens, and tokens_per_second.',
  judge: 'Use {input_name} placeholders in the question. All inputs are sent to System One as the decision state, and this block can output verdict, probability, threshold, question, and model.',
};

initServiceShell({
  currentService: 'koreagentnetwork',
  urls:           window.__koreSuiteUrls || {},
  path:           window.location.pathname,
  section:        'network',
  shellMeta:      { network: { brandLabel: 'KoreAgentNetwork', overline: 'Visual Processing', brandIcon: 'koreagent' } },
  shellTabs:      [{ key: 'network', label: 'Network editor', href: '/ui' }],
});

function id() { return `node_${crypto.randomUUID().replaceAll('-', '').slice(0, 10)}`; }
function escapeHtml(value) { const el = document.createElement('span'); el.textContent = String(value); return el.innerHTML; }
function currentNode() { return state.network?.nodes.find((node) => node.id === state.selectedNodeId) || null; }
function nodeHeight(node) { return 69 + Math.max(node.inputs.length, node.outputs.length, 1) * 20; }
// Centre a new node in the visible part of the diagram, nudging it clear of any node already there.
function placeInView(node) {
  let x = Math.round((graphStage.scrollLeft + graphStage.clientWidth / 2) / zoom - 110);
  let y = Math.round((graphStage.scrollTop + graphStage.clientHeight / 2) / zoom - nodeHeight(node) / 2);
  x = Math.max(10, x); y = Math.max(10, y);
  while (state.network.nodes.some((n) => Math.abs(n.position.x - x) < 20 && Math.abs(n.position.y - y) < 20)) { x += 30; y += 30; }
  node.position = { x, y };
}
function defaultNode(kind = 'python', index = 0) {
  const nodeId = id();
  const base = {
    id: nodeId,
    kind,
    config: {},
    position: { x: 60 + (index % 4) * 280, y: 70 + Math.floor(index / 4) * 230 },
  };
  if (kind === 'llm') {
    return { ...base, label: 'Prompt model', inputs: [{ name: 'prompt' }], outputs: [{ name: 'response' }], code: '', config: { prompt_template: '{prompt}', model: '' } };
  }
  if (kind === 'judge') {
    return {
      ...base,
      label: 'Decide response',
      inputs: [{ name: 'prompt' }, { name: 'response' }],
      outputs: [{ name: 'verdict' }, { name: 'probability' }],
      code: '',
      config: { question: 'Does {response} directly and sensibly answer {prompt}?', threshold: 0.7, model: '' },
    };
  }
  return { ...base, label: 'New block', inputs: [{ name: 'value' }], outputs: [{ name: 'result' }], code: "outputs['result'] = inputs['value']" };
}
function ensureNodeShape(node) {
  node.kind = ['python', 'llm', 'judge'].includes(node.kind) ? node.kind : 'python';
  node.config = node.config && typeof node.config === 'object' ? node.config : {};
  if (!Array.isArray(node.inputs)) node.inputs = [];
  if (!Array.isArray(node.outputs)) node.outputs = [];
  if (typeof node.code !== 'string') node.code = '';
  if (node.kind === 'llm') {
    node.config.prompt_template = typeof node.config.prompt_template === 'string' ? node.config.prompt_template : '{prompt}';
    node.config.model = typeof node.config.model === 'string' ? node.config.model : '';
    if (!node.outputs.some((port) => port.name === 'response')) node.outputs.unshift({ name: 'response' });
  } else if (node.kind === 'judge') {
    node.config.question = typeof node.config.question === 'string' ? node.config.question : 'Does {response} directly and sensibly answer {prompt}?';
    node.config.threshold = Number.isFinite(Number(node.config.threshold)) ? Number(node.config.threshold) : 0.7;
    node.config.model = typeof node.config.model === 'string' ? node.config.model : '';
    ['prompt', 'response'].forEach((name) => { if (!node.inputs.some((port) => port.name === name)) node.inputs.push({ name }); });
    ['verdict', 'probability'].forEach((name) => { if (!node.outputs.some((port) => port.name === name)) node.outputs.push({ name }); });
  } else {
    node.config = {};
  }
  return node;
}
function mergeTemplateInputs(node, template, baseNames = []) {
  const names = [...new Set([...baseNames, ...Array.from(String(template || '').matchAll(TEMPLATE_RE), (match) => match[1])])];
  const existing = new Set(node.inputs.map((port) => port.name));
  names.forEach((name) => { if (!existing.has(name)) node.inputs.push({ name }); });
}
function showKindFields(kind) {
  $('#field-python').hidden = kind !== 'python';
  $('#field-llm').hidden = kind !== 'llm';
  $('#field-judge').hidden = kind !== 'judge';
  $('#node-help').textContent = BLOCK_HELP[kind] || '';
}
function portPosition(node, portName, output) {
  const ports = output ? node.outputs : node.inputs;
  const index = ports.findIndex((port) => port.name === portName);
  return { x: node.position.x + (output ? 220 : 0), y: node.position.y + 45 + Math.max(index, 0) * 20 };
}

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

async function loadList() {
  const { networks } = await request('/api/networks');
  state.networkList = networks;
  const list = $('#network-list');
  list.innerHTML = networks.map((network) => {
    const active = network.id === state.network?.id;
    const name = active ? `<input class="kan-network-name" aria-label="Network name" value="${escapeHtml(state.network.title)}">` : `<strong>${escapeHtml(network.title)}</strong>`;
    return `<div class="kan-network-item ${active ? 'active' : ''}" data-id="${escapeHtml(network.id)}" role="button" tabindex="0">${name}<small>${network.node_count} blocks</small></div>`;
  }).join('');
  list.querySelectorAll('[data-id]').forEach((item) => item.addEventListener('click', () => { if (item.dataset.id !== state.network?.id) loadNetwork(item.dataset.id); }));
  list.querySelector('.kan-network-name')?.addEventListener('input', (event) => { state.network.title = event.target.value; $('#network-title').value = event.target.value; });
  list.querySelector('.kan-network-name')?.addEventListener('change', () => persist({ refreshList: true }));
  return networks;
}

async function loadNetwork(networkId) {
  const { network } = await request(`/api/networks/${encodeURIComponent(networkId)}`);
  network.nodes.forEach(ensureNodeShape);
  state.network        = network;
  try { localStorage.setItem('kan.lastNetwork', network.id); } catch { /* storage unavailable */ }
  state.selectedNodeId = null;
  state.pendingOutput  = null;
  state.run            = null;
  state.edits          = {};
  try {
    const saved = await request(`/api/networks/${encodeURIComponent(networkId)}/state`);
    state.run   = saved.run;
    state.edits = saved.edits || {};
  } catch (error) { setStatus(error.message, 'error'); }
  $('#network-title').value = network.title;
  recentre({ revealContent: true });
  await loadList();
}

function render({ inspector = true } = {}) {
  const network = state.network;
  if (!network) return;
  network.nodes.forEach(ensureNodeShape);
  $('#network-title').value = network.title;
  $('#canvas-hint').hidden  = network.nodes.length > 0;
  nodeLayer.innerHTML = '';
  edgeLayer.innerHTML = '';
  network.nodes.forEach(renderNode);
  drawEdges();
  fitLayers();
  if (inspector) renderInspector();
  renderData();
}

function dotCenter(node, portName, output) {
  const article = nodeLayer.querySelector(`[data-node-id="${CSS.escape(node.id)}"]`);
  const port    = article?.querySelector(`.kan-port--${output ? 'output' : 'input'}[data-port="${CSS.escape(portName)}"] .kan-port-dot`);
  if (!port) return portPosition(node, portName, output);
  const origin = nodeLayer.getBoundingClientRect();
  const box    = port.getBoundingClientRect();
  return { x: (box.left + box.width / 2 - origin.left) / zoom, y: (box.top + box.height / 2 - origin.top) / zoom };
}

function nodeBox(node) {
  const article = nodeLayer.querySelector(`[data-node-id="${CSS.escape(node.id)}"]`);
  if (!article) return { id: node.id, left: node.position.x, right: node.position.x + 260, top: node.position.y, bottom: node.position.y + 120 };
  const origin = nodeLayer.getBoundingClientRect();
  const box    = article.getBoundingClientRect();
  return { id: node.id, left: (box.left - origin.left) / zoom, right: (box.right - origin.left) / zoom, top: (box.top - origin.top) / zoom, bottom: (box.bottom - origin.top) / zoom };
}

const WIRE_MARGIN = 8;
const WIRE_STUB = 26;
const WIRE_PITCH = 14;

function segmentHitsBox(a, b, box) {
  const m = WIRE_MARGIN;
  if (Math.abs(a.y - b.y) < 0.01) return a.y > box.top - m && a.y < box.bottom + m && Math.max(a.x, b.x) > box.left - m && Math.min(a.x, b.x) < box.right + m;
  return a.x > box.left - m && a.x < box.right + m && Math.max(a.y, b.y) > box.top - m && Math.min(a.y, b.y) < box.bottom + m;
}

function simplifyPoints(points) {
  const out = [];
  points.forEach((point) => {
    if (out.length && Math.abs(out.at(-1).x - point.x) < 0.01 && Math.abs(out.at(-1).y - point.y) < 0.01) return;
    out.push(point);
  });
  for (let i = out.length - 2; i > 0; i -= 1) {
    const p = out[i - 1], q = out[i], r = out[i + 1];
    if ((Math.abs(p.x - q.x) < 0.01 && Math.abs(q.x - r.x) < 0.01) || (Math.abs(p.y - q.y) < 0.01 && Math.abs(q.y - r.y) < 0.01)) out.splice(i, 1);
  }
  return out;
}

// Parallel runs of different wire kinds, or of different sources, must not share a lane.
function laneConflict(a, b, wire, used) {
  const horizontal = Math.abs(a.y - b.y) < 0.01;
  const pos = horizontal ? a.y : a.x;
  const lo = horizontal ? Math.min(a.x, b.x) : Math.min(a.y, b.y), hi = horizontal ? Math.max(a.x, b.x) : Math.max(a.y, b.y);
  let cost = 0;
  used.forEach((run) => {
    if (run.horizontal !== horizontal || Math.abs(run.pos - pos) >= WIRE_MARGIN - 1 || run.hi <= lo || run.lo >= hi) return;
    if (run.kind !== wire.kind) cost += 5000;
    else if (run.key !== wire.key) cost += 60;
  });
  return cost;
}

// Circuit-board route: horizontal out of the dot, then the cheapest orthogonal path that stays clear of every block and of other lanes.
function routePoints(from, to, sourceId, targetId, boxes, wire, used) {
  const xsList = [], xtList = [], ysSet = new Set();
  for (let k = 0; k < 10; k += 1) { xsList.push(from.x + WIRE_STUB + k * WIRE_PITCH); xtList.push(to.x - WIRE_STUB - k * WIRE_PITCH); }
  // Wires from the same output share one exit lane so they leave as a single trunk.
  const trunk = wire.trunks.get(wire.key);
  if (trunk !== undefined) xsList.splice(0, xsList.length, trunk);
  const sorted = boxes.slice().sort((m, n) => m.top - n.top);
  boxes.forEach((box) => { ysSet.add(box.top - 14); ysSet.add(box.bottom + 14); });
  sorted.forEach((box, i) => { if (sorted[i + 1] && sorted[i + 1].top - box.bottom > 2 * WIRE_MARGIN + 6) ysSet.add((box.bottom + sorted[i + 1].top) / 2); });
  const ys = [...ysSet];
  const candidates = [];
  for (let x = from.x + WIRE_STUB; x <= to.x - WIRE_STUB; x += WIRE_PITCH) if (trunk === undefined || x === trunk) candidates.push([from, { x, y: from.y }, { x, y: to.y }, to]);
  xsList.forEach((xs) => xtList.forEach((xt) => ys.filter((y) => y !== from.y && y !== to.y).forEach((y) => candidates.push([from, { x: xs, y: from.y }, { x: xs, y }, { x: xt, y }, { x: xt, y: to.y }, to]))));
  let best = null, bestCost = Infinity;
  candidates.forEach((raw) => {
    const points = simplifyPoints(raw);
    let cost = (points.length - 2) * 20;
    for (let i = 0; i < points.length - 1 && cost < bestCost; i += 1) {
      const a = points[i], b = points[i + 1];
      cost += Math.abs(b.x - a.x) + Math.abs(b.y - a.y);
      boxes.forEach((box) => {
        const own = (i === 0 && box.id === sourceId) || (i === points.length - 2 && box.id === targetId);
        if (!own && segmentHitsBox(a, b, box)) cost += 10000;
      });
      cost += laneConflict(a, b, wire, used);
    }
    if (cost < bestCost) { bestCost = cost; best = points; }
  });
  if (best.length > 2 && !wire.trunks.has(wire.key)) wire.trunks.set(wire.key, best[1].x);
  for (let i = 0; i < best.length - 1; i += 1) {
    const a = best[i], b = best[i + 1], horizontal = Math.abs(a.y - b.y) < 0.01;
    used.push({ horizontal, pos: horizontal ? a.y : a.x, lo: horizontal ? Math.min(a.x, b.x) : Math.min(a.y, b.y), hi: horizontal ? Math.max(a.x, b.x) : Math.max(a.y, b.y), kind: wire.kind, key: wire.key });
  }
  return best;
}

// Rounded corners, plus a small hop wherever a horizontal run crosses a vertical run of an unrelated wire.
let hopGaps = '';
function roundedPath(points, crossings = [], radius = 5, hop = 7) {
  hopGaps = '';
  const trim = (from, to, amount) => {
    const length = Math.hypot(to.x - from.x, to.y - from.y) || 1;
    return { x: from.x + ((to.x - from.x) / length) * amount, y: from.y + ((to.y - from.y) / length) * amount };
  };
  const length = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);
  let d = `M ${points[0].x} ${points[0].y}`;
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i], b = points[i + 1];
    const r0 = i > 0 ? Math.min(radius, length(a, b) / 2, length(points[i - 1], a) / 2) : 0;
    const r1 = i < points.length - 2 ? Math.min(radius, length(a, b) / 2, length(b, points[i + 2]) / 2) : 0;
    const start = r0 ? trim(a, b, r0) : a, end = r1 ? trim(b, a, r1) : b;
    if (i > 0) d += ` Q ${a.x} ${a.y} ${start.x} ${start.y}`;
    if (Math.abs(a.y - b.y) < 0.01) {
      const dir = Math.sign(b.x - a.x) || 1;
      const xs = crossings.filter((c) => c.y1 < a.y && c.y2 > a.y && (c.x - start.x) * dir > hop && (end.x - c.x) * dir > hop).map((c) => c.x).sort((m, n) => (m - n) * dir).filter((x, k, all) => k === 0 || Math.abs(x - all[k - 1]) > hop * 2);
      xs.forEach((x) => { hopGaps += ` M ${x} ${a.y - hop - 2.5} L ${x} ${a.y - hop + 2.5}`; d += ` L ${x - dir * hop} ${a.y} A ${hop} ${hop} 0 0 ${dir > 0 ? 1 : 0} ${x + dir * hop} ${a.y}`; });
    }
    d += ` L ${end.x} ${end.y}`;
  }
  return d;
}

function verticalRuns(points, key) {
  const runs = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i], b = points[i + 1];
    if (Math.abs(a.x - b.x) < 0.01 && Math.abs(a.y - b.y) > 0.01) runs.push({ key, x: a.x, y1: Math.min(a.y, b.y), y2: Math.max(a.y, b.y) });
  }
  return runs;
}

function chevronPath(points) {
  let d = '';
  for (let i = 0; i < points.length - 1; i += 1) {
    const a = points[i], b = points[i + 1], length = Math.hypot(b.x - a.x, b.y - a.y);
    if (length < 4) continue;
    const ux = (b.x - a.x) / length, uy = (b.y - a.y) / length, nx = -uy, ny = ux;
    for (let at = 6; at <= length - 3; at += 9) {
      const cx = a.x + ux * at, cy = a.y + uy * at;
      d += `M ${cx - ux * 2.5 + nx * 2.5} ${cy - uy * 2.5 + ny * 2.5} L ${cx + ux * 2} ${cy + uy * 2} L ${cx - ux * 2.5 - nx * 2.5} ${cy - uy * 2.5 - ny * 2.5} `;
    }
  }
  return d;
}

// 32 muted tones of similar brightness; stepping by 11 keeps consecutive picks far apart in hue.
const WIRE_PALETTE = Array.from({ length: 32 }, (_, i) => `hsl(${(((i * 11) % 32) * 11.25 + 5).toFixed(1)}, 38%, 62%)`);

function drawEdges() {
  const wires = [];
  const used = [];
  const trunks = new Map();
  const boxes = state.network.nodes.map(nodeBox);
  (state.network.triggers || []).forEach((trigger) => {
    const source = state.network.nodes.find((node) => node.id === trigger.source_node);
    const target = state.network.nodes.find((node) => node.id === trigger.target_node);
    if (!source || !target) return;
    const key = `${source.id}|${TRIGGER_PORT}`;
    const points = routePoints(dotCenter(source, TRIGGER_PORT, true), dotCenter(target, TRIGGER_PORT, false), source.id, target.id, boxes, { kind: 'trigger', key, trunks }, used);
    wires.push({ key, points, trigger, title: `Trigger ${trigger.name}: ${source.label} → ${target.label}` });
  });
  state.network.edges.forEach((edge) => {
    const fromNode = state.network.nodes.find((node) => node.id === edge.source_node);
    const toNode   = state.network.nodes.find((node) => node.id === edge.target_node);
    if (!fromNode || !toNode) return;
    const key = `${fromNode.id}|${edge.source_port}`;
    const points = routePoints(dotCenter(fromNode, edge.source_port, true), dotCenter(toNode, edge.target_port, false), fromNode.id, toNode.id, boxes, { kind: 'data', key, trunks }, used);
    wires.push({ key, points, edge, title: `${fromNode.label}.${edge.source_port} → ${toNode.label}.${edge.target_port}` });
  });
  const hopWires = [];
  const wireColors = new Map();
  const colorFor = (key) => { if (!wireColors.has(key)) wireColors.set(key, WIRE_PALETTE[wireColors.size % WIRE_PALETTE.length]); return wireColors.get(key); };
  const runs = wires.filter((wire) => !wire.trigger).flatMap((wire) => verticalRuns(wire.points, wire.key));
  wires.forEach((wire) => {
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('title', wire.title);
    if (wire.trigger) {
      path.setAttribute('d', chevronPath(wire.points));
      path.classList.add('kan-edge-trigger');
    } else {
      path.setAttribute('d', roundedPath(wire.points, runs.filter((run) => run.key !== wire.key)));
      path.style.stroke = colorFor(wire.key);
      if (hopGaps) hopWires.push({ path, gaps: hopGaps });
      if (state.run?.nodes?.[wire.edge.source_node]?.status === 'completed') path.classList.add('kan-edge-live');
    }
    edgeLayer.append(path);
  });
  // Break the crossed wire under each hop so the hopping wire visibly passes over it.
  hopWires.forEach(({ path, gaps }) => {
    const gap = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    gap.setAttribute('d', gaps);
    gap.classList.add('kan-edge-gap');
    edgeLayer.append(gap, path.cloneNode());
    path.remove();
  });
}

function preview(value) {
  const text = value === undefined ? '—' : typeof value === 'string' ? value : JSON.stringify(value);
  const flat = String(text).replace(/\s+/g, ' ');
  return flat.length > 40 ? `${flat.slice(0, 40)}…` : flat;
}

function ioPreview(node, result) {
  if (!result) return '';
  const inputs = resolveInputs(node);
  const outputs = outputValues(node.id);
  const row = (dir, port, value) => `<div class="kan-io-row kan-io-row--${dir}" title="${escapeHtml(pretty(value))}"><b>${dir === 'in' ? '&rarr;' : '&larr;'} ${escapeHtml(port.name)}</b><span>${escapeHtml(preview(value))}</span></div>`;
  const error = result.error ? `<div class="kan-io-error">${escapeHtml(preview(result.error))}</div>` : '';
  return `<div class="kan-io">${node.inputs.map((port) => row('in', port, inputs[port.name])).join('')}${node.outputs.map((port) => row('out', port, outputs[port.name])).join('')}${error}</div>`;
}

function portValue(value, result) {
  if (!result) return '';
  const text = preview(value);
  return `<em class="${text.length > 6 ? 'long' : ''}" title="${escapeHtml(pretty(value))}">${escapeHtml(text)}</em>`;
}

const TRIGGER_PORT = '⚡';
function triggerPort(node, output) {
  const pending = output && state.pendingOutput?.nodeId === node.id && state.pendingOutput?.portName === TRIGGER_PORT;
  const title = output ? 'Click, then click another block\u2019s left trigger dot to link it' : 'This block runs when the block linked here finishes or calls trigger()';
  return `<button type="button" class="kan-port kan-port--trigger kan-port--${output ? 'output' : 'input'} ${pending ? 'pending' : ''}" data-port="${TRIGGER_PORT}" title="${title}"><i class="kan-port-dot"></i></button>`;
}
function triggerRow(node) { return `<div class="kan-trigger-bar">${triggerPort(node, false)}<span class="kan-trigger-label">TRIGGER</span>${triggerPort(node, true)}</div>`; }
function renderNode(node) {
  const result = state.run?.nodes?.[node.id];
  const element = document.createElement('article');
  element.className = `kan-node ${node.id === state.selectedNodeId ? 'selected' : ''} ${result?.status || ''} ${node.id === state.activeNodeId ? 'active' : ''} kan-node--${node.kind || 'python'}`;
  const seq = triggeredBlocks().has(node.id) ? '⚡' : node.order ?? executionOrder().indexOf(node.id) + 1;
  const inValues = result ? resolveInputs(node) : {};
  const outValues = result ? outputValues(node.id) : {};
  element.dataset.nodeId = node.id;
  element.style.left = `${node.position.x}px`;
  element.style.top  = `${node.position.y}px`;
  element.style.minHeight = `${nodeHeight(node)}px`;
  element.innerHTML = `<div class="kan-node-head"><button type="button" class="kcui-icon-button kan-node-play" title="Run this block with its current inputs">&#9654;</button><span class="kan-node-seq" title="Execution order">${seq || '-'}</span><span>${escapeHtml(node.label)}</span></div>${triggerRow(node)}<div class="kan-port-lines"><div class="kan-port-column">${node.inputs.map((port) => `<button type="button" class="kan-port kan-port--input" data-port="${escapeHtml(port.name)}"><i class="kan-port-dot"></i><span class="kan-port-text">${escapeHtml(port.name)}${portValue(inValues[port.name], result)}</span></button>`).join('')}</div><div class="kan-port-column">${node.outputs.map((port) => `<button type="button" class="kan-port kan-port--output ${state.pendingOutput?.nodeId === node.id && state.pendingOutput?.portName === port.name ? 'pending' : ''}" data-port="${escapeHtml(port.name)}"><span class="kan-port-text">${escapeHtml(port.name)}${portValue(outValues[port.name], result)}</span><i class="kan-port-dot"></i></button>`).join('')}</div></div>${result?.error ? `<div class="kan-io-error">${escapeHtml(preview(result.error))}</div>` : ''}`;
  const play = element.querySelector('.kan-node-play');
  play.addEventListener('pointerdown', (event) => event.stopPropagation());
  play.addEventListener('click', (event) => { event.stopPropagation(); state.selectedNodeId = node.id; playNode(node.id); });
  element.querySelector('.kan-node-head').addEventListener('pointerdown', (event) => { if (!event.target.closest('.kan-node-play')) beginDrag(event, node); });
  element.addEventListener('pointerdown', (event) => { if (event.button === 0 && !event.target.closest('.kan-port, .kan-node-play') && state.selectedNodeId !== node.id) selectNode(node.id); });
  element.addEventListener('click', (event) => { if (!event.target.closest('.kan-port')) selectNode(node.id); });
  element.querySelectorAll('.kan-port--output').forEach((port) => port.addEventListener('click', (event) => { event.stopPropagation(); state.pendingOutput = { nodeId: node.id, portName: port.dataset.port }; render(); }));
  element.querySelectorAll('.kan-port--input').forEach((port) => port.addEventListener('click', (event) => { event.stopPropagation(); connectInput(node.id, port.dataset.port); }));
  nodeLayer.append(element);
}

function showTab(name) {
  document.querySelectorAll('.kan-tab').forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === name));
  $('#tab-networks').hidden = name !== 'networks';
  $('#tab-nodes').hidden = name !== 'nodes';
  $('#tab-templates').hidden = name !== 'templates';
  if (name === 'templates') loadTemplates();
}
document.querySelectorAll('.kan-tab').forEach((tab) => tab.addEventListener('click', () => showTab(tab.dataset.tab)));
let templates = [];
async function loadTemplates() {
  try { templates = (await request('/api/templates')).templates; } catch (error) { setStatus(error.message, 'error'); return; }
  $('#template-list').innerHTML = templates.map((item) => `<div class="kan-template-item" data-id="${escapeHtml(item.id)}"><div class="kan-template-text"><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.description || '')}</small></div><button type="button" class="btn btn-secondary" data-add title="Add a copy to the current network">Add</button><button type="button" class="kcui-icon-button" data-del title="Remove this template">×</button></div>`).join('') || '<div class="kan-empty-inspector">No templates yet. Select a block and use “Add To Templates”.</div>';
}
function addFromTemplate(item) {
  if (!state.network) return;
  const node = ensureNodeShape({ ...structuredClone(item.node), id: id() });
  placeInView(node);
  state.network.nodes.push(node);
  recentre();
  persist();
  selectNode(node.id);
  setStatus(`Added ${node.label} from templates.`, 'ok');
}
$('#template-list').addEventListener('click', async (event) => {
  const row  = event.target.closest('.kan-template-item');
  const item = templates.find((t) => t.id === row?.dataset.id);
  if (!item) return;
  if (event.target.closest('[data-add]')) addFromTemplate(item);
  else if (event.target.closest('[data-del]')) {
    if (!window.confirm(`Remove template “${item.name}”?`)) return;
    try { await request(`/api/templates/${encodeURIComponent(item.id)}`, { method: 'DELETE' }); await loadTemplates(); } catch (error) { setStatus(error.message, 'error'); }
  }
});
$('#add-template').addEventListener('click', async () => {
  const node = currentNode();
  if (!node) return;
  try { await request('/api/templates', { method: 'POST', body: JSON.stringify({ node, name: node.label }) }); setStatus(`Saved “${node.label}” to Templates.`, 'ok'); await loadTemplates(); } catch (error) { setStatus(error.message, 'error'); }
});
function selectNode(nodeId) { state.selectedNodeId = nodeId; render(); showTab('nodes'); }
// Drop edges whose ports no longer exist, and keep a single connection per input
function pruneEdges() {
  const network = state.network;
  const seen = new Set();
  const before = network.edges.length;
  network.edges = network.edges.filter((edge) => {
    const from = network.nodes.find((node) => node.id === edge.source_node);
    const to   = network.nodes.find((node) => node.id === edge.target_node);
    if (!from?.outputs.some((port) => port.name === edge.source_port) || !to?.inputs.some((port) => port.name === edge.target_port)) return false;
    const key = `${edge.target_node}\u0000${edge.target_port}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (network.edges.length !== before) render({ inspector: false });
}

function connectTrigger(sourceId, targetId) {
  const triggers = (state.network.triggers ||= []);
  const existing = triggers.find((item) => item.source_node === sourceId && item.target_node === targetId);
  state.pendingOutput = null;
  if (existing) {
    state.network.triggers = triggers.filter((item) => item !== existing);
    setStatus('Trigger cleared.', 'ok');
  } else {
    const target = state.network.nodes.find((node) => node.id === targetId);
    const base = (target.label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'trigger');
    let name = base, count = 2;
    while (triggers.some((item) => item.source_node === sourceId && item.name === name)) name = `${base}_${count++}`;
    triggers.push({ name, source_node: sourceId, target_node: targetId });
    setStatus(`Trigger linked. Call trigger('${name}') from the block code.`, 'ok');
      }
  render();
  persist();
}

function connectInput(targetNode, targetPort) {
  if (!state.pendingOutput) { setStatus('Choose an output first.', 'error'); return; }
  const source = state.pendingOutput;
  if (source.nodeId === targetNode) { setStatus('A block cannot feed itself.', 'error'); return; }
  if ((source.portName === TRIGGER_PORT) !== (targetPort === TRIGGER_PORT)) { setStatus('Link trigger dots to trigger dots, and data ports to data ports.', 'error'); return; }
  if (targetPort === TRIGGER_PORT) { connectTrigger(source.nodeId, targetNode); return; }
  const existing = state.network.edges.find((edge) => edge.target_node === targetNode && edge.target_port === targetPort);
  const same     = existing && existing.source_node === source.nodeId && existing.source_port === source.portName;
  state.network.edges = state.network.edges.filter((edge) => edge !== existing);
  if (same) {
    state.pendingOutput = null;
    render();
    persist();
    setStatus('Connection cleared.', 'ok');
    return;
  }
  state.network.edges.push({ id: `edge_${crypto.randomUUID().replaceAll('-', '').slice(0, 10)}`, source_node: source.nodeId, source_port: source.portName, target_node: targetNode, target_port: targetPort });
  state.pendingOutput = null;
  render();
  persist();
  setStatus('Ports connected.', 'ok');
}

let zoom = 1;
const zoomLabel = document.createElement('div');
zoomLabel.className = 'kan-zoom-label';
$('.kan-workspace').append(zoomLabel);
// Size the layers to cover every node (plus a margin) so scrolling always reaches them at any zoom.
const CANVAS_MARGIN = 1000;
function fitLayers() {
  let right = 0, bottom = 0;
  nodeLayer.querySelectorAll('[data-node-id]').forEach((el) => {
    right  = Math.max(right, el.offsetLeft + el.offsetWidth);
    bottom = Math.max(bottom, el.offsetTop + el.offsetHeight);
  });
  const width  = Math.max(graphStage.clientWidth / zoom, right + CANVAS_MARGIN);
  const height = Math.max(graphStage.clientHeight / zoom, bottom + CANVAS_MARGIN);
  for (const layer of [nodeLayer, edgeLayer]) {
    layer.style.minWidth = layer.style.width = `${width}px`;
    layer.style.minHeight = layer.style.height = `${height}px`;
  }
  // The browser does not reliably refresh scroll extents after a transform-only change, so a layout-positioned sizer defines the scrollable area.
  if (!fitLayers.sizer) {
    fitLayers.sizer = document.createElement('div');
    fitLayers.sizer.style.cssText = 'position:absolute;width:1px;height:1px;visibility:hidden;pointer-events:none';
    graphStage.append(fitLayers.sizer);
  }
  fitLayers.sizer.style.left = `${Math.round(width * zoom) - 1}px`;
  fitLayers.sizer.style.top  = `${Math.round(height * zoom) - 1}px`;
}
// Keep a CANVAS_MARGIN border on every side of the blocks' bounding box by shifting all blocks, and the scroll with them so nothing moves on screen.
function recentre({ revealContent = false } = {}) {
  const nodes = state.network?.nodes || [];
  if (!nodes.length) { render(); return; }
  const dx = CANVAS_MARGIN - Math.min(...nodes.map((n) => n.position.x));
  const dy = CANVAS_MARGIN - Math.min(...nodes.map((n) => n.position.y));
  if (dx) nodes.forEach((n) => { n.position.x += dx; });
  if (dy) nodes.forEach((n) => { n.position.y += dy; });
  render();
  if (revealContent) {
    graphStage.scrollLeft = (CANVAS_MARGIN - 40) * zoom;
    graphStage.scrollTop  = (CANVAS_MARGIN - 40) * zoom;
  } else {
    graphStage.scrollLeft += dx * zoom;
    graphStage.scrollTop  += dy * zoom;
  }
}
function applyZoom() {
  nodeLayer.style.transform = edgeLayer.style.transform = `scale(${zoom})`;
  graphStage.style.backgroundSize = `${20 * zoom}px ${20 * zoom}px`;
  zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
  fitLayers();
}
applyZoom();
graphStage.addEventListener('wheel', (event) => {
  if (!event.ctrlKey) return;
  event.preventDefault();
  const next = Math.min(2, Math.max(0.25, Math.round(zoom * (event.deltaY < 0 ? 1.1 : 1 / 1.1) * 100) / 100));
  if (next === zoom) return;
  const bounds = graphStage.getBoundingClientRect();
  const px = event.clientX - bounds.left, py = event.clientY - bounds.top;
  const wx = (px + graphStage.scrollLeft) / zoom, wy = (py + graphStage.scrollTop) / zoom;
  zoom = next;
  applyZoom();
  graphStage.scrollLeft = wx * zoom - px;
  graphStage.scrollTop  = wy * zoom - py;
}, { passive: false });

graphStage.addEventListener('pointerdown', (event) => {
  if (event.button !== 0 || event.target !== graphStage) return;
  event.preventDefault();
  const start = { x: event.clientX, y: event.clientY, left: graphStage.scrollLeft, top: graphStage.scrollTop };
  graphStage.classList.add('kan-panning');
  const move = (e) => {
    graphStage.scrollLeft = start.left - (e.clientX - start.x);
    graphStage.scrollTop  = start.top - (e.clientY - start.y);
  };
  const up = () => {
    graphStage.classList.remove('kan-panning');
    window.removeEventListener('pointermove', move);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up, { once: true });
});

function beginDrag(event, node) {
  event.preventDefault();
  const bounds = graphStage.getBoundingClientRect();
  state.dragging = { node, offsetX: (event.clientX - bounds.left + graphStage.scrollLeft) / zoom - node.position.x, offsetY: (event.clientY - bounds.top + graphStage.scrollTop) / zoom - node.position.y };
  window.addEventListener('pointermove', moveDrag);
  window.addEventListener('pointerup', endDrag, { once: true });
}
function moveDrag(event) {
  if (!state.dragging) return;
  const bounds = graphStage.getBoundingClientRect();
  state.dragging.node.position.x = Math.max(10, Math.round((event.clientX - bounds.left + graphStage.scrollLeft) / zoom - state.dragging.offsetX));
  state.dragging.node.position.y = Math.max(10, Math.round((event.clientY - bounds.top + graphStage.scrollTop) / zoom - state.dragging.offsetY));
  const element = nodeLayer.querySelector(`[data-node-id="${CSS.escape(state.dragging.node.id)}"]`);
  if (element) { element.style.left = `${state.dragging.node.position.x}px`; element.style.top = `${state.dragging.node.position.y}px`; }
  scheduleWireRedraw();
}
// The block follows the pointer immediately; wires catch up at a rate scaled to how long the last redraw took.
let wireTimer = 0, wireCost = 0;
function scheduleWireRedraw() {
  if (wireTimer) return;
  wireTimer = setTimeout(() => {
    wireTimer = 0;
    const started = performance.now();
    edgeLayer.innerHTML = '';
    drawEdges();
    wireCost = performance.now() - started;
  }, Math.max(16, wireCost * 2));
}
function endDrag() {
  clearTimeout(wireTimer);
  wireTimer = 0;
  if (state.dragging) { recentre(); persist(); }
  state.dragging = null;
  window.removeEventListener('pointermove', moveDrag);
}

function renderInspector() {
  const node = currentNode();
  $('#empty-inspector').hidden = Boolean(node);
  $('#node-form').hidden       = !node;
  $('#node-actions').hidden    = !node;
  if (!node) {
    $('#input-ports').innerHTML = $('#output-ports').innerHTML = '';
    $('#node-label').value = $('#node-code').value = '';
    return;
  }
  ensureNodeShape(node);
  $('#node-label').value = node.label;
  $('#node-order').value = node.order ?? executionOrder().indexOf(node.id) + 1;
  $('#node-kind').value = node.kind;
  $('#node-code').value  = node.code;
  $('#node-prompt-template').value = node.config.prompt_template || '';
  $('#node-llm-model').value = node.config.model || '';
  $('#node-question').value = node.config.question || '';
  $('#node-threshold').value = String(node.config.threshold ?? 0.7);
  $('#node-judge-model').value = node.config.model || '';
  showKindFields(node.kind);
  renderPorts($('#input-ports'), node.inputs, true);
  renderPorts($('#output-ports'), node.outputs, false);
}
function renderPorts(container, ports, isInput) {
  container.innerHTML = ports.map((port, index) => `<div class="kan-port-row"><input data-index="${index}" value="${escapeHtml(port.name)}" aria-label="${isInput ? 'Input' : 'Output'} port name"><button type="button" class="kcui-icon-button kan-port-remove" data-remove="${index}" title="Remove port">×</button></div>`).join('');
  container.querySelectorAll('[data-remove]').forEach((button) => button.addEventListener('click', () => { ports.splice(Number(button.dataset.remove), 1); state.network.edges = state.network.edges.filter((edge) => isInput ? !(edge.target_node === currentNode().id && edge.target_port === button.parentElement.querySelector('input').value) : !(edge.source_node === currentNode().id && edge.source_port === button.parentElement.querySelector('input').value)); persist(); render(); }));
}

const pretty = (value) => value === undefined ? '' : JSON.stringify(value, null, 2);
const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
let stateChain = Promise.resolve();
function persistState() {
  if (!state.network) return stateChain;
  const id   = state.network.id;
  const body = JSON.stringify({ run: state.run, edits: state.edits });
  stateChain = stateChain.then(() => request(`/api/networks/${encodeURIComponent(id)}/state`, { method: 'PUT', body }).catch((error) => setStatus(`State save failed: ${error.message}`, 'error')));
  return stateChain;
}
function editsFor(nodeId) { return (state.edits[nodeId] ||= { inputs: {}, outputs: {} }); }
function outputValues(nodeId) { return { ...(state.run?.nodes?.[nodeId]?.outputs || {}), ...(state.edits[nodeId]?.outputs || {}) }; }

function resolveInputs(node) {
  const values = {};
  for (const port of node.inputs) {
    const edit = state.edits[node.id]?.inputs || {};
    const edge = state.network.edges.find((item) => item.target_node === node.id && item.target_port === port.name);
    const upstream = edge ? outputValues(edge.source_node) : {};
    if (hasOwn(edit, port.name)) values[port.name] = edit[port.name];
    else if (edge && hasOwn(upstream, edge.source_port)) values[port.name] = upstream[edge.source_port];
    else values[port.name] = 'default' in port ? port.default : null;
  }
  return values;
}

async function playNode(nodeId) {
  const node = state.network?.nodes.find((item) => item.id === nodeId);
  if (!node) return false;
  try {
    setStatus(`Running ${node.label}…`, 'running');
    state.activeNodeId = node.id; render({ inspector: false });
    const { result } = await request('/api/run-node', { method: 'POST', body: JSON.stringify({ node, inputs: resolveInputs(node), network_id: state.network.id, outputs: Object.fromEntries(state.network.nodes.map((item) => [item.id, outputValues(item.id)])) }) });
    state.run ||= { nodes: {} };
    const { triggered, ...record } = result;
    state.run.nodes[node.id] = record;
    Object.entries(triggered || {}).forEach(([id, item]) => { state.run.nodes[id] = item; });
    state.activeNodeId = null;
    if (state.edits[node.id]) state.edits[node.id].outputs = {};
    persistState();
    render();
    setStatus(result.status === 'completed' ? `${node.label} completed in ${result.elapsed_seconds}s` : `${node.label} failed: ${result.error}`, result.status === 'completed' ? 'ok' : 'error');
    return result.status === 'completed';
  } catch (error) { state.activeNodeId = null; render({ inspector: false }); setStatus(error.message, 'error'); return false; }
}

function naturalOrder() { const owned = triggeredBlocks(); return executionOrder().filter((id) => !owned.has(id)); }
function triggeredBlocks() { return new Set((state.network.triggers || []).map((item) => item.target_node).filter(Boolean)); }
function executionLinks() { return state.network.edges.map((edge) => [edge.source_node, edge.target_node]); }
function executionOrder() {
  const links = executionLinks();
  const incoming = new Map(state.network.nodes.map((node) => [node.id, 0]));
  links.forEach(([, target]) => incoming.set(target, (incoming.get(target) || 0) + 1));
  const rank = new Map(state.network.nodes.map((node, index) => [node.id, [Number.isFinite(node.order) ? node.order : Infinity, index]]));
  const byRank = (a, b) => (rank.get(a)[0] - rank.get(b)[0]) || (rank.get(a)[1] - rank.get(b)[1]) || 0;
  const ready = state.network.nodes.filter((node) => !incoming.get(node.id)).map((node) => node.id);
  const order = [];
  while (ready.length) {
    ready.sort(byRank);
    const nodeId = ready.shift();
    order.push(nodeId);
    links.filter(([source]) => source === nodeId).forEach(([, target]) => { incoming.set(target, incoming.get(target) - 1); if (!incoming.get(target)) ready.push(target); });
  }
  return order;
}

async function stepNetwork() {
  if (!state.network) return;
  const next = naturalOrder().find((nodeId) => state.run?.nodes?.[nodeId]?.status !== 'completed');
  if (!next) { setStatus('All blocks have run. Use a block’s play button to re-run it.', 'ok'); return; }
  state.selectedNodeId = next;
  await playNode(next);
}

function dataCard(kind, port, value, note, edited) {
  const name = escapeHtml(port.name);
  const buttons = (kind === 'inputs' ? `<button type="button" data-default="${name}">Use as default</button>` : '') + (edited ? `<button type="button" data-reset="${kind}:${name}">Reset</button>` : '');
  return `<div class="kan-data-item"><div class="kan-data-name">${name}<small>${escapeHtml(note)}${edited ? ' · edited' : ''}</small></div>`
    + `<textarea data-kind="${kind}" data-name="${name}" rows="${Math.min(10, Math.max(2, pretty(value).split('\n').length))}" spellcheck="false" placeholder="—">${escapeHtml(pretty(value))}</textarea>`
    + (buttons ? `<div class="kan-data-actions">${buttons}</div>` : '') + '</div>';
}

function renderData() {
  const node = currentNode();
  $('#empty-data').hidden = Boolean(node);
  $('#data-form').hidden  = !node;
  if (!node) {
    $('#data-inputs').innerHTML = $('#data-outputs').innerHTML = '';
    $('#node-result').textContent = '';
    $('#data-status').textContent = '';
    $('#result-panel').hidden = true;
    return;
  }
  const result = state.run?.nodes?.[node.id];
  const edits  = state.edits[node.id] || { inputs: {}, outputs: {} };
  $('#result-panel').hidden = !result;
  if (result) $('#node-result').textContent = JSON.stringify(result, null, 2);
  const status = $('#data-status');
  status.className   = `kan-data-status ${result?.status || ''}`;
  status.textContent = result
    ? `Last run: ${result.status}${result.elapsed_seconds !== undefined ? ` in ${result.elapsed_seconds}s` : ''}${result.error ? ` — ${result.error}` : ''}`
    : 'Not run yet.';
  const inputs = resolveInputs(node);
  $('#data-inputs').innerHTML = node.inputs.map((port) => {
    const edge = state.network.edges.find((item) => item.target_node === node.id && item.target_port === port.name);
    return dataCard('inputs', port, inputs[port.name], edge ? `← ${state.network.nodes.find((n) => n.id === edge.source_node)?.label || edge.source_node} · ${edge.source_port}` : 'unconnected', hasOwn(edits.inputs, port.name));
  }).join('') || '<div class="kan-data-empty">No inputs.</div>';
  const outputs = outputValues(node.id);
  $('#data-outputs').innerHTML = node.outputs.map((port) => dataCard('outputs', port, outputs[port.name], 'feeds downstream blocks', hasOwn(edits.outputs, port.name))).join('') || '<div class="kan-data-empty">No outputs.</div>';
}

$('#data-form').addEventListener('change', (event) => {
  const area = event.target.closest('textarea[data-kind]');
  const node = currentNode();
  if (!area || !node) return;
  const text = area.value.trim();
  let value;
  try { value = text ? JSON.parse(text) : null; }
  catch (error) { setStatus(`${area.dataset.name}: invalid JSON (${error.message})`, 'error'); return; }
  editsFor(node.id)[area.dataset.kind][area.dataset.name] = value;
  persistState();
  setStatus(`${area.dataset.name} edited. Play a block to use the custom value.`, 'ok');
  setTimeout(renderData, 0);
});

$('#data-form').addEventListener('click', (event) => {
  const node = currentNode();
  const button = event.target.closest('button[data-default], button[data-reset]');
  if (!node || !button) return;
  if (button.dataset.default) {
    const port = node.inputs.find((item) => item.name === button.dataset.default);
    if (port) { port.default = resolveInputs(node)[port.name]; persist(); setStatus('Saved as the default.', 'ok'); }
  } else {
    const [kind, name] = button.dataset.reset.split(':');
    delete editsFor(node.id)[kind][name];
    persistState();
  }
  renderData();
});

$('#play-selected').addEventListener('click', () => { const node = currentNode(); if (node) playNode(node.id); });
$('#step-network').addEventListener('click', stepNetwork);

function setStatus(text, kind = '') { const status = $('#run-status'); status.textContent = text; status.className = `kan-run-status ${kind}`; }

$('#node-form').addEventListener('submit', (event) => event.preventDefault());
$('#node-form').addEventListener('change', applyNode);

function applyNode() {
  const node = currentNode();
  if (!node) return;
  const originalId = node.id;
  const originalKind = node.kind;
  const nextId = originalId;
  const nextKind = $('#node-kind').value;
  node.kind = nextKind;
  node.label = $('#node-label').value.trim() || nextId;
  node.code = $('#node-code').value;
  if (nextKind === 'llm') {
    node.config = { prompt_template: $('#node-prompt-template').value, model: $('#node-llm-model').value.trim() };
  } else if (nextKind === 'judge') {
    const threshold = Number.parseFloat($('#node-threshold').value);
    if (!Number.isFinite(threshold) || threshold < 0 || threshold > 1) { setStatus('Pass threshold must be between 0 and 1.', 'error'); return; }
    node.config = { question: $('#node-question').value, threshold, model: $('#node-judge-model').value.trim() };
  } else {
    node.config = {};
  }
  node.inputs = Array.from($('#input-ports').querySelectorAll('input')).map((input, index) => { const name = input.value.trim(); const old = node.inputs[index]; return old && 'default' in old ? { name, default: old.default } : { name }; }).filter((port) => port.name);
  node.outputs = Array.from($('#output-ports').querySelectorAll('input')).map((input) => ({ name: input.value.trim() })).filter((port) => port.name);
  if (node.kind === 'llm') {
    mergeTemplateInputs(node, node.config.prompt_template);
    if (!node.outputs.some((port) => port.name === 'response')) node.outputs.unshift({ name: 'response' });
  } else if (node.kind === 'judge') {
    mergeTemplateInputs(node, node.config.question, ['prompt', 'response']);
    ['verdict', 'probability'].forEach((name) => { if (!node.outputs.some((port) => port.name === name)) node.outputs.push({ name }); });
  }
  if (nextId !== originalId) state.network.edges.forEach((edge) => { if (edge.source_node === originalId) edge.source_node = nextId; if (edge.target_node === originalId) edge.target_node = nextId; });
  state.selectedNodeId = nextId;
  // Rebuild the inspector only when the port rows no longer match, so focus and clicks on other fields are not lost
  const kindChanged = originalKind !== node.kind;
  const rowNames = (container) => Array.from(container.querySelectorAll('input')).map((input) => input.value.trim()).join('|');
  const inspectorStale = rowNames($('#input-ports')) !== node.inputs.map((port) => port.name).join('|') || rowNames($('#output-ports')) !== node.outputs.map((port) => port.name).join('|') || nextId !== originalId;
  if (inspectorStale || kindChanged) { render({ inspector: inspectorStale }); }
  else {
    // Patch in place: rebuilding the canvas on blur would swallow the click that caused the blur
    const article = nodeLayer.querySelector(`[data-node-id="${CSS.escape(node.id)}"]`);
    const label = article?.querySelector('.kan-node-head > span:nth-of-type(2)');
    if (label) label.textContent = node.label;
  }
  pruneEdges();
  persist();
  setStatus('Block updated.', 'ok');
}
function applyOrder(event, final) {
  const node = currentNode();
  if (!node) return;
  const value = Number.parseFloat(event.target.value);
  if (Number.isFinite(value)) node.order = value;
  else if (final) delete node.order;
  else return;
  if (final) event.target.value = node.order ?? executionOrder().indexOf(node.id) + 1;
  render();
  persist();
}
$('#node-order').addEventListener('input', (event) => applyOrder(event, false));
$('#node-order').addEventListener('change', (event) => applyOrder(event, true));
document.querySelectorAll('#node-order ~ .stepper-btns button').forEach((button) => button.addEventListener('click', () => stepNumberInput(button, Number(button.dataset.step))));
$('#node-kind').addEventListener('change', (event) => showKindFields(event.target.value));
$('#add-input').addEventListener('click', () => { const node = currentNode(); if (node) { node.inputs.push({ name: `input_${node.inputs.length + 1}` }); persist(); render(); } });
$('#add-output').addEventListener('click', () => { const node = currentNode(); if (node) { node.outputs.push({ name: `output_${node.outputs.length + 1}` }); persist(); render(); } });
$('#add-node').addEventListener('click', () => { const node = defaultNode($('#new-node-kind').value, state.network.nodes.length); placeInView(node); state.network.nodes.push(node); recentre(); persist(); selectNode(node.id); });
$('#network-title').addEventListener('input', (event) => { if (!state.network) return; state.network.title = event.target.value; const name = document.querySelector('.kan-network-name'); if (name) name.value = event.target.value; });
$('#network-title').addEventListener('change', () => persist({ refreshList: true }));
let saveChain = Promise.resolve();
function persist({ refreshList = false } = {}) {
  if (!state.network) return saveChain;
  const snapshot = state.network;
  const body = JSON.stringify({ network: snapshot });
  const id = snapshot.id;
  saveChain = saveChain.then(async () => {
    try {
      const base = snapshot.updated_at;
      const saved = await request(`/api/networks/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify({ network: JSON.parse(body).network, base_updated_at: base }) });
      snapshot.updated_at = saved.network.updated_at;
      if (refreshList) await loadList();
    } catch (error) { setStatus(`Auto-save failed: ${error.message}`, 'error'); }
  });
  return saveChain;
}
$('#new-network').addEventListener('click', async () => { try { const { network } = await request('/api/networks', { method: 'POST', body: JSON.stringify({ title: 'Untitled network' }) }); state.network = network; try { localStorage.setItem('kan.lastNetwork', network.id); } catch { /* storage unavailable */ } state.selectedNodeId = null; render(); await loadList(); setStatus('New network created. Rename it in the title field.', 'ok'); } catch (error) { setStatus(error.message, 'error'); } });
$('#duplicate-network').addEventListener('click', async () => { if (!state.network) return; try { const { network } = await request(`/api/networks/${encodeURIComponent(state.network.id)}/duplicate`, { method: 'POST' }); await loadList(); await loadNetwork(network.id); setStatus(`Duplicated as ${network.title}.`, 'ok'); } catch (error) { setStatus(error.message, 'error'); } });
$('#delete-network').addEventListener('click', async () => { if (!state.network || !window.confirm(`Delete ${state.network.title}?`)) return; try { await request(`/api/networks/${encodeURIComponent(state.network.id)}`, { method: 'DELETE' }); state.network = null; const networks = await loadList(); if (networks[0]) await loadNetwork(networks[0].id); } catch (error) { setStatus(error.message, 'error'); } });
$('#run-network').addEventListener('click', async () => {
  if (!state.network || state.running) return;
  const started = performance.now();
  state.run = { nodes: {} };
  state.edits = {};
  persistState();
  state.running = true;
  render();
  let ok = true;
  for (const nodeId of naturalOrder()) {
    state.selectedNodeId = nodeId;
    ok = await playNode(nodeId);
    if (!ok) break;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  state.running = false;
  render();
  const seconds = ((performance.now() - started) / 1000).toFixed(1);
  setStatus(ok ? `Network completed in ${seconds}s` : `Stopped after ${seconds}s; inspect the failed block`, ok ? 'ok' : 'error');
});

loadList().then((networks) => {
  let last = null;
  try { last = localStorage.getItem('kan.lastNetwork'); } catch { /* storage unavailable */ }
  const pick = networks.find((n) => n.id === last) || networks[0];
  return pick ? loadNetwork(pick.id) : null;
}).catch((error) => setStatus(error.message, 'error'));

// Fonts and window size change port positions after the first render
document.fonts?.ready.then(() => state.network && render());
window.addEventListener('resize', () => state.network && render());

$('#delete-node').addEventListener('click', () => {
  const node = currentNode();
  if (!node || !window.confirm(`Delete ${node.label}?`)) return;
  const network = state.network;
  network.nodes = network.nodes.filter((item) => item.id !== node.id);
  network.triggers = (network.triggers || []).filter((item) => item.source_node !== node.id && item.target_node !== node.id);
  network.edges = network.edges.filter((edge) => edge.source_node !== node.id && edge.target_node !== node.id);
  if (state.run?.nodes) delete state.run.nodes[node.id];
  delete state.edits?.[node.id];
  persistState();
  state.selectedNodeId = null;
  render();
  persist();
});

initWorkspaceLayouts();
