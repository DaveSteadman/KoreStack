import { initServiceShell } from '/ui-elements/assets/js/chrome.js';

const state = { network: null, selectedNodeId: null, pendingOutput: null, run: null, edits: {}, dragging: null };
const $ = (selector) => document.querySelector(selector);
const graphStage = $('#graph-stage');
const nodeLayer  = $('#nodes');
const edgeLayer  = $('#edges');
const TEMPLATE_RE = /\{([A-Za-z0-9_-]+)\}/g;
const BLOCK_HELP = {
  python: 'Available: inputs, outputs, api_get, api_post, llm, llm_result, decide, judge, json, math, re.',
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
function nodeHeight(node) { return Math.max(126, 58 + Math.max(node.inputs.length, node.outputs.length, 1) * 25); }
function defaultNode(kind = 'python', index = 0) {
  const nodeId = id();
  const base = {
    id: nodeId,
    kind,
    config: {},
    position: { x: 110 + index * 34, y: 90 + index * 34 },
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
  return { x: node.position.x + (output ? 220 : 0), y: node.position.y + 59 + Math.max(index, 0) * 25 };
}

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

async function loadList() {
  const { networks } = await request('/api/networks');
  const list = $('#network-list');
  list.innerHTML = networks.map((network) => `<button class="kan-network-item ${network.id === state.network?.id ? 'active' : ''}" data-id="${escapeHtml(network.id)}"><strong>${escapeHtml(network.title)}</strong><small>${network.node_count} blocks</small></button>`).join('');
  list.querySelectorAll('[data-id]').forEach((button) => button.addEventListener('click', () => loadNetwork(button.dataset.id)));
  return networks;
}

async function loadNetwork(networkId) {
  const { network } = await request(`/api/networks/${encodeURIComponent(networkId)}`);
  network.nodes.forEach(ensureNodeShape);
  state.network        = network;
  state.selectedNodeId = null;
  state.pendingOutput  = null;
  state.run            = null;
  state.edits          = {};
  $('#network-title').value = network.title;
  render();
  await loadList();
}

function render() {
  const network = state.network;
  if (!network) return;
  network.nodes.forEach(ensureNodeShape);
  $('#network-title').value = network.title;
  $('#canvas-hint').hidden  = network.nodes.length > 0;
  nodeLayer.innerHTML = '';
  edgeLayer.innerHTML = '';
  network.nodes.forEach(renderNode);
  drawEdges();
  renderInspector();
  renderData();
}

function dotCenter(node, portName, output) {
  const article = nodeLayer.querySelector(`[data-node-id="${CSS.escape(node.id)}"]`);
  const port    = article?.querySelector(`.kan-port--${output ? 'output' : 'input'}[data-port="${CSS.escape(portName)}"] .kan-port-dot`);
  if (!port) return portPosition(node, portName, output);
  const origin = nodeLayer.getBoundingClientRect();
  const box    = port.getBoundingClientRect();
  return { x: box.left + box.width / 2 - origin.left, y: box.top + box.height / 2 - origin.top };
}

function drawEdges() {
  state.network.edges.forEach((edge) => {
    const fromNode = state.network.nodes.find((node) => node.id === edge.source_node);
    const toNode   = state.network.nodes.find((node) => node.id === edge.target_node);
    if (!fromNode || !toNode) return;
    const from = dotCenter(fromNode, edge.source_port, true);
    const to   = dotCenter(toNode, edge.target_port, false);
    const mid  = Math.max(45, Math.abs(to.x - from.x) * .5);
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', `M ${from.x} ${from.y} C ${from.x + mid} ${from.y}, ${to.x - mid} ${to.y}, ${to.x} ${to.y}`);
    path.setAttribute('title', `${edge.source_node}.${edge.source_port} ? ${edge.target_node}.${edge.target_port}`);
    edgeLayer.append(path);
  });
}

function renderNode(node) {
  const result = state.run?.nodes?.[node.id];
  const element = document.createElement('article');
  element.className = `kan-node ${node.id === state.selectedNodeId ? 'selected' : ''} ${result?.status || ''}`;
  element.dataset.nodeId = node.id;
  element.style.left = `${node.position.x}px`;
  element.style.top  = `${node.position.y}px`;
  element.style.minHeight = `${nodeHeight(node)}px`;
  element.innerHTML = `<div class="kan-node-head"><button type="button" class="kan-icon-button kan-node-play" title="Run this block with its current inputs">&#9654;</button><span>${escapeHtml(node.label)}</span><span class="kan-node-id">${escapeHtml(node.id)}</span></div><div class="kan-port-lines"><div class="kan-port-column">${node.inputs.map((port) => `<button type="button" class="kan-port kan-port--input" data-port="${escapeHtml(port.name)}"><i class="kan-port-dot"></i>${escapeHtml(port.name)}</button>`).join('')}</div><div class="kan-port-column">${node.outputs.map((port) => `<button type="button" class="kan-port kan-port--output ${state.pendingOutput?.nodeId === node.id && state.pendingOutput?.portName === port.name ? 'pending' : ''}" data-port="${escapeHtml(port.name)}">${escapeHtml(port.name)}<i class="kan-port-dot"></i></button>`).join('')}</div></div>`;
  const play = element.querySelector('.kan-node-play');
  play.addEventListener('pointerdown', (event) => event.stopPropagation());
  play.addEventListener('click', (event) => { event.stopPropagation(); state.selectedNodeId = node.id; playNode(node.id); });
  element.querySelector('.kan-node-head').addEventListener('pointerdown', (event) => { if (!event.target.closest('.kan-node-play')) beginDrag(event, node); });
  element.addEventListener('click', (event) => { if (!event.target.closest('.kan-port')) selectNode(node.id); });
  element.querySelectorAll('.kan-port--output').forEach((port) => port.addEventListener('click', (event) => { event.stopPropagation(); state.pendingOutput = { nodeId: node.id, portName: port.dataset.port }; render(); }));
  element.querySelectorAll('.kan-port--input').forEach((port) => port.addEventListener('click', (event) => { event.stopPropagation(); connectInput(node.id, port.dataset.port); }));
  nodeLayer.append(element);
}

function selectNode(nodeId) { state.selectedNodeId = nodeId; render(); }
function connectInput(targetNode, targetPort) {
  if (!state.pendingOutput) { setStatus('Choose an output first.', 'error'); return; }
  const source = state.pendingOutput;
  if (source.nodeId === targetNode) { setStatus('A block cannot feed itself.', 'error'); return; }
  state.network.edges = state.network.edges.filter((edge) => !(edge.target_node === targetNode && edge.target_port === targetPort));
  state.network.edges.push({ id: `edge_${crypto.randomUUID().replaceAll('-', '').slice(0, 10)}`, source_node: source.nodeId, source_port: source.portName, target_node: targetNode, target_port: targetPort });
  state.pendingOutput = null;
  render();
  setStatus('Ports connected. Save the network to persist it.', 'ok');
}

function beginDrag(event, node) {
  event.preventDefault();
  const bounds = graphStage.getBoundingClientRect();
  state.dragging = { node, offsetX: event.clientX - bounds.left + graphStage.scrollLeft - node.position.x, offsetY: event.clientY - bounds.top + graphStage.scrollTop - node.position.y };
  window.addEventListener('pointermove', moveDrag);
  window.addEventListener('pointerup', endDrag, { once: true });
}
function moveDrag(event) {
  if (!state.dragging) return;
  const bounds = graphStage.getBoundingClientRect();
  state.dragging.node.position.x = Math.max(10, Math.round(event.clientX - bounds.left + graphStage.scrollLeft - state.dragging.offsetX));
  state.dragging.node.position.y = Math.max(10, Math.round(event.clientY - bounds.top + graphStage.scrollTop - state.dragging.offsetY));
  render();
}
function endDrag() { state.dragging = null; window.removeEventListener('pointermove', moveDrag); }

function renderInspector() {
  const node = currentNode();
  $('#empty-inspector').hidden = Boolean(node);
  $('#node-form').hidden       = !node;
  $('#result-panel').hidden    = !node || !state.run?.nodes?.[node.id];
  if (!node) return;
  ensureNodeShape(node);
  $('#node-label').value = node.label;
  $('#node-id').value    = node.id;
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
  const result = state.run?.nodes?.[node.id];
  if (result) $('#node-result').textContent = JSON.stringify(result, null, 2);
}
function renderPorts(container, ports, isInput) {
  container.innerHTML = ports.map((port, index) => `<div class="kan-port-row"><input data-index="${index}" value="${escapeHtml(port.name)}" aria-label="${isInput ? 'Input' : 'Output'} port name"><button type="button" data-remove="${index}" title="Remove port">×</button></div>`).join('');
  container.querySelectorAll('[data-remove]').forEach((button) => button.addEventListener('click', () => { ports.splice(Number(button.dataset.remove), 1); state.network.edges = state.network.edges.filter((edge) => isInput ? !(edge.target_node === currentNode().id && edge.target_port === button.parentElement.querySelector('input').value) : !(edge.source_node === currentNode().id && edge.source_port === button.parentElement.querySelector('input').value)); render(); }));
}

const pretty = (value) => value === undefined ? '' : JSON.stringify(value, null, 2);
const hasOwn = (object, key) => Object.prototype.hasOwnProperty.call(object || {}, key);
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
    const { result } = await request('/api/run-node', { method: 'POST', body: JSON.stringify({ node, inputs: resolveInputs(node) }) });
    state.run ||= { nodes: {} };
    state.run.nodes[node.id] = result;
    if (state.edits[node.id]) state.edits[node.id].outputs = {};
    render();
    setStatus(result.status === 'completed' ? `${node.label} completed in ${result.elapsed_seconds}s` : `${node.label} failed: ${result.error}`, result.status === 'completed' ? 'ok' : 'error');
    return result.status === 'completed';
  } catch (error) { setStatus(error.message, 'error'); return false; }
}

function executionOrder() {
  const incoming = new Map(state.network.nodes.map((node) => [node.id, 0]));
  state.network.edges.forEach((edge) => incoming.set(edge.target_node, (incoming.get(edge.target_node) || 0) + 1));
  const ready = state.network.nodes.filter((node) => !incoming.get(node.id)).map((node) => node.id);
  const order = [];
  while (ready.length) {
    const nodeId = ready.shift();
    order.push(nodeId);
    state.network.edges.filter((edge) => edge.source_node === nodeId).forEach((edge) => { incoming.set(edge.target_node, incoming.get(edge.target_node) - 1); if (!incoming.get(edge.target_node)) ready.push(edge.target_node); });
  }
  return order;
}

async function stepNetwork() {
  if (!state.network) return;
  const next = executionOrder().find((nodeId) => state.run?.nodes?.[nodeId]?.status !== 'completed');
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
  if (!node) return;
  const result = state.run?.nodes?.[node.id];
  const edits  = state.edits[node.id] || { inputs: {}, outputs: {} };
  const status = $('#data-status');
  status.className   = `kan-data-status ${result?.status || ''}`;
  status.textContent = result
    ? `Last run: ${result.status}${result.elapsed_seconds !== undefined ? ` in ${result.elapsed_seconds}s` : ''}${result.error ? ` — ${result.error}` : ''}`
    : 'Not run yet.';
  const inputs = resolveInputs(node);
  $('#data-inputs').innerHTML = node.inputs.map((port) => {
    const edge = state.network.edges.find((item) => item.target_node === node.id && item.target_port === port.name);
    return dataCard('inputs', port, inputs[port.name], edge ? `← ${edge.source_node}.${edge.source_port}` : 'unconnected', hasOwn(edits.inputs, port.name));
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
  setStatus(`${area.dataset.name} edited. Play a block to use the custom value.`, 'ok');
  setTimeout(renderData, 0);
});

$('#data-form').addEventListener('click', (event) => {
  const node = currentNode();
  const button = event.target.closest('button[data-default], button[data-reset]');
  if (!node || !button) return;
  if (button.dataset.default) {
    const port = node.inputs.find((item) => item.name === button.dataset.default);
    if (port) { port.default = resolveInputs(node)[port.name]; setStatus('Saved as the default. Save the network to persist.', 'ok'); }
  } else {
    const [kind, name] = button.dataset.reset.split(':');
    delete editsFor(node.id)[kind][name];
  }
  renderData();
});

$('#play-selected').addEventListener('click', () => { const node = currentNode(); if (node) playNode(node.id); });
$('#step-network').addEventListener('click', stepNetwork);

function setStatus(text, kind = '') { const status = $('#run-status'); status.textContent = text; status.className = `kan-run-status ${kind}`; }

$('#node-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const node = currentNode();
  if (!node) return;
  const originalId = node.id;
  const nextId = $('#node-id').value.trim();
  const nextKind = $('#node-kind').value;
  if (!/^[A-Za-z0-9_-]+$/.test(nextId)) { setStatus('Block id may contain letters, numbers, _ or -.', 'error'); return; }
  if (nextId !== originalId && state.network.nodes.some((item) => item.id === nextId)) { setStatus('Block id must be unique.', 'error'); return; }
  node.id = nextId;
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
  render(); setStatus('Block changes applied. Save to persist.', 'ok');
});
$('#node-kind').addEventListener('change', (event) => showKindFields(event.target.value));
$('#add-input').addEventListener('click', () => { const node = currentNode(); if (node) { node.inputs.push({ name: `input_${node.inputs.length + 1}` }); render(); } });
$('#add-output').addEventListener('click', () => { const node = currentNode(); if (node) { node.outputs.push({ name: `output_${node.outputs.length + 1}` }); render(); } });
$('#add-node').addEventListener('click', () => { const node = defaultNode($('#new-node-kind').value, state.network.nodes.length); state.network.nodes.push(node); selectNode(node.id); });
$('#network-title').addEventListener('input', (event) => { if (state.network) state.network.title = event.target.value; });
$('#save-network').addEventListener('click', async () => { try { const { network } = await request(`/api/networks/${encodeURIComponent(state.network.id)}`, { method: 'PUT', body: JSON.stringify({ network: state.network }) }); state.network = network; await loadList(); setStatus('Network saved.', 'ok'); } catch (error) { setStatus(error.message, 'error'); } });
$('#new-network').addEventListener('click', async () => { try { const { network } = await request('/api/networks', { method: 'POST', body: JSON.stringify({ title: 'Untitled network' }) }); state.network = network; state.selectedNodeId = null; render(); await loadList(); setStatus('New network created. Rename it in the title field.', 'ok'); } catch (error) { setStatus(error.message, 'error'); } });
$('#delete-network').addEventListener('click', async () => { if (!state.network || !window.confirm(`Delete ${state.network.title}?`)) return; try { await request(`/api/networks/${encodeURIComponent(state.network.id)}`, { method: 'DELETE' }); state.network = null; const networks = await loadList(); if (networks[0]) await loadNetwork(networks[0].id); } catch (error) { setStatus(error.message, 'error'); } });
$('#run-network').addEventListener('click', async () => { if (!state.network) return; try { setStatus('Running network…', 'running'); const { run } = await request(`/api/networks/${encodeURIComponent(state.network.id)}/run`, { method: 'POST' }); state.run = run; state.edits = {}; render(); setStatus(run.ok ? `Completed in ${run.elapsed_seconds}s` : `Stopped after ${run.elapsed_seconds}s; inspect failed block`, run.ok ? 'ok' : 'error'); } catch (error) { setStatus(error.message, 'error'); } });

loadList().then((networks) => networks[0] ? loadNetwork(networks[0].id) : null).catch((error) => setStatus(error.message, 'error'));

// Fonts and window size change port positions after the first render
document.fonts?.ready.then(() => state.network && render());
window.addEventListener('resize', () => state.network && render());
