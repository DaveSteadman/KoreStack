(function () {
  'use strict';

  const pfx = window._kgPfx || '';
  const _SIDEBAR_STATE_KEY = 'koregraph.graph-sidebar-state';
  const _SIDEBAR_WIDTH_KEY = 'koregraph-graph-sidebar-w';
  const _MIN_SIDEBAR_W = 260;
  const _MAX_SIDEBAR_W = 560;
  const _DEFAULT_SIDEBAR_W = 320;
  const _PAN_THRESHOLD = 6;
  const SHOWCASE_INTERVAL_MS = 2000;
  const SHOWCASE_RECENT_LIMIT = 10;

  const input = document.getElementById('g-input');
  const btn = document.getElementById('g-btn');
  const showcaseBtn = document.getElementById('g-showcase');
  const gwBtn = document.getElementById('g-search-gw');
  const ac = document.getElementById('g-ac');
  const outer = document.getElementById('g-outer');
  const canvas = document.getElementById('g-canvas');
  const ctx = canvas.getContext('2d');
  const hint = document.getElementById('g-hint');
  const shell = document.getElementById('kg-shell');
  const sidebar = document.getElementById('kg-sidebar');
  const splitter = document.getElementById('kg-splitter');
  const closeSidebarBtn = document.getElementById('kg-close-sidebar');
  const sidebarStatus = document.getElementById('kg-sidebar-status');
  const selectionPath = document.getElementById('kg-selection-path');
  const emptySelection = document.getElementById('kg-empty-selection');
  const selectionCard = document.getElementById('kg-selection-card');
  const selectionType = document.getElementById('kg-selection-type');
  const selectionTitle = document.getElementById('kg-selection-title');
  const selectionCopy = document.getElementById('kg-selection-copy');
  const selectionMeta = document.getElementById('kg-selection-meta');
  const deleteSelectionBtn = document.getElementById('kg-delete-selection');

  const C = {
    bg: '#0b0c10',
    panel: '#0f1117',
    border: '#1e2233',
    text: '#c5c8d0',
    dim: '#4e5466',
    green: '#4af77a',
    blue: '#6eb5ff',
    amber: '#f0c060',
  };

  const view = { zoom: 1.0, pan: { x: 0, y: 0 } };
  let _graphData = null;
  let _positions = new Map();
  let _nodeRing = new Map();
  let _centralId = null;
  let _nodeHits = [];
  let _edgeLabelHits = [];
  let _selection = null;
  let _nodeDetail = null;
  let _sidebarOpen = false;
  let _transition = null;
  let _showcaseActive = false;
  let _showcaseTimer = null;
  let _showcaseBusy = false;
  let _showcaseRecent = [];

  const NODE_R = 34;
  const NODE_R2 = 26;
  const TRANSITION_MS = 1000;
  const TRANSITION_HANDOFF_FRACTION = 0.28;

  function _readSidebarWidth() {
    try {
      const raw = Number(localStorage.getItem(_SIDEBAR_WIDTH_KEY));
      if (!Number.isFinite(raw)) return _DEFAULT_SIDEBAR_W;
      return Math.max(_MIN_SIDEBAR_W, Math.min(_MAX_SIDEBAR_W, raw));
    } catch (_) {
      return _DEFAULT_SIDEBAR_W;
    }
  }

  function _applySidebarWidth(width) {
    const clamped = Math.max(_MIN_SIDEBAR_W, Math.min(_MAX_SIDEBAR_W, Number(width) || _DEFAULT_SIDEBAR_W));
    shell.style.setProperty('--kg-sidebar-w', `${clamped}px`);
    try {
      localStorage.setItem(_SIDEBAR_WIDTH_KEY, String(clamped));
    } catch (_) {}
    return clamped;
  }

  function _loadSidebarState() {
    try {
      const raw = localStorage.getItem(_SIDEBAR_STATE_KEY);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (_) {
      return null;
    }
  }

  function _saveSidebarState() {
    try {
      localStorage.setItem(_SIDEBAR_STATE_KEY, JSON.stringify({
        open: _sidebarOpen,
        selection: _serializeSelection(),
      }));
    } catch (_) {}
  }

  function _serializeSelection() {
    if (!_selection) return null;
    if (_selection.kind === 'node') {
      return {
        kind: 'node',
        concept_id: _selection.node.concept_id,
        vocab_id: _selection.node.vocab_id,
      };
    }
    return {
      kind: 'edge',
      start_concept_id: _selection.edge.start_concept_id,
      connection_concept_id: _selection.edge.connection_concept_id,
      end_concept_id: _selection.edge.end_concept_id,
    };
  }

  function _setSidebarOpen(open) {
    _sidebarOpen = Boolean(open);
    shell.classList.toggle('is-sidebar-open', _sidebarOpen);
    sidebar.setAttribute('aria-hidden', _sidebarOpen ? 'false' : 'true');
    _saveSidebarState();
    requestAnimationFrame(resize);
  }

  function _showcaseCandidates() {
    if (!_graphData?.matched) return [];
    const nodes = _graphData.nodes || [];
    const directIds = new Set();
    for (const edge of _graphData.edges || []) {
      if (edge.start_concept_id === _centralId && edge.end_concept_id !== _centralId) {
        directIds.add(edge.end_concept_id);
      }
      if (edge.end_concept_id === _centralId && edge.start_concept_id !== _centralId) {
        directIds.add(edge.start_concept_id);
      }
    }

    const directNodes = nodes.filter(node => directIds.has(node.concept_id));
    if (directNodes.length) return directNodes;
    return nodes.filter(node => node.concept_id !== _centralId && _nodeRing.get(node.concept_id) === 1);
  }

  function _rememberShowcaseNode(conceptId) {
    if (!Number.isFinite(conceptId)) return;
    _showcaseRecent = _showcaseRecent.filter(id => id !== conceptId);
    _showcaseRecent.push(conceptId);
    if (_showcaseRecent.length > SHOWCASE_RECENT_LIMIT) {
      _showcaseRecent = _showcaseRecent.slice(-SHOWCASE_RECENT_LIMIT);
    }
  }

  function _updateShowcaseButton() {
    showcaseBtn.classList.toggle('is-active', _showcaseActive);
    showcaseBtn.setAttribute('aria-pressed', _showcaseActive ? 'true' : 'false');
    showcaseBtn.title = _showcaseActive ? 'Stop showcase traversal' : 'Start showcase traversal';
    showcaseBtn.setAttribute('aria-label', showcaseBtn.title);
    showcaseBtn.disabled = !_graphData?.matched || _showcaseCandidates().length === 0;
  }

  function _stopShowcase() {
    _showcaseActive = false;
    _showcaseBusy = false;
    if (_showcaseTimer) {
      clearTimeout(_showcaseTimer);
      _showcaseTimer = null;
    }
    _updateShowcaseButton();
  }

  function _scheduleShowcase(delay = SHOWCASE_INTERVAL_MS) {
    if (_showcaseTimer) clearTimeout(_showcaseTimer);
    if (!_showcaseActive) return;
    _showcaseTimer = setTimeout(_runShowcaseStep, delay);
  }

  async function _runShowcaseStep() {
    _showcaseTimer = null;
    if (!_showcaseActive) return;
    if (_transition || _showcaseBusy || _panning || _pendingBlankPan || _sidebarResizeDrag) {
      _scheduleShowcase(300);
      return;
    }

    const candidates = _showcaseCandidates();
    if (!candidates.length) {
      _stopShowcase();
      return;
    }

    const recentSet = new Set(_showcaseRecent);
    const unselected = candidates.filter(node => node.concept_id !== _selection?.node?.concept_id);
    const unrecent = unselected.filter(node => !recentSet.has(node.concept_id));
    const pool = unrecent.length ? unrecent : (unselected.length ? unselected : candidates);
    const node = pool[Math.floor(Math.random() * pool.length)];
    if (!node) {
      _scheduleShowcase();
      return;
    }

    _showcaseBusy = true;
    try {
      _rememberShowcaseNode(node.concept_id);
      await handleNodeClick(node);
    } finally {
      _showcaseBusy = false;
      if (_showcaseActive) _scheduleShowcase();
    }
  }

  function _toggleShowcase() {
    if (_showcaseActive) {
      _stopShowcase();
      return;
    }
    if (!_graphData?.matched || _showcaseCandidates().length === 0) {
      _updateShowcaseButton();
      return;
    }
    _showcaseActive = true;
    _showcaseRecent = Number.isFinite(_centralId) ? [_centralId] : [];
    _updateShowcaseButton();
    _scheduleShowcase(0);
  }

  function _setSidebarStatus(message) {
    sidebarStatus.textContent = message;
  }

  function resize() {
    canvas.width = outer.clientWidth;
    canvas.height = outer.clientHeight;
    draw();
  }
  new ResizeObserver(resize).observe(outer);
  _applySidebarWidth(_readSidebarWidth());

  function nodeRadius(id) {
    if (id === _centralId) return NODE_R + 6;
    return _nodeRing.get(id) === 2 ? NODE_R2 : NODE_R;
  }

  function _sceneNodeRadius(scene, id) {
    if (id === scene.centralId) return NODE_R + 6;
    return scene.nodeRing.get(id) === 2 ? NODE_R2 : NODE_R;
  }

  function _snapshotGraphScene() {
    if (!_graphData || !_graphData.matched) return null;
    return {
      graphData: _graphData,
      positions: new Map(Array.from(_positions.entries(), ([id, pos]) => [id, { wx: pos.wx, wy: pos.wy }])),
      nodeRing: new Map(_nodeRing),
      centralId: _centralId,
      view: {
        zoom: view.zoom,
        pan: { x: view.pan.x, y: view.pan.y },
      },
    };
  }

  function _easeInOut(t) {
    return 0.5 - Math.cos(Math.PI * t) / 2;
  }

  function _lerp(a, b, t) {
    return a + (b - a) * t;
  }

  function _hexToRgb(hex) {
    if (typeof hex !== 'string') return null;
    const value = hex.trim();
    const match = /^#([0-9a-f]{6})$/i.exec(value);
    if (!match) return null;
    const raw = match[1];
    return {
      r: parseInt(raw.slice(0, 2), 16),
      g: parseInt(raw.slice(2, 4), 16),
      b: parseInt(raw.slice(4, 6), 16),
    };
  }

  function _mixColor(from, to, t) {
    const a = _hexToRgb(from);
    const b = _hexToRgb(to);
    if (!a || !b) return t >= 1 ? to : from;
    return `rgb(${Math.round(_lerp(a.r, b.r, t))} ${Math.round(_lerp(a.g, b.g, t))} ${Math.round(_lerp(a.b, b.b, t))})`;
  }

  function _sceneNodeById(scene, conceptId) {
    return scene?.graphData?.nodes?.find(nd => nd.concept_id === conceptId) || null;
  }

  function _defaultNodeVisual(scene, nd) {
    const isCentral = nd.concept_id === scene.centralId;
    const isRing2 = scene.nodeRing.get(nd.concept_id) === 2;
    const isSelected = _isSelectedNode(nd.concept_id);
    return {
      fillStyle: isSelected ? 'rgba(240, 192, 96, 0.14)' : C.panel,
      strokeStyle: isSelected ? C.amber : (isCentral ? C.green : (isRing2 ? C.dim : C.border)),
      lineWidth: isSelected ? 2.4 : (isCentral ? 2 : 1.5),
      textStyle: isSelected ? C.amber : (isCentral ? C.green : (isRing2 ? C.dim : C.text)),
    };
  }

  function _lerpNodeVisual(from, to, t) {
    return {
      fillStyle: t >= 1 ? to.fillStyle : from.fillStyle,
      strokeStyle: _mixColor(from.strokeStyle, to.strokeStyle, t),
      lineWidth: _lerp(from.lineWidth, to.lineWidth, t),
      textStyle: _mixColor(from.textStyle, to.textStyle, t),
    };
  }

  function _buildTransitionNodeVisuals(transition, handoff) {
    const previousScene = transition?.previousScene;
    const nextScene = transition?.nextScene;
    if (!previousScene || !nextScene) return null;

    const visuals = new Map();
    const previousCentralNode = _sceneNodeById(previousScene, previousScene.centralId);
    const nextCentralNode = _sceneNodeById(nextScene, previousScene.centralId) || previousCentralNode;
    if (previousCentralNode && nextCentralNode) {
      visuals.set(
        previousScene.centralId,
        _lerpNodeVisual(
          _defaultNodeVisual(previousScene, previousCentralNode),
          _defaultNodeVisual(nextScene, nextCentralNode),
          handoff,
        ),
      );
    }

    const focusNode = _sceneNodeById(previousScene, transition.focusNodeId);
    const promotedFocusNode = _sceneNodeById(nextScene, transition.focusNodeId) || focusNode;
    if (focusNode && promotedFocusNode) {
      visuals.set(
        transition.focusNodeId,
        _lerpNodeVisual(
          _defaultNodeVisual(previousScene, focusNode),
          _defaultNodeVisual(nextScene, promotedFocusNode),
          handoff,
        ),
      );
    }

    return visuals;
  }

  function _screenPointFor(scene, conceptId) {
    const pos = scene?.positions?.get(conceptId);
    if (!pos) return null;
    return {
      x: scene.view.pan.x + pos.wx * scene.view.zoom,
      y: scene.view.pan.y + pos.wy * scene.view.zoom,
    };
  }

  function _tickTransition() {
    if (!_transition) return;
    const elapsed = performance.now() - _transition.startedAt;
    if (elapsed >= _transition.duration) {
      _transition = null;
      draw();
      return;
    }
    draw();
    requestAnimationFrame(_tickTransition);
  }

  function _startGraphTransition(previousScene, focusNodeId) {
    const nextScene = _snapshotGraphScene();
    if (!previousScene || !nextScene || !_screenPointFor(previousScene, focusNodeId) || !_screenPointFor(nextScene, focusNodeId)) {
      _transition = null;
      draw();
      return;
    }

    _transition = {
      previousScene,
      nextScene,
      focusNodeId,
      duration: TRANSITION_MS,
      startedAt: performance.now(),
    };
    requestAnimationFrame(_tickTransition);
  }

  function _buildInterpolatedScene(transition, eased) {
    const previousScene = transition?.previousScene;
    const nextScene = transition?.nextScene;
    if (!previousScene || !nextScene) return nextScene;

    const viewState = {
      pan: {
        x: _lerp(previousScene.view.pan.x, nextScene.view.pan.x, eased),
        y: _lerp(previousScene.view.pan.y, nextScene.view.pan.y, eased),
      },
      zoom: _lerp(previousScene.view.zoom, nextScene.view.zoom, eased),
    };

    const positions = new Map();
    for (const nd of nextScene.graphData.nodes) {
      const nextScreen = _screenPointFor(nextScene, nd.concept_id);
      if (!nextScreen) continue;
      const prevScreen = _screenPointFor(previousScene, nd.concept_id) || nextScreen;
      const currentScreen = {
        x: _lerp(prevScreen.x, nextScreen.x, eased),
        y: _lerp(prevScreen.y, nextScreen.y, eased),
      };
      const zoom = Math.abs(viewState.zoom) < 1e-6 ? 1 : viewState.zoom;
      positions.set(nd.concept_id, {
        wx: (currentScreen.x - viewState.pan.x) / zoom,
        wy: (currentScreen.y - viewState.pan.y) / zoom,
      });
    }

    return {
      graphData: nextScene.graphData,
      positions,
      nodeRing: nextScene.nodeRing,
      centralId: nextScene.centralId,
      view: viewState,
    };
  }

  function _sameEdge(a, b) {
    return Boolean(a && b
      && a.start_concept_id === b.start_concept_id
      && a.connection_concept_id === b.connection_concept_id
      && a.end_concept_id === b.end_concept_id);
  }

  function _isSelectedNode(id) {
    return _selection?.kind === 'node' && _selection.node?.concept_id === id;
  }

  function _isSelectedEdge(edge) {
    return _selection?.kind === 'edge' && _sameEdge(_selection.edge, edge);
  }

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    _nodeHits = [];
    _edgeLabelHits = [];
    if (_transition) {
      const elapsed = performance.now() - _transition.startedAt;
      const progress = Math.min(1, elapsed / _transition.duration);
      const handoffProgress = Math.min(1, progress / TRANSITION_HANDOFF_FRACTION);
      const handoffEased = _easeInOut(handoffProgress);
      const motionProgress = progress <= TRANSITION_HANDOFF_FRACTION
        ? 0
        : (progress - TRANSITION_HANDOFF_FRACTION) / (1 - TRANSITION_HANDOFF_FRACTION);
      const motionEased = _easeInOut(motionProgress);
      const previousNodeVisuals = _buildTransitionNodeVisuals(_transition, handoffEased);

      _drawScene(_transition.previousScene, {
        sceneAlpha: motionProgress > 0 ? 1 - motionEased : 1,
        nodeVisuals: previousNodeVisuals,
      });

      if (motionProgress > 0) {
        const transitionScene = _buildInterpolatedScene(_transition, motionEased);
        _drawScene(transitionScene, { sceneAlpha: motionEased });
      }
      return;
    }
    if (!_graphData || !_graphData.matched) return;
    _drawScene(_snapshotGraphScene(), { collectHits: true, sceneAlpha: 1 });
  }

  function _drawScene(scene, options = {}) {
    if (!scene?.graphData?.matched) return;

    const collectHits = Boolean(options.collectHits);
    const nodeVisuals = options.nodeVisuals instanceof Map ? options.nodeVisuals : null;
    const sceneAlpha = Math.max(0, Math.min(1, Number(options.sceneAlpha) || 0));
    if (sceneAlpha <= 0) return;

    const pan = options.pan || scene.view?.pan || view.pan;
    const zoom = options.zoom || scene.view?.zoom || view.zoom;
    const { nodes, edges } = scene.graphData;
    ctx.save();
    ctx.translate(pan.x, pan.y);
    ctx.scale(zoom, zoom);

    const pairs = new Map();
    for (const e of edges) {
      if (e.start_concept_id === e.end_concept_id) continue;
      if (!scene.positions.has(e.start_concept_id) || !scene.positions.has(e.end_concept_id)) continue;
      const key = [e.start_concept_id, e.end_concept_id].sort().join('_');
      if (!pairs.has(key)) pairs.set(key, []);
      pairs.get(key).push(e);
    }

    for (const group of pairs.values()) {
      const count = group.length;
      group.forEach((e, gi) => {
        const p1 = scene.positions.get(e.start_concept_id);
        const p2 = scene.positions.get(e.end_concept_id);
        const dx = p2.wx - p1.wx;
        const dy = p2.wy - p1.wy;
        const len = Math.hypot(dx, dy);
        if (len < 1) return;
        const ux = dx / len;
        const uy = dy / len;
        const selected = _isSelectedEdge(e);

        const r1c = _sceneNodeRadius(scene, e.start_concept_id);
        const r2c = _sceneNodeRadius(scene, e.end_concept_id);
        const x1 = p1.wx + ux * r1c;
        const y1 = p1.wy + uy * r1c;
        const x2 = p2.wx - ux * (r2c + 2);
        const y2 = p2.wy - uy * (r2c + 2);

        ctx.save();
        ctx.strokeStyle = selected ? C.amber : C.blue;
        ctx.globalAlpha = sceneAlpha * (selected ? 0.92 : 0.45);
        ctx.lineWidth = selected ? 2.6 : 1.5;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
        ctx.restore();

        drawArrow(p1, p2, r2c, selected, sceneAlpha);

        const stagger = (gi - (count - 1) / 2) * 15;
        const lx = (x1 + x2) / 2 + (-uy) * stagger;
        const ly = (y1 + y2) / 2 + ux * stagger;
        ctx.save();
        ctx.font = '10px monospace';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const lbl = e.connection_name;
        const tw = ctx.measureText(lbl).width;
        const hit = { x: lx - tw / 2 - 3, y: ly - 7, w: tw + 6, h: 14, edge: e };
        if (collectHits) _edgeLabelHits.push(hit);
        ctx.fillStyle = selected ? 'rgba(240, 192, 96, 0.16)' : C.bg;
        ctx.globalAlpha = sceneAlpha * (selected ? 1 : 0.82);
        ctx.fillRect(hit.x, hit.y, hit.w, hit.h);
        ctx.globalAlpha = sceneAlpha;
        ctx.fillStyle = selected ? C.amber : C.dim;
        ctx.fillText(lbl, lx, ly);
        ctx.restore();
      });
    }

    for (const nd of nodes) {
      const p = scene.positions.get(nd.concept_id);
      if (!p) continue;
      const visual = nodeVisuals?.get(nd.concept_id) || _defaultNodeVisual(scene, nd);
      const r = _sceneNodeRadius(scene, nd.concept_id);

      ctx.save();
      ctx.globalAlpha = sceneAlpha;
      ctx.beginPath();
      ctx.arc(p.wx, p.wy, r, 0, Math.PI * 2);
      ctx.fillStyle = visual.fillStyle;
      ctx.fill();
      ctx.strokeStyle = visual.strokeStyle;
      ctx.lineWidth = visual.lineWidth;
      ctx.stroke();

      ctx.font = '11px monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = visual.textStyle;
      const maxW = r * 1.55;
      const lines = wrapLabel(nd.name, maxW);
      const lineH = 13;
      const topY = p.wy - (lines.length - 1) * lineH / 2;
      for (let li = 0; li < lines.length; li++) {
        ctx.fillText(lines[li], p.wx, topY + li * lineH);
      }
      ctx.restore();

      if (collectHits) _nodeHits.push({ x: p.wx, y: p.wy, r, node: nd });
    }

    ctx.restore();
  }

  function wrapLabel(name, maxPx) {
    ctx.font = '11px monospace';
    const words = name.split(' ');
    const lines = [];
    let cur = '';
    for (const word of words) {
      const test = cur ? cur + ' ' + word : word;
      if (ctx.measureText(test).width <= maxPx) {
        cur = test;
      } else {
        if (cur) lines.push(cur);
        if (ctx.measureText(word).width > maxPx) {
          let t = word;
          while (t.length > 1 && ctx.measureText(t + '…').width > maxPx) t = t.slice(0, -1);
          lines.push(t + '…');
          cur = '';
        } else {
          cur = word;
        }
      }
    }
    if (cur) lines.push(cur);
    return lines;
  }

  function drawArrow(from, to, targetRadius, selected, sceneAlpha = 1) {
    const dx = to.wx - from.wx;
    const dy = to.wy - from.wy;
    const angle = Math.atan2(dy, dx);
    const size = 9;
    const tx = to.wx - (targetRadius + 2) * Math.cos(angle);
    const ty = to.wy - (targetRadius + 2) * Math.sin(angle);
    ctx.save();
    ctx.fillStyle = selected ? C.amber : C.blue;
    ctx.globalAlpha = sceneAlpha * (selected ? 0.92 : 0.65);
    ctx.beginPath();
    ctx.moveTo(tx, ty);
    ctx.lineTo(tx - size * Math.cos(angle - 0.4), ty - size * Math.sin(angle - 0.4));
    ctx.lineTo(tx - size * Math.cos(angle + 0.4), ty - size * Math.sin(angle + 0.4));
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  function buildLayout(data) {
    _positions.clear();
    _nodeRing.clear();
    const { nodes, edges } = data;
    const qLower = (data.query || '').toLowerCase();
    const central = nodes.find(n => n.name.toLowerCase() === qLower) || nodes[0];
    if (!central) return false;

    _centralId = central.concept_id;
    const ring1Set = new Set();
    const ring2Set = new Set();

    for (const e of edges) {
      if (e.start_concept_id === _centralId && e.end_concept_id !== _centralId) ring1Set.add(e.end_concept_id);
      if (e.end_concept_id === _centralId && e.start_concept_id !== _centralId) ring1Set.add(e.start_concept_id);
    }
    for (const e of edges) {
      if (ring1Set.has(e.start_concept_id) && e.end_concept_id !== _centralId && !ring1Set.has(e.end_concept_id)) ring2Set.add(e.end_concept_id);
      if (ring1Set.has(e.end_concept_id) && e.start_concept_id !== _centralId && !ring1Set.has(e.start_concept_id)) ring2Set.add(e.start_concept_id);
    }

    const ring1 = [...ring1Set].map(id => nodes.find(n => n.concept_id === id)).filter(Boolean);
    const ring2 = [...ring2Set].map(id => nodes.find(n => n.concept_id === id)).filter(Boolean);
    const n1 = ring1.length;
    const n2 = ring2.length;
    const R1 = n1 === 0 ? 0 : Math.max(160, n1 * NODE_R * 2.8 / (2 * Math.PI));
    const R2 = n2 === 0 ? 0 : Math.max(R1 + 140, n2 * NODE_R2 * 2.8 / (2 * Math.PI));

    _positions.set(_centralId, { wx: 0, wy: 0 });
    _nodeRing.set(_centralId, 0);

    const ring1Angle = new Map();
    ring1.forEach((nd, i) => {
      const angle = (2 * Math.PI * i / Math.max(n1, 1)) - Math.PI / 2;
      ring1Angle.set(nd.concept_id, angle);
      _positions.set(nd.concept_id, { wx: R1 * Math.cos(angle), wy: R1 * Math.sin(angle) });
      _nodeRing.set(nd.concept_id, 1);
    });

    if (n2 > 0) {
      const parents = new Map();
      for (const e of edges) {
        const link = (a, b) => {
          if (ring1Set.has(a) && ring2Set.has(b)) {
            if (!parents.has(b)) parents.set(b, []);
            if (!parents.get(b).includes(a)) parents.get(b).push(a);
          }
        };
        link(e.start_concept_id, e.end_concept_id);
        link(e.end_concept_id, e.start_concept_id);
      }

      const groups = new Map();
      for (const nd2 of ring2) {
        const pList = parents.get(nd2.concept_id) || [];
        const primary = pList[0] ?? ring1[0]?.concept_id;
        if (primary !== undefined) {
          if (!groups.has(primary)) groups.set(primary, []);
          groups.get(primary).push(nd2.concept_id);
        }
      }

      const sectorFrac = 0.82;
      for (const [parentId, children] of groups) {
        const parentAngle = ring1Angle.get(parentId) ?? 0;
        const sliceWidth = (2 * Math.PI / Math.max(n1, 1)) * sectorFrac;
        const nc = children.length;
        children.forEach((cid, j) => {
          const spread = nc === 1 ? 0 : (j / (nc - 1) - 0.5) * sliceWidth;
          const angle = parentAngle + spread;
          _positions.set(cid, { wx: R2 * Math.cos(angle), wy: R2 * Math.sin(angle) });
          _nodeRing.set(cid, 2);
        });
      }
    }

    return true;
  }

  function resetView() {
    view.zoom = 1.0;
    view.pan.x = canvas.width / 2;
    view.pan.y = canvas.height / 2;
  }

  function saveView() {
    sessionStorage.setItem('kg-last-view', JSON.stringify({ zoom: view.zoom, pan: { x: view.pan.x, y: view.pan.y } }));
  }

  let _history = [];
  let _histIdx = -1;
  let _histNaving = false;

  function histPush(term) {
    if (_histNaving) return;
    _history = _history.slice(0, _histIdx + 1);
    if (_history[_history.length - 1] !== term) {
      _history.push(term);
      _histIdx = _history.length - 1;
    }
    sessionStorage.setItem('kg-history', JSON.stringify({ terms: _history, idx: _histIdx }));
    updateHistBtns();
  }

  function updateHistBtns() {
    document.getElementById('g-prev').disabled = _histIdx <= 0;
    document.getElementById('g-next').disabled = _histIdx >= _history.length - 1;
  }

  async function histNav(delta) {
    const newIdx = _histIdx + delta;
    if (newIdx < 0 || newIdx >= _history.length) return;
    _histIdx = newIdx;
    _histNaving = true;
    input.value = _history[_histIdx];
    await doExpand();
    _histNaving = false;
    sessionStorage.setItem('kg-history', JSON.stringify({ terms: _history, idx: _histIdx }));
    updateHistBtns();
  }

  async function doExpand(options = {}) {
    hideAC();
    const term = input.value.trim();
    if (!term) return false;
    const focusNodeId = Number.isFinite(options.focusNodeId) ? options.focusNodeId : null;
    const previousScene = focusNodeId !== null ? _snapshotGraphScene() : null;
    _transition = null;
    try {
      const r = await fetch(`${pfx}/api/expand-by-term?q=${enc(term)}&depth=2`);
      if (!r.ok) return false;
      const data = await r.json();
      if (!data.matched) {
        hint.textContent = `No concept found for "${esc(data.query)}"`;
        hint.style.display = 'flex';
        _graphData = null;
        _clearSelection(false);
        _stopShowcase();
        draw();
        gwBtn.disabled = true;
        return false;
      }
      _graphData = data;
      sessionStorage.setItem('kg-last-term', term);
      histPush(term);
      if (!buildLayout(data)) return false;
      hint.style.display = 'none';
      resetView();
      _restoreSelectionFromState();
      if (previousScene && focusNodeId !== null) {
        _startGraphTransition(previousScene, focusNodeId);
      } else {
        draw();
      }
      gwBtn.disabled = false;
      _updateShowcaseButton();
      return true;
    } catch (err) {
      console.error('expand failed', err);
      _updateShowcaseButton();
      return false;
    }
  }

  let _panning = false;
  let _pendingBlankPan = null;
  let _sidebarResizeDrag = null;
  let _panStart = { x: 0, y: 0 };
  let _panOrigin = { x: 0, y: 0 };

  splitter.addEventListener('mousedown', e => {
    if (!_sidebarOpen) return;
    e.preventDefault();
    _sidebarResizeDrag = { startX: e.clientX, startW: _readSidebarWidth() };
    splitter.classList.add('is-dragging');
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';
  });

  canvas.addEventListener('mousedown', e => {
    if (_transition) return;
    if (e.button === 1) {
      e.preventDefault();
      startPan(e);
      return;
    }
    if (e.button !== 0) return;
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const nodeHit = hitNodeAt(sx, sy);
    if (nodeHit) {
      e.preventDefault();
      handleNodeClick(nodeHit);
      return;
    }
    const edgeHit = hitEdgeLabelAt(sx, sy);
    if (edgeHit) {
      e.preventDefault();
      selectEdge(edgeHit);
      return;
    }
    _pendingBlankPan = {
      startX: e.clientX,
      startY: e.clientY,
      originX: view.pan.x,
      originY: view.pan.y,
      moved: false,
    };
  });

  canvas.addEventListener('mousemove', e => {
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    if (_transition) {
      canvas.style.cursor = 'default';
      return;
    }
    if (_sidebarResizeDrag) {
      const nextW = _sidebarResizeDrag.startW + (e.clientX - _sidebarResizeDrag.startX);
      _applySidebarWidth(nextW);
      return;
    }
    if (_panning) {
      view.pan.x = _panOrigin.x + (e.clientX - _panStart.x);
      view.pan.y = _panOrigin.y + (e.clientY - _panStart.y);
      draw();
    } else if (_pendingBlankPan) {
      const dx = e.clientX - _pendingBlankPan.startX;
      const dy = e.clientY - _pendingBlankPan.startY;
      if (!_pendingBlankPan.moved && Math.hypot(dx, dy) >= _PAN_THRESHOLD) {
        _pendingBlankPan.moved = true;
        _panning = true;
        _panStart = { x: _pendingBlankPan.startX, y: _pendingBlankPan.startY };
        _panOrigin = { x: _pendingBlankPan.originX, y: _pendingBlankPan.originY };
        canvas.style.cursor = 'grabbing';
      }
      if (_panning) {
        view.pan.x = _panOrigin.x + (e.clientX - _panStart.x);
        view.pan.y = _panOrigin.y + (e.clientY - _panStart.y);
        draw();
      }
    } else {
      const nodeHit = hitNodeAt(sx, sy);
      const edgeHit = nodeHit ? null : hitEdgeLabelAt(sx, sy);
      canvas.style.cursor = nodeHit || edgeHit ? 'pointer' : 'grab';
    }
  });

  window.addEventListener('mouseup', () => {
    if (_sidebarResizeDrag) {
      _sidebarResizeDrag = null;
      splitter.classList.remove('is-dragging');
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    }
    if (_panning) saveView();
    if (_pendingBlankPan && !_pendingBlankPan.moved) {
      _clearSelection(false);
      _setSidebarOpen(false);
    }
    _pendingBlankPan = null;
    _panning = false;
    canvas.style.cursor = 'grab';
  });

  let _saveViewTimer = null;
  canvas.addEventListener('wheel', e => {
    if (_transition) return;
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    view.pan.x = mx - (mx - view.pan.x) * factor;
    view.pan.y = my - (my - view.pan.y) * factor;
    view.zoom = Math.max(0.1, Math.min(6, view.zoom * factor));
    draw();
    clearTimeout(_saveViewTimer);
    _saveViewTimer = setTimeout(saveView, 400);
  }, { passive: false });

  function startPan(e) {
    _panning = true;
    _panStart = { x: e.clientX, y: e.clientY };
    _panOrigin = { x: view.pan.x, y: view.pan.y };
    canvas.style.cursor = 'grabbing';
  }

  function _screenToWorld(sx, sy) {
    return { wx: (sx - view.pan.x) / view.zoom, wy: (sy - view.pan.y) / view.zoom };
  }

  function hitNodeAt(sx, sy) {
    const { wx, wy } = _screenToWorld(sx, sy);
    for (const hit of _nodeHits) {
      if (Math.hypot(wx - hit.x, wy - hit.y) <= hit.r) return hit.node;
    }
    return null;
  }

  function hitEdgeLabelAt(sx, sy) {
    const { wx, wy } = _screenToWorld(sx, sy);
    for (const hit of _edgeLabelHits) {
      if (wx >= hit.x && wx <= hit.x + hit.w && wy >= hit.y && wy <= hit.y + hit.h) return hit.edge;
    }
    return null;
  }

  async function handleNodeClick(node) {
    const sameNodeSelected = _selection?.kind === 'node' && _selection.node?.concept_id === node.concept_id;
    const isCentralNode = node.concept_id === _centralId;

    if (sameNodeSelected || isCentralNode) {
      if (sameNodeSelected && _sidebarOpen) {
        _clearSelection(false);
        _setSidebarOpen(false);
        return;
      }
      await selectNode(node);
      return;
    }

    _clearSelection(false);
    _setSidebarOpen(false);
    input.value = node.name;
    await doExpand({ focusNodeId: node.concept_id });
  }

  async function selectNode(node) {
    _selection = { kind: 'node', node };
    _nodeDetail = null;
    _setSidebarOpen(true);
    _setSidebarStatus(`Selected concept "${node.name}".`);
    renderSidebar();
    draw();
    _saveSidebarState();
    if (!node.vocab_id) return;
    try {
      const detail = await apiJson(`${pfx}/api/vocab/${node.vocab_id}`);
      if (_selection?.kind !== 'node' || _selection.node?.concept_id !== node.concept_id) return;
      _nodeDetail = detail;
      renderSidebar();
    } catch (err) {
      if (_selection?.kind !== 'node' || _selection.node?.concept_id !== node.concept_id) return;
      _setSidebarStatus(`Selected concept "${node.name}". Detail lookup failed.`);
      renderSidebar();
    }
  }

  function selectEdge(edge) {
    _selection = { kind: 'edge', edge };
    _nodeDetail = null;
    _setSidebarOpen(true);
    _setSidebarStatus(`Selected connection "${edge.connection_name}".`);
    renderSidebar();
    draw();
    _saveSidebarState();
  }

  function _clearSelection(save = true) {
    _selection = null;
    _nodeDetail = null;
    _setSidebarStatus('Click a node circle or connection label to inspect it.');
    renderSidebar();
    draw();
    if (save) _saveSidebarState();
  }

  function _renderMetaRow(label, valueHtml) {
    return `<div class="kg-meta-row"><div class="kg-meta-label">${esc(label)}</div><div class="kg-meta-value">${valueHtml}</div></div>`;
  }

  function renderSidebar() {
    selectionCard.hidden = !_selection;
    emptySelection.hidden = Boolean(_selection);
    deleteSelectionBtn.disabled = !_selection;
    if (!_selection) {
      selectionPath.textContent = 'No graph item selected';
      selectionType.textContent = '';
      selectionTitle.textContent = '';
      selectionCopy.textContent = '';
      selectionMeta.innerHTML = '';
      deleteSelectionBtn.textContent = 'Delete';
      return;
    }

    if (_selection.kind === 'node') {
      const node = _selection.node;
      const aliases = Array.isArray(_nodeDetail?.aliases) ? _nodeDetail.aliases : [];
      selectionPath.textContent = `Concept ${node.concept_id}`;
      selectionType.textContent = 'Vocab Concept';
      selectionTitle.textContent = _nodeDetail?.term || node.name;
      selectionCopy.textContent = aliases.length
        ? `${aliases.length} alias${aliases.length === 1 ? '' : 'es'} attached to this concept.`
        : 'No additional aliases recorded for this concept.';
      const aliasHtml = aliases.length
        ? `<ol class="kg-alias-list">${aliases.map(alias => `<li>${esc(alias.term)}</li>`).join('')}</ol>`
        : 'None';
      selectionMeta.innerHTML = [
        _renderMetaRow('Concept', esc(String(node.concept_id))),
        _renderMetaRow('Vocab Row', esc(String(node.vocab_id || _nodeDetail?.id || '—'))),
        _renderMetaRow('Aliases', aliasHtml),
      ].join('');
      deleteSelectionBtn.textContent = 'Delete Concept';
      return;
    }

    const edge = _selection.edge;
    selectionPath.textContent = `${edge.start_name} / ${edge.connection_name} / ${edge.end_name}`;
    selectionType.textContent = 'Connection';
    selectionTitle.textContent = edge.connection_name;
    selectionCopy.textContent = `${edge.start_name} → ${edge.end_name}`;
    selectionMeta.innerHTML = [
      _renderMetaRow('Start', `${esc(edge.start_name)} <span class="meta">(#${esc(String(edge.start_concept_id))})</span>`),
      _renderMetaRow('Connection', `${esc(edge.connection_name)} <span class="meta">(#${esc(String(edge.connection_concept_id))})</span>`),
      _renderMetaRow('End', `${esc(edge.end_name)} <span class="meta">(#${esc(String(edge.end_concept_id))})</span>`),
      _renderMetaRow('State', esc(edge.state_label || String(edge.state ?? ''))),
      _renderMetaRow('Score', esc(String(edge.score ?? '0'))),
    ].join('');
    deleteSelectionBtn.textContent = 'Delete Connection';
  }

  function _deleteRecoveryTerms(deletedConceptId, deletedName) {
    const out = [];
    const seen = new Set();
    const addTerm = (value) => {
      const term = String(value || '').trim();
      if (!term) return;
      const key = term.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      out.push(term);
    };

    seen.add(String(deletedName || '').trim().toLowerCase());

    const nodes = (_graphData?.nodes || []).filter((node) => node.concept_id !== deletedConceptId);
    const origin = _positions.get(deletedConceptId) || _positions.get(_centralId) || null;
    const scoredNodes = nodes.map((node) => {
      const pos = _positions.get(node.concept_id);
      const distance = origin && pos ? Math.hypot(pos.wx - origin.wx, pos.wy - origin.wy) : Number.POSITIVE_INFINITY;
      return { node, distance };
    }).sort((a, b) => a.distance - b.distance || a.node.name.localeCompare(b.node.name));

    for (const entry of scoredNodes) addTerm(entry.node.name);
    for (let i = _history.length - 1; i >= 0; i -= 1) addTerm(_history[i]);
    return out;
  }

  async function _recoverDeletedNode(node, noticePrefix) {
    _clearSelection(false);
    _setSidebarOpen(false);

    const recoveryTerms = _deleteRecoveryTerms(node.concept_id, node.name);
    for (const term of recoveryTerms) {
      input.value = term;
      const ok = await doExpand();
      if (ok) {
        _setSidebarStatus(`${noticePrefix} Showing "${term}".`);
        _saveSidebarState();
        return true;
      }
    }

    input.value = '';
    _graphData = null;
    hint.textContent = `${noticePrefix} Search for another concept to continue.`;
    hint.style.display = 'flex';
    gwBtn.disabled = true;
    draw();
    _setSidebarStatus(noticePrefix);
    _saveSidebarState();
    return false;
  }

  async function deleteSelection() {
    if (!_selection) return;
    try {
      if (_selection.kind === 'node') {
        const node = _selection.node;
        if (!window.confirm(`Delete concept "${node.name}" and all touching relations?`)) return;
        const result = await apiJson(`${pfx}/api/concepts/${node.concept_id}`, { method: 'DELETE' });
        await _recoverDeletedNode(
          node,
          `Deleted concept "${node.name}" (${result.deleted_relations} relation${result.deleted_relations === 1 ? '' : 's'} removed).`
        );
      } else {
        const edge = _selection.edge;
        if (!window.confirm(`Delete connection "${edge.start_name} / ${edge.connection_name} / ${edge.end_name}"?`)) return;
        await apiJson(`${pfx}/api/connections`, {
          method: 'DELETE',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            start_concept_id: edge.start_concept_id,
            connection_concept_id: edge.connection_concept_id,
            end_concept_id: edge.end_concept_id,
          }),
        });
        _clearSelection(false);
        _setSidebarStatus(`Deleted connection "${edge.connection_name}".`);
        _setSidebarOpen(false);
        await doExpand();
        _saveSidebarState();
        return;
      }
    } catch (err) {
      console.error('delete failed', err);
      if (_selection?.kind === 'node' && /not found|http 404/i.test(String(err.message || ''))) {
        await _recoverDeletedNode(_selection.node, `Concept "${_selection.node.name}" is no longer present.`);
        return;
      }
      _setSidebarStatus(err.message || 'Delete failed.');
    }
  }

  function _restoreSelectionFromState() {
    const saved = _loadSidebarState();
    if (!saved?.selection || !_graphData) {
      renderSidebar();
      return;
    }
    const sel = saved.selection;
    if (sel.kind === 'node') {
      const node = (_graphData.nodes || []).find(n => n.concept_id === sel.concept_id);
      if (node) {
        selectNode(node);
        return;
      }
    } else {
      const edge = (_graphData.edges || []).find(item => _sameEdge(item, sel));
      if (edge) {
        selectEdge(edge);
        return;
      }
    }
    _clearSelection(false);
  }

  let _acTimer = null;
  input.addEventListener('input', () => {
    clearTimeout(_acTimer);
    const q = input.value.trim();
    if (!q) gwBtn.disabled = true;
    if (q.length < 2) {
      hideAC();
      return;
    }
    _acTimer = setTimeout(() => loadAC(q), 180);
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      hideAC();
      doExpand();
    }
    if (e.key === 'Escape') hideAC();
  });

  btn.addEventListener('click', doExpand);
  document.getElementById('g-prev').addEventListener('click', () => histNav(-1));
  document.getElementById('g-next').addEventListener('click', () => histNav(1));
  showcaseBtn.addEventListener('click', _toggleShowcase);
  gwBtn.addEventListener('click', () => {
    const term = input.value.trim();
    if (!term) return;
    window.location.href = '/ui?q=' + enc(term);
  });
  closeSidebarBtn.addEventListener('click', () => _setSidebarOpen(false));
  deleteSelectionBtn.addEventListener('click', deleteSelection);

  document.addEventListener('pointerdown', e => {
    if (!e.target.closest('.kg-search-row')) hideAC();
  });

  async function loadAC(q) {
    try {
      const r = await fetch(`${pfx}/api/search?q=${enc(q)}&limit=12`);
      if (!r.ok) {
        hideAC();
        return;
      }
      const items = await r.json();
      if (!items.length) {
        hideAC();
        return;
      }
      ac.innerHTML = '';
      for (const it of items) {
        const d = document.createElement('div');
        d.className = 'kg-ac-item';
        d.textContent = it.term;
        d.addEventListener('pointerdown', ev => {
          ev.preventDefault();
          input.value = it.term;
          hideAC();
          doExpand();
        });
        ac.appendChild(d);
      }
      ac.style.display = 'block';
    } catch {
      hideAC();
    }
  }

  function hideAC() {
    ac.style.display = 'none';
  }

  async function apiJson(url, options = {}) {
    const response = await fetch(url, options);
    if (response.ok) {
      if (response.status === 204) return null;
      return response.json();
    }
    let message = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      if (payload?.detail) message = String(payload.detail);
    } catch (_) {}
    throw new Error(message);
  }

  (function restoreState() {
    _updateShowcaseButton();
    const savedState = _loadSidebarState();
    _setSidebarOpen(Boolean(savedState?.open));
    const savedHist = sessionStorage.getItem('kg-history');
    if (savedHist) {
      try {
        const h = JSON.parse(savedHist);
        _history = h.terms || [];
        _histIdx = typeof h.idx === 'number' ? h.idx : _history.length - 1;
      } catch (_) {}
    }
    updateHistBtns();
    const savedTerm = sessionStorage.getItem('kg-last-term');
    if (savedTerm) {
      input.value = savedTerm;
      _histNaving = true;
      doExpand().then(() => {
        _histNaving = false;
        const savedView = sessionStorage.getItem('kg-last-view');
        if (savedView) {
          try {
            const v = JSON.parse(savedView);
            view.zoom = v.zoom;
            view.pan.x = v.pan.x;
            view.pan.y = v.pan.y;
            draw();
          } catch (_) {}
        }
      });
    } else {
      renderSidebar();
    }
  })();

  function enc(s) { return encodeURIComponent(s); }
  function esc(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
})();
