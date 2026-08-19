import { initAppBar, initTopbar } from "/ui-elements/assets/js/chrome.js";
import { initWorkspaceLayouts, resetWorkspaceLayout } from "/ui-elements/assets/js/workspace.js";

// ============================================================
// KoreAgent Web UI - app.js
// Vanilla JS - no build step, no dependencies.
// ============================================================

// ====================================================================================================
// MARK: CONFIG
// ====================================================================================================
const API_BASE          = "";           // same origin
const SESSION_STORAGE_KEY   = "maf.activeSession";
const INPUT_DRAFT_KEY       = "maf.inputDraft";
const WRAP_STATE_KEY        = "maf.wrapState";
const ACTIVE_RUN_STORAGE_KEY = "maf.activeRun";
let   _sessionId        = _restoreSessionId();  // mutable: /chat resume changes this
const POLL_OLLAMA_MS    = 10_000;
const POLL_QUEUE_MS     = 3_000;
const POLL_LATEST_LOG_MS = 15_000;   // fallback only - /logs/stream SSE handles normal case
const MAX_PENDING_PROMPTS = 5;
const MAX_LOG_LINES_LIVE = 500;
const MAX_CHAT_MESSAGES = 200;

// CSS class name constants used by panel and tag state toggles.
const CSS_NOWRAP       = "kcui-panel-body--nowrap";
const CSS_LIVE_FOLLOW  = "kcui-panel-body--live-follow";
const CSS_WRAP_ACTIVE  = "is-on";
const CSS_TAG_INACTIVE = "kcui-tag--inactive";

// All registered slash commands - used for command-name tab completion.
const _ALL_COMMANDS = [
    "/help", "/llmserver", "/llmserverconfig", "/rounds", "/timeout",
    "/stopmodel", "/stoprun",
    "/clearmemory", "/reskill", "/sandbox", "/tools",
    "/deletelogs",
    "/version", "/defaults", "/chat", "/workspace",
    "/comms",
];

// Sub-commands for /chat.
const _CHAT_SUBS = ["new", "name", "list", "resume", "resumecopy", "park", "delete", "info"];

// Sub-commands for /llmserverconfig.
const _LLMSERVERCFG_SUBS = ["model", "ctx", "max_predict", "cpugpu"];
const _LLMSERVER_SUBS    = ["ollama", "lmstudio"];
const _LLMSERVER_CONFIGS = ["forcecpu", "forcegpu", "autogpu"];
const _SUGGEST_HINTS     = {
    "/comms":   "Configure or control KoreComms delivery for this chat",
    cpugpu:    "Set Ollama CPU/GPU model placement",
    max_predict: "Use /llmserverconfig max_predict <count>; use /llmserverconfig max_predict to reset to 1024",
    connection: "Pause, resume, or explicitly publish KoreComms output",
    delivery:   "Bind this chat's agent output to a KoreComms connection",
    forcecpu:  "No GPU: run the model entirely on CPU",
    forcegpu:  "Request all model layers on the GPU",
    autogpu:   "Let Ollama choose CPU/GPU placement",
    bind:       "Create or update the delivery target for this chat",
    groups:     "List, inspect, or re-evaluate named tool groups",
    show:       "Show the member tools in a named tool group",
    reevaluate: "Build complete named tool groups from the live tool inventory",
    pause:      "Stop automatic copying of agent output to the connection",
    resume:     "Resume automatic copying of pending and future agent output",
    publishprevious: "Send the latest eligible agent output once, even while paused",
    "--connection":  "Connection name; required for an individual recipient or an SFTP file",
    "--to":          "Single email recipient",
    "--to-list":     "Named KoreComms distribution list",
    "--subject":     "Delivery subject; required when binding",
    "--chat":        "Target another chat instead of the current chat",
    "--startpaused": "Create the delivery binding paused; use connection resume to enable copying",
    ollama:    "Use an Ollama server",
    lmstudio:  "Use an LM Studio server",
};

// Sub-commands for /tools.
const _TOOLS_SUBS = ["active", "all", "groups"];
const _TOOL_GROUPS_SUBS = ["show", "reevaluate"];

// Sub-commands and options for /comms.
const _COMMS_SUBS             = ["delivery", "connection"];
const _COMMS_DELIVERY_SUBS    = ["bind"];
const _COMMS_CONNECTION_SUBS  = ["pause", "resume", "publishprevious"];
const _COMMS_BIND_OPTIONS     = ["--connection", "--to", "--to-list", "--subject", "--chat", "--startpaused"];
const _COMMS_CONNECTION_OPTIONS = ["--chat"];
const _WORKSPACE_SUBS      = ["clear"];

// Pre-compiled log line classification patterns.
const RE_LOG_TOOL_ROUND = /^TOOL ROUND\s+\d+/i;
const RE_LOG_ERROR      = /error|exception|failed/i;
const RE_LOG_OK         = /completed|success/i;

// ====================================================================================================
// MARK: STATE
// ====================================================================================================
let _logLines       = [];
let _logEventSource = null;
let _inputHistory   = [];     // loaded per conversation from server on init or session switch
let _historyIdx        = -1;     // -1 = not browsing history
let _historyDraft      = null;   // unsent text preserved while browsing input history
let _ollamaReachable   = true;   // updated by refreshOllamaStatus; used in submitPrompt
let _currentLogPath       = "";
let _logScrollCtl      = null;
let _chatScrollCtl     = null;
let _logLineLimit      = MAX_LOG_LINES_LIVE;
let _sessionTitle      = "";
let _thinkingTimer     = null;

// Tab-completion state.
let _completions  = { sessions: [], models: [] };
let _suggestItems = [];   // current filtered candidate list
let _suggestIdx   = -1;   // highlighted row index (-1 = none)
let _suggestBase  = "";   // portion of input before the completion token

// ====================================================================================================
// MARK: DOM REFS
// ====================================================================================================
const $ = id => document.getElementById(id);

const dom = {
    ollamaHost:   () => $("ollama-host"),
    ollamaModel:  () => $("ollama-model"),
    ollamaCtx:    () => $("ollama-ctx"),
    log:          () => $("log-body"),
    pendingPromptsPanel: () => $("panel-pending-prompts"),
    pendingPromptsCount: () => $("pending-prompts-count"),
    pendingPromptsList:  () => $("pending-prompts-list"),
    chat:         () => $("chat-body"),
    chatTitle:    () => $("chat-panel-title"),
    input:        () => $("chat-input"),
    sendBtn:      () => $("send-btn"),
};

function _restoreSessionId() {
    try {
        const raw = sessionStorage.getItem(SESSION_STORAGE_KEY);
        if (raw) {
            const saved = JSON.parse(raw);
            if (saved && typeof saved.sessionId === "string" && saved.sessionId.trim()) {
                return saved.sessionId.trim();
            }
        }
    } catch (_) { /* ignore */ }
    return "web_" + Date.now();
}

function _persistActiveSession() {
    try {
        sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify({
            sessionId: _sessionId,
            title: _sessionTitle,
        }));
    } catch (_) { /* ignore */ }
}

function _saveInputDraft(text) {
    try { sessionStorage.setItem(INPUT_DRAFT_KEY, text); } catch (_) { /* ignore */ }
}

function _restoreInputDraft() {
    try { return sessionStorage.getItem(INPUT_DRAFT_KEY) || ""; } catch (_) { return ""; }
}

function _clearInputDraft() {
    try { sessionStorage.removeItem(INPUT_DRAFT_KEY); } catch (_) { /* ignore */ }
}

function _loadActiveRun() {
    try {
        const raw = localStorage.getItem(ACTIVE_RUN_STORAGE_KEY);
        const run = raw ? JSON.parse(raw) : null;
        return run && typeof run.runId === "string" && typeof run.sessionId === "string" ? run : null;
    } catch (_) {
        return null;
    }
}

function _saveActiveRun(run) {
    try { localStorage.setItem(ACTIVE_RUN_STORAGE_KEY, JSON.stringify(run)); } catch (_) { /* ignore */ }
}

function _clearActiveRun(runId) {
    const run = _loadActiveRun();
    if (!run || run.runId !== runId) return;
    try { localStorage.removeItem(ACTIVE_RUN_STORAGE_KEY); } catch (_) { /* ignore */ }
}

function _turnMetaStorageKey(sessionId) {
    return "maf_turn_meta_" + sessionId;
}

function _turnMetaKey(prompt, response) {
    return String(prompt || "") + "\u0000" + String(response || "");
}

function _loadTurnMeta(sessionId) {
    try {
        const raw = localStorage.getItem(_turnMetaStorageKey(sessionId));
        const stored = raw ? JSON.parse(raw) : {};
        return stored && typeof stored === "object" ? stored : {};
    } catch (_) {
        return {};
    }
}

function _saveTurnMeta(sessionId, prompt, response, meta) {
    if (!meta) return;
    const stored = _loadTurnMeta(sessionId);
    stored[_turnMetaKey(prompt, response)] = meta;
    try { localStorage.setItem(_turnMetaStorageKey(sessionId), JSON.stringify(stored)); } catch (_) { /* ignore */ }
}

function _getTurnMeta(sessionId, prompt, response) {
    return _loadTurnMeta(sessionId)[_turnMetaKey(prompt, response)] || "";
}

function _formatSavedTelemetry(telemetry) {
    if (!telemetry || typeof telemetry !== "object") return "";
    const tokens = Number(telemetry.context_tokens) || 0;
    const tps    = String(telemetry.tokens_per_second || "0");
    const elapsed = _formatElapsed(telemetry.elapsed_ms);
    return tokens
        ? tokens.toLocaleString() + " ctx" + (tps !== "0" ? " | " + tps + " tok/s" : "") + " | " + elapsed
        : elapsed;
}

function _turnMetaText(sessionId, prompt, assistantTurn) {
    return _formatSavedTelemetry(assistantTurn?.telemetry)
        || _getTurnMeta(sessionId, prompt, assistantTurn?.content);
}

function _formatElapsed(elapsedMs) {
    const seconds = Math.max(0, Number(elapsedMs) || 0) / 1000;
    return seconds < 60 ? seconds.toFixed(1) + "s" : Math.floor(seconds / 60) + "m " + Math.floor(seconds % 60) + "s";
}

function _refreshThinkingTimers() {
    const now = Date.now();
    dom.chat().querySelectorAll(".chat-thinking[data-started-at-ms]").forEach(el => {
        el.textContent = "thinking... " + _formatElapsed(now - Number(el.dataset.startedAtMs));
    });
}

function _restoreSessionUiState() {
    try {
        const raw = sessionStorage.getItem(SESSION_STORAGE_KEY);
        if (!raw) return;
        const saved = JSON.parse(raw);
        if (saved && typeof saved.title === "string") {
            _setChatPanelTitle(saved.title, { persist: false });
        }
    } catch (_) { /* ignore */ }
}

function _setChatPanelTitle(title, { persist = true } = {}) {
    _sessionTitle = String(title || "").trim();
    dom.chatTitle().textContent = _sessionTitle;
    if (persist) _persistActiveSession();
}

function _resolveSessionTitle(sessionId, title) {
    const resolved = String(title || "").trim();
    if (resolved) return resolved;
    return String(sessionId || "").trim();
}

function _consumeRequestedSession() {
    try {
        const url = new URL(window.location.href);
        const sessionId = (url.searchParams.get("session_id") || "").trim();
        const name = (url.searchParams.get("name") || "").trim();
        if (!sessionId) return null;
        url.searchParams.delete("session_id");
        url.searchParams.delete("name");
        window.history.replaceState({}, "", url.toString());
        return { sessionId, name };
    } catch (_) {
        return null;
    }
}

// ====================================================================================================
// MARK: PANEL AUTO-FOLLOW
// ====================================================================================================

function _createPanelScrollController(panel, {
    threshold = 4,
    initialLive = true,
    allowAutoResume = true,
    onLiveChange = null,
} = {}) {
    const state = {
        panel,
        threshold,
        live: initialLive,
        rafId: null,
        suppressScrollEvent: false,
        resizeObserver: null,
    };

    function _notify() {
        if (typeof onLiveChange === "function") onLiveChange(state.live);
    }

    function isNearBottom() {
        return (panel.scrollHeight - panel.scrollTop - panel.clientHeight) <= state.threshold;
    }

    function _flushFollow() {
        state.rafId = null;
        if (!state.live) return;
        state.suppressScrollEvent = true;
        panel.scrollTop = Math.max(0, panel.scrollHeight - panel.clientHeight);
        requestAnimationFrame(() => {
            state.suppressScrollEvent = false;
        });
    }

    function followNow() {
        if (!state.live) return;
        if (state.rafId !== null) {
            cancelAnimationFrame(state.rafId);
            state.rafId = null;
        }
        _flushFollow();
    }

    function followSoon() {
        if (!state.live) return;
        if (state.rafId !== null) return;
        state.rafId = requestAnimationFrame(_flushFollow);
    }

    function setLive(nextLive, { snap = false } = {}) {
        const normalized = !!nextLive;
        if (state.live === normalized) {
            if (normalized && snap) followNow();
            return;
        }
        state.live = normalized;
        _notify();
        if (state.live && snap) followNow();
    }

    function runWithoutScrollTracking(callback) {
        state.suppressScrollEvent = true;
        try {
            callback();
        } finally {
            requestAnimationFrame(() => {
                state.suppressScrollEvent = false;
            });
        }
    }

    panel.addEventListener("scroll", () => {
        if (state.suppressScrollEvent) return;
        if (isNearBottom()) {
            if (allowAutoResume) setLive(true);
        }
    });

    panel.addEventListener("wheel", (e) => {
        if (e.deltaY < 0) setLive(false);
        if (allowAutoResume && e.deltaY > 0 && isNearBottom()) setLive(true);
    }, { passive: true });

    panel.addEventListener("pointerdown", (e) => {
        const rect = panel.getBoundingClientRect();
        if (e.clientX >= rect.left + panel.clientWidth) {
            setLive(false);
        }
    });

    if (window.ResizeObserver) {
        state.resizeObserver = new ResizeObserver(() => {
            if (state.live) followNow();
        });
        state.resizeObserver.observe(panel);
    }

    _notify();
    if (state.live) followSoon();
    return {
        get live() { return state.live; },
        setLive,
        followSoon,
        followNow,
        isNearBottom,
        runWithoutScrollTracking,
    };
}

// ====================================================================================================
// MARK: FETCH HELPERS
// ====================================================================================================

async function apiFetch(path, opts) {
    try {
        const res = await fetch(API_BASE + path, opts);
        if (!res.ok) {
            const txt = await res.text().catch(() => "");
            console.warn("API error", path, res.status, txt);
            return null;
        }
        return await res.json();
    } catch (e) {
        console.warn("fetch failed", path, e.message);
        return null;
    }
}

// ====================================================================================================
// MARK: OLLAMA STATUS
// ====================================================================================================

async function refreshOllamaStatus() {
    const data = await apiFetch("/status/ollama");
    if (!data) {
        dom.ollamaHost().textContent  = "unreachable";
        dom.ollamaModel().textContent = "";
        dom.ollamaCtx().textContent   = "";
        _ollamaReachable = false;
        return;
    }
    // Update text BEFORE dot so the two are never mismatched.
    const backend   = data.backend || "ollama";
    const isLMStudio = backend === "lmstudio";
    const rows  = data.rows || [];
    const first = rows[0] || {};
    // For Ollama: prefer the running model name from `ollama ps`.
    // For LM Studio: `ollama ps` is unavailable; use the configured model name.
    const modelName = isLMStudio ? (data.model || "") : ((first.name || "").trim() || data.model || "");
    // For LM Studio the context window is set inside the LM Studio UI and cannot
    // be read via API, so label it "local ctx" to make the distinction clear.
    const ctxLabel  = isLMStudio ? "local ctx" : "ctx";
    const ctxVal    = data.num_ctx ? data.num_ctx.toLocaleString() + " " + ctxLabel : "";
    dom.ollamaHost().textContent  = (data.host || "") + " (" + backend + ")";
    dom.ollamaModel().textContent = modelName;
    dom.ollamaCtx().textContent   = ctxVal;
    _ollamaReachable = true;
}

// ====================================================================================================
// MARK: QUEUE STATUS
// ====================================================================================================

async function refreshQueue() {
    const data = await apiFetch("/queue");
    if (!data) return;
    _renderPendingPrompts(data);
    if (data.pending_switch) {
        _applySessionSwitch(data.pending_switch.session_id, data.pending_switch.name || "");
    }
}

function _applySessionSwitch(sessionId, name) {
    _sessionId = sessionId;
    const label = _resolveSessionTitle(sessionId, name);
    _setChatPanelTitle(label);
    clearChatPanel();
    if (label) {
        appendChatMessage("agent", "\u2500\u2500\u2500 Session: " + label + " \u2500\u2500\u2500");
    }
    _loadSessionHistory(sessionId);
    _loadHistory();
    _loadCompletions();
}

function _renderPendingPrompts(queueData) {
    const panel = dom.pendingPromptsPanel();
    const list  = dom.pendingPromptsList();
    const count = dom.pendingPromptsCount();
    if (!panel || !list || !count) return;

    const pending = (queueData.next_prompts || [])
        .filter(item => item.state === "pending")
        .slice(0, MAX_PENDING_PROMPTS);
    const pendingCount = Number(queueData.pending_count || 0);
    panel.hidden = pendingCount === 0;
    count.textContent = String(pendingCount);
    list.innerHTML = "";
    if (pendingCount === 0) return;

    for (const item of pending) {
        const row = document.createElement("div");
        row.className   = "pending-prompt";
        row.textContent = String(item.label || item.name || "").replace(/\s+/g, " ").trim();
        row.title       = row.textContent;
        list.appendChild(row);
    }
}

// ====================================================================================================
// MARK: PANEL SPLITTERS
// ====================================================================================================
function resetLayout() {
    resetWorkspaceLayout("koreagent-main-v1");
}

function initSplitters() {
    initWorkspaceLayouts();
}


// ====================================================================================================
// MARK: WRAP TOGGLE
// ====================================================================================================

function toggleWrap(bodyId, btnId) {
    const body = $(bodyId);
    const btn  = $(btnId);
    if (!body || !btn) return;
    const ctl = body === dom.log() ? _logScrollCtl : (body === dom.chat() ? _chatScrollCtl : null);
    const wasLive = ctl ? ctl.live : null;

    const applyToggle = () => {
        // Capture anchor before reflow: first child whose bottom edge meets the panel midpoint.
        const bodyRect = body.getBoundingClientRect();
        const midY     = bodyRect.top + bodyRect.height / 2;
        let anchor     = null;
        for (const child of body.children) {
            if (child.getBoundingClientRect().bottom >= midY) { anchor = child; break; }
        }
        const anchorTopBefore = anchor ? anchor.getBoundingClientRect().top : null;

        // Toggle class - triggers browser reflow.
        const nowrapOn = body.classList.toggle(CSS_NOWRAP);
        btn.classList.toggle(CSS_WRAP_ACTIVE, !nowrapOn);

        // After reflow the anchor may have moved in viewport coords (content height changed).
        // Compensate by exactly that delta so the anchor stays at the same screen position.
        if (anchor !== null && anchorTopBefore !== null) {
            const delta = anchor.getBoundingClientRect().top - anchorTopBefore;
            if (delta !== 0) {
                body.scrollTop += delta;
            }
        }
    };

    if (ctl) {
        ctl.runWithoutScrollTracking(applyToggle);
        if (wasLive) ctl.followSoon();
        _saveWrapState();
        return;
    }

    applyToggle();
    _saveWrapState();
}

function _saveWrapState() {
    try {
        localStorage.setItem(WRAP_STATE_KEY, JSON.stringify({
            log:  !$('log-body')?.classList.contains(CSS_NOWRAP),
            chat: !$('chat-body')?.classList.contains(CSS_NOWRAP),
        }));
    } catch (_) { /* ignore */ }
}

function _restoreWrapState() {
    try {
        const raw = localStorage.getItem(WRAP_STATE_KEY);
        if (!raw) return;
        const saved = JSON.parse(raw);
        if (saved.log)  { $('log-body')?.classList.remove(CSS_NOWRAP);  $('wrap-btn-log')?.classList.add(CSS_WRAP_ACTIVE); }
        if (saved.chat) { $('chat-body')?.classList.remove(CSS_NOWRAP); $('wrap-btn-chat')?.classList.add(CSS_WRAP_ACTIVE); }
    } catch (_) { /* ignore */ }
}

// ====================================================================================================
// MARK: LOG STREAM (SSE)
// ====================================================================================================

let _prevLogWasSep = false;

function _logLineClass(text) {
    if (!text) return "";
    const t = text.trim();
    if (t.startsWith("=") && t.endsWith("=")) return "log-sep";
    if (_prevLogWasSep) {
        if (RE_LOG_TOOL_ROUND.test(t)) return "log-tool-round";
        return "log-title";
    }
    if (t.startsWith("[progress]"))            return "log-progress";
    if (t.startsWith("[thinking]") || t.startsWith("[/thinking]")) return "log-thinking";
    if (t.includes("[SCHEDULER]"))             return "sched";
    if (RE_LOG_ERROR.test(t))                  return "error";
    if (RE_LOG_OK.test(t))                     return "success";
    return "";
}

function appendLogLine(text) {
    const el    = dom.log();
    const div   = document.createElement("div");
    const t     = text ? text.trim() : "";
    div.className = "log-line " + _logLineClass(text);
    _prevLogWasSep = (t.startsWith("=") && t.endsWith("="));
    div.textContent = text;
    el.appendChild(div);
    _logLines.push(div);
    // Trim excess only for aggregate live-stream mode; specific file views keep the full file.
    while (_logLineLimit > 0 && _logLines.length > _logLineLimit) {
        const old = _logLines.shift();
        old.remove();
    }
    if (_logScrollCtl) _logScrollCtl.followSoon();
}

function clearLogLines() {
    _logLines = [];
    _prevLogWasSep = false;
    dom.log().innerHTML = "";
}

function _displayLogPath(path) {
    if (!path) return "";
    const normalized = path.replace(/\\/g, "/");
    return normalized.split("/").pop();
}

function _setLogPanelTitle(path) {
    const titleEl = $("log-panel-title");
    if (!titleEl) return;
    const displayPath = _displayLogPath(path);
    titleEl.textContent = displayPath ? "Log: " + displayPath : "Log";
}

function _setChatMessageMeta(wrap, meta) {
    if (!wrap || !meta) return;
    let metaEl = wrap.querySelector(".msg-meta");
    if (!metaEl) {
        metaEl = document.createElement("div");
        metaEl.className = "msg-meta";
        wrap.appendChild(metaEl);
    }
    metaEl.textContent = meta;
}

function startLogStream() {
    if (_logEventSource) _logEventSource.close();
    _currentLogPath = "";
    _logLineLimit = MAX_LOG_LINES_LIVE;
    _logEventSource = new EventSource(API_BASE + "/logs/stream");
    _logEventSource.onmessage = e => {
        try {
            const data = JSON.parse(e.data);
            // When the active log file changes, switch to tailing that file directly.
            if (data.path && data.path !== _currentLogPath && _logScrollCtl && _logScrollCtl.live) {
                _switchLogStream(data.path);
                return;
            }
            if (data.path) {
                _currentLogPath = data.path;
                _setLogPanelTitle(data.path);
            }
            if (data.text !== undefined) appendLogLine(data.text);
        } catch { appendLogLine(e.data); }
    };
    _logEventSource.onerror = () => {
        // Reconnect after a short wait.
        setTimeout(startLogStream, 3000);
    };
}

// ----------------------------------------------------------------------------------------------------

function _switchLogStream(path) {
    if (!path) return;
    _currentLogPath = path;
    _logLineLimit = 0;
    _setLogPanelTitle(path);

    clearLogLines();
    if (_logEventSource) {
        _logEventSource.close();
        _logEventSource = null;
    }
    _logEventSource = new EventSource(API_BASE + "/logs/file?path=" + encodeURIComponent(path));
    _logEventSource.onmessage = e => {
        try {
            const data = JSON.parse(e.data);
            if (data.path) {
                _currentLogPath = data.path;
                _setLogPanelTitle(data.path);
            }
            if (data.text !== undefined) appendLogLine(data.text);
        } catch { appendLogLine(e.data); }
    };
    _logEventSource.onerror = () => {
        // Let the latest-log poller reopen the active file if this connection drops.
        _currentLogPath = "";
    };
}

async function refreshLatestLogFile() {
    if (!_logScrollCtl || !_logScrollCtl.live) return;
    const data = await apiFetch("/logs/latest");
    if (!data || !data.path) return;
    if (data.path === _currentLogPath) return;
    _switchLogStream(data.path);
}

// ----------------------------------------------------------------------------------------------------

function _setLiveBtn(on) {
    const btn = $("log-btn-live");
    const body = dom.log();
    if (!btn) return;
    btn.classList.toggle(CSS_WRAP_ACTIVE, on);
    if (body) body.classList.toggle(CSS_LIVE_FOLLOW, on);
}

function toggleLogLive() {
    if (!_logScrollCtl) return;
    const nextLive = !_logScrollCtl.live;
    _logScrollCtl.setLive(nextLive, { snap: nextLive });
    if (nextLive) refreshLatestLogFile();
}

// ----------------------------------------------------------------------------------------------------

function _updateSandboxBtn(sandboxOn) {
    const btn = $('sandbox-btn');
    if (!btn) return;
    if (sandboxOn) {
        btn.textContent = "sandbox on";
        btn.classList.remove("sandbox-off");
        btn.classList.add("sandbox-on");
    } else {
        btn.textContent = "sandbox off";
        btn.classList.remove("sandbox-on");
        btn.classList.add("sandbox-off");
    }
}

async function toggleSandbox() {
    const current = await apiFetch("/settings/sandbox");
    if (!current) return;
    const next = !current.sandbox;
    const result = await apiFetch("/settings/sandbox?enabled=" + next, { method: "POST" });
    if (result) _updateSandboxBtn(result.sandbox);
}

async function _initSandboxBtn() {
    const data = await apiFetch("/settings/sandbox");
    if (data) _updateSandboxBtn(data.sandbox);
}

// ----------------------------------------------------------------------------------------------------

function _updateWebSkillsBtn(webOn) {
    const btn = $('webskills-btn');
    if (!btn) return;
    if (webOn) {
        btn.textContent = "web on";
        btn.classList.remove("webskills-off");
        btn.classList.add("webskills-on");
    } else {
        btn.textContent = "web off";
        btn.classList.remove("webskills-on");
        btn.classList.add("webskills-off");
    }
}

async function toggleWebSkills() {
    const current = await apiFetch("/settings/webskills");
    if (!current) return;
    const next = !current.webskills;
    const result = await apiFetch("/settings/webskills?enabled=" + next, { method: "POST" });
    if (result) _updateWebSkillsBtn(result.webskills);
}

async function _initWebSkillsBtn() {
    const data = await apiFetch("/settings/webskills");
    if (data) _updateWebSkillsBtn(data.webskills);
}

// ----------------------------------------------------------------------------------------------------

function _updateDirectBtn(directOn) {
    const btn = $('direct-btn');
    if (!btn) return;
    if (directOn) {
        btn.textContent = "LLM-DIRECT on";
        btn.classList.remove("direct-off");
        btn.classList.add("direct-on");
    } else {
        btn.textContent = "LLM-DIRECT";
        btn.classList.remove("direct-on");
        btn.classList.add("direct-off");
    }
    // Grey out settings that don't apply in direct mode.
    $('sandbox-btn')?.classList.toggle(CSS_TAG_INACTIVE, directOn);
    $('webskills-btn')?.classList.toggle(CSS_TAG_INACTIVE, directOn);
}

async function toggleDirect() {
    const current = await apiFetch("/settings/llmdirect");
    if (!current) return;
    const next = !current.llmdirect;
    const result = await apiFetch("/settings/llmdirect?enabled=" + next, { method: "POST" });
    if (result) _updateDirectBtn(result.llmdirect);
}

async function _initDirectBtn() {
    const data = await apiFetch("/settings/llmdirect");
    if (data) _updateDirectBtn(data.llmdirect);
}

// ----------------------------------------------------------------------------------------------------

async function logNavStep(delta) {
    // delta: -1 = older (up), +1 = newer (down).
    const data = await apiFetch("/logs");
    if (!data || !data.log_dirs) return;

    // Flatten all files into a single chronological list (oldest first).
    const allFiles = [];
    const dirs = data.log_dirs.slice().reverse();   // /logs returns newest-first; reverse to oldest-first
    for (const d of dirs) {
        const files = d.files.slice().reverse();    // files also newest-first within a dir
        for (const f of files) {
            allFiles.push(d.date + "/" + f);
        }
    }

    // Find current position by matching the tail of _currentLogPath.
    const curTail = _currentLogPath.replace(/\\/g, "/").split("/logs/").pop() || "";
    let idx = allFiles.findIndex(p => p === curTail);
    if (idx < 0) idx = allFiles.length - 1;  // default to newest if unknown

    const next = allFiles[idx + delta];
    if (!next) return;  // already at boundary

    // Navigating away from live stream pauses follow mode.
    if (_logScrollCtl && _logScrollCtl.live) {
        _logScrollCtl.setLive(false);
    }

    const logsDir = _currentLogPath.replace(/\\/g, "/").split("/logs/")[0] + "/logs/";
    _switchLogStream(logsDir + next);
}

// ====================================================================================================
// MARK: CHAT
// ====================================================================================================

function appendChatMessage(role, text, meta, runId = "") {
    const el    = dom.chat();
    const wrap  = document.createElement("div");
    wrap.className = "chat-msg " + role;
    if (runId) wrap.setAttribute("data-run-id", runId);

    const label = document.createElement("div");
    label.className = "msg-role";
    label.textContent = role === "user" ? "You" : "Agent";

    const body  = document.createElement("div");
    body.className = "msg-text";
    body.textContent = text;

    wrap.appendChild(label);
    wrap.appendChild(body);

    if (meta) {
        const m = document.createElement("div");
        m.className = "msg-meta";
        m.textContent = meta;
        wrap.appendChild(m);
    }

    el.appendChild(wrap);
    if (_chatScrollCtl) _chatScrollCtl.followSoon();
    return wrap;
}

function clearChatPanel() {
    dom.chat().replaceChildren();
    if (_chatScrollCtl) _chatScrollCtl.followNow();
}

function appendChatLine(wrap, text) {
    if (!wrap) return;
    const body = wrap.querySelector(".msg-text");
    if (!body) return;
    body.textContent = body.textContent ? body.textContent + "\n" + text : text;
    if (_chatScrollCtl) _chatScrollCtl.followSoon();
}

function appendChatToken(wrap, text) {
    if (!wrap || !text) return;
    const body = wrap.querySelector(".msg-text");
    if (!body) return;
    body.textContent += text;
    if (_chatScrollCtl) _chatScrollCtl.followSoon();
}

function setChatMeta(wrap, meta) {
    if (!wrap || !meta) return;
    let el = wrap.querySelector(".msg-meta");
    if (!el) {
        el = document.createElement("div");
        el.className = "msg-meta";
        wrap.appendChild(el);
    }
    el.textContent = meta;
}

function appendThinking(runId, startedAtMs = Date.now()) {
    const el   = dom.chat();
    const existing = el.querySelector(".chat-thinking[data-run-id='" + runId + "']");
    if (existing) return existing;
    const wrap = document.createElement("div");
    wrap.className = "chat-thinking";
    wrap.setAttribute("data-run-id", runId);
    wrap.setAttribute("data-started-at-ms", String(startedAtMs));
    wrap.textContent = "thinking... " + _formatElapsed(Date.now() - startedAtMs);
    el.appendChild(wrap);
    if (_chatScrollCtl) _chatScrollCtl.followSoon();
}

function removeThinking(runId) {
    const el = dom.chat().querySelector(".chat-thinking[data-run-id='" + runId + "']");
    if (el) el.remove();
}

// ====================================================================================================
// MARK: RUN STREAM (SSE per prompt)
// ====================================================================================================

function listenRun(runId, { startRendered = false } = {}) {
    // Each run gets its own EventSource so concurrent in-flight requests
    // do not cancel each other.
    const es = new EventSource(API_BASE + "/runs/" + encodeURIComponent(runId) + "/stream");
    let progressWrap = null;
    let tokenWrap    = null;
    let streamedText = "";
    let startedAtMs  = Number(_loadActiveRun()?.startedAtMs) || Date.now();

    es.onmessage = e => {
        try {
            const ev = JSON.parse(e.data);
            if (ev.type === "start") {
                startedAtMs = Number(ev.submitted_at_ms) || startedAtMs;
                _saveActiveRun({ runId, sessionId: _sessionId, prompt: ev.prompt || "", startedAtMs });
                if (!startRendered) appendChatMessage("user", ev.prompt, "", runId);
                if (ev.prompt && ev.prompt.startsWith("/")) {
                    startLogStream();
                }
                appendThinking(runId, startedAtMs);
            } else if (ev.type === "log_file") {
                // Only follow the new log file if live mode is active.
                if (_logScrollCtl && _logScrollCtl.live) {
                    _switchLogStream(ev.path);
                }
            } else if (ev.type === "progress") {
                if (!progressWrap) {
                    progressWrap = appendChatMessage("agent", ev.text);
                } else {
                    appendChatLine(progressWrap, ev.text);
                }
            } else if (ev.type === "token") {
                removeThinking(runId);
                if (!tokenWrap) tokenWrap = appendChatMessage("agent", "");
                appendChatToken(tokenWrap, ev.text || "");
                streamedText += ev.text || "";
            } else if (ev.type === "response") {
                removeThinking(runId);
                const elapsedMs = Number(ev.elapsed_ms) || Date.now() - startedAtMs;
                const meta = ev.tokens
                    ? ev.tokens.toLocaleString() + " ctx" + (ev.tps && ev.tps !== "0" ? " | " + ev.tps + " tok/s" : "") + " | " + _formatElapsed(elapsedMs)
                    : _formatElapsed(elapsedMs);
                _saveTurnMeta(_sessionId, _loadActiveRun()?.prompt || "", ev.response, meta);
                if (!tokenWrap) {
                    appendChatMessage("agent", ev.response, meta);
                } else {
                    if ((ev.response || "").startsWith(streamedText)) {
                        appendChatToken(tokenWrap, ev.response.slice(streamedText.length));
                    }
                    setChatMeta(tokenWrap, meta);
                }
                // Refresh the history cache silently so the next page load is instant.
                apiFetch("/sessions/" + encodeURIComponent(_sessionId) + "/history").then(d => {
                    if (!d) return;
                    if (typeof d.title === "string") {
                        _setChatPanelTitle(_resolveSessionTitle(_sessionId, d.title));
                    }
                    if (Array.isArray(d.turns)) {
                        try { localStorage.setItem("maf_history_" + _sessionId, JSON.stringify(d.turns)); } catch (_) {}
                    }
                });
            } else if (ev.type === "error") {
                removeThinking(runId);
                appendChatMessage("agent", "[Error: " + ev.message + "]");
                _clearActiveRun(runId);
            } else if (ev.type === "rename_session") {
                // Same chat, file renamed - update routing ID and title in-place; no history replay.
                _sessionId = ev.session_id;
                _setChatPanelTitle(_resolveSessionTitle(ev.session_id, ev.name));
                _loadCompletions();
            } else if (ev.type === "switch_session") {
                _sessionId = ev.session_id;
                const label = _resolveSessionTitle(ev.session_id, ev.name);
                _setChatPanelTitle(label);
                clearChatPanel();
                if (label) {
                    appendChatMessage("agent", "\u2500\u2500\u2500 Session: " + label + " \u2500\u2500\u2500");
                }
                _loadSessionHistory(ev.session_id);
                _loadHistory();
                _loadCompletions();
            } else if (ev.type === "done") {
                removeThinking(runId);
                _clearActiveRun(runId);
                es.close();
                refreshQueue();
            }
        } catch (err) {
            console.warn("run event parse error", err);
        }
    };

    es.onerror = () => {
        es.close();
    };
}

function _resumePersistedRun(sessionId) {
    const run = _loadActiveRun();
    if (!run || run.sessionId !== sessionId) return;

    const selector = ".chat-msg.user[data-run-id='" + run.runId + "']";
    if (!dom.chat().querySelector(selector)) {
        appendChatMessage("user", run.prompt || "", "", run.runId);
    }
    appendThinking(run.runId, Number(run.startedAtMs) || Date.now());
    listenRun(run.runId, { startRendered: true });
}

// ----------------------------------------------------------------------------------------------------

async function _loadSessionHistory(sessionId) {
    _setChatPanelTitle(_resolveSessionTitle(sessionId, _sessionTitle));
    // Render from cache immediately so the panel is populated before the network responds.
    const cacheKey = "maf_history_" + sessionId;
    const cached = (() => { try { const r = localStorage.getItem(cacheKey); return r ? JSON.parse(r) : null; } catch (_) { return null; } })();
    if (cached && Array.isArray(cached)) {
        for (let i = 0; i + 1 < cached.length; i += 2) {
            const u = cached[i];
            const a = cached[i + 1];
            if (u && u.role === "user")      appendChatMessage("user",  u.content);
            if (a && a.role === "assistant") appendChatMessage("agent", a.content, _turnMetaText(sessionId, u?.content, a));
        }
    }
    // Fetch fresh data and update the panel.
    const data = await apiFetch("/sessions/" + encodeURIComponent(sessionId) + "/history");
    if (!data) {
        _resumePersistedRun(sessionId);
        return;
    }
    if (typeof data.title === "string") {
        _setChatPanelTitle(_resolveSessionTitle(sessionId, data.title));
    }
    if (!Array.isArray(data.turns)) {
        _resumePersistedRun(sessionId);
        return;
    }
    const turns = data.turns;
    try { localStorage.setItem(cacheKey, JSON.stringify(turns)); } catch (_) {}
    // Only re-render if the content differs from what was already shown from cache.
    const cachedJson = cached ? JSON.stringify(cached) : null;
    if (cachedJson !== JSON.stringify(turns)) {
        clearChatPanel();
        for (let i = 0; i + 1 < turns.length; i += 2) {
            const u = turns[i];
            const a = turns[i + 1];
            if (u && u.role === "user")      appendChatMessage("user",  u.content);
            if (a && a.role === "assistant") appendChatMessage("agent", a.content, _turnMetaText(sessionId, u?.content, a));
        }
    }
    _resumePersistedRun(sessionId);
}

// ====================================================================================================
// MARK: SUBMIT PROMPT
// ====================================================================================================

async function _loadHistory() {
    const data = await apiFetch("/sessions/" + encodeURIComponent(_sessionId) + "/input-history");
    if (data && Array.isArray(data.entries)) {
        _inputHistory = data.entries;
    }
}

async function _pushHistory(text) {
    // Optimistic local update so Up-arrow works immediately.
    // Erase-dups: remove any prior occurrence then append so each entry appears only once.
    const idx = _inputHistory.lastIndexOf(text);
    if (idx !== -1) _inputHistory.splice(idx, 1);
    _inputHistory.push(text);
    if (_inputHistory.length > 20) _inputHistory.shift();
    // Persist to server (fire-and-forget; refresh local list from response).
    const data = await apiFetch("/sessions/" + encodeURIComponent(_sessionId) + "/input-history", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ text }),
    });
    if (data && Array.isArray(data.entries)) {
        _inputHistory = data.entries;
    }
}

function submitPrompt() {
    const text = dom.input().value.trim();
    if (!text) return;
    _hideSuggest();

    // Every prompt is first recorded in KoreChat.  The shared Agent event worker
    // then handles it when the model becomes available, instead of discarding work
    // in this browser when the local-model status is temporarily unavailable.

    // Clear input and reset history cursor immediately so the user can keep typing.
    dom.input().value = "";
    _clearInputDraft();
    _resizeTextarea();
    _historyIdx = -1;
    _historyDraft = null;

    // Dispatch immediately so the Python queue reflects the real prompt backlog.
    _dispatchPrompt(text);
}

async function _dispatchPrompt(text) {
    // KoreChat owns durable prompts and replies.  This UI contributes an inbound
    // message, then observes the outbound result produced by the shared event worker.
    const data = await apiFetch("/kc/send", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ session_id: _sessionId, content: text }),
    });
    _pushHistory(text);
    if (!data) {
        appendChatMessage("user", text);
        appendChatMessage("agent", "[Error: could not record the prompt in KoreChat]");
        return;
    }
    const thinkKey = "kc_" + data.conv_id + "_" + data.msg_id;
    appendChatMessage("user", text, "", thinkKey);
    appendThinking(thinkKey);
    refreshQueue();
    _pollKcReply(thinkKey, data.conv_id, data.msg_id);
}

async function _pollKcReply(thinkKey, convId, afterMsgId) {
    const MAX_POLLS   = 120;  // 2 minutes at 1-second intervals
    const POLL_MS     = 1000;
    for (let i = 0; i < MAX_POLLS; i++) {
        await new Promise(r => setTimeout(r, POLL_MS));
        const messages = await apiFetch("/kc/conversations/" + convId + "/messages");
        if (!Array.isArray(messages)) continue;
        const replies = messages.filter(m => m.direction === "outbound" && m.id > afterMsgId);
        if (replies.length > 0) {
            removeThinking(thinkKey);
            for (const m of replies) {
                appendChatMessage("agent", m.content);
            }
            return;
        }
    }
    removeThinking(thinkKey);
    appendChatMessage("agent", "[No response received within timeout]");
}

// ====================================================================================================
// MARK: TAB COMPLETE
// ====================================================================================================

async function _loadCompletions() {
    const data = await apiFetch("/completions");
    if (data) _completions = data;
}

// Parse the current input value and return the completion context, or null.
// Returns { pool, prefix, base } where:
//   pool   - string[] of all candidates for this slot
//   prefix - the partial text the user has typed (used for filtering)
//   base   - everything in the input before the partial text
function _parseSuggestContext(value) {
    if (!value.startsWith("/")) return null;

    const firstSpace = value.indexOf(" ");

    // Slot 0: still typing the command name (no space yet).
    if (firstSpace === -1) {
        return { pool: _ALL_COMMANDS, prefix: value, base: "" };
    }

    const cmd  = value.slice(0, firstSpace);   // e.g. "/chat"
    const rest = value.slice(firstSpace + 1);  // everything after first space

    if (cmd === "/chat" || cmd === "/session") {
        const subSpace = rest.indexOf(" ");
        if (subSpace === -1) {
            // Slot 1: completing the sub-command.
            return { pool: _CHAT_SUBS, prefix: rest, base: `${cmd} ` };
        }
        const sub      = rest.slice(0, subSpace);
        const arg1Base = value.slice(0, firstSpace + 1 + subSpace + 1);  // "/chat sub "
        const arg1Text = value.slice(arg1Base.length);

        if (sub === "resume" || sub === "delete") {
            return { pool: _completions.sessions, prefix: arg1Text.trimEnd(), base: arg1Base };
        }
        if (sub === "resumecopy") {
            // Only complete the first argument (the source session name).
            if (arg1Text.indexOf(" ") === -1) {
                return { pool: _completions.sessions, prefix: arg1Text.trimEnd(), base: arg1Base };
            }
            // Second arg is a new name - no completion.
            return null;
        }
        return null;
    }

    if (cmd === "/llmserver") {
        const subSpace = rest.indexOf(" ");
        if (subSpace === -1) {
            return { pool: _LLMSERVER_SUBS, prefix: rest, base: "/llmserver " };
        }
        const sub      = rest.slice(0, subSpace).toLowerCase();
        const arg1Base = value.slice(0, firstSpace + 1 + subSpace + 1);
        const arg1Text = value.slice(arg1Base.length);
        return null;
    }

    if (cmd === "/workspace") {
        return { pool: _WORKSPACE_SUBS, prefix: rest, base: "/workspace " };
    }

    if (cmd === "/llmserverconfig") {
        const subSpace = rest.indexOf(" ");
        if (subSpace === -1) {
            // Slot 1: completing the sub-command name.
            return { pool: _LLMSERVERCFG_SUBS, prefix: rest, base: "/llmserverconfig " };
        }
        const sub      = rest.slice(0, subSpace);
        const arg1Base = value.slice(0, firstSpace + 1 + subSpace + 1);
        const arg1Text = value.slice(arg1Base.length);
        if (sub === "model" && !arg1Text.includes(" ")) {
            return { pool: ["list", ..._completions.models], prefix: arg1Text.trimEnd(), base: arg1Base };
        }
        if (sub === "cpugpu" && !arg1Text.includes(" ")) {
            return { pool: _LLMSERVER_CONFIGS, prefix: arg1Text.trimEnd(), base: arg1Base };
        }
        return null;
    }

    if (cmd === "/model") {
        if (!rest.includes(" ")) {
            return { pool: _completions.models, prefix: rest.trimEnd(), base: "/model " };
        }
        return null;
    }

    if (cmd === "/tools") {
        if (!rest.includes(" ")) {
            return { pool: _TOOLS_SUBS, prefix: rest.trimEnd(), base: "/tools " };
        }
        const words = rest.trimStart().split(/\s+/);
        if (words[0] === "groups" && words.length === 1 && rest.endsWith(" ")) {
            return { pool: _TOOL_GROUPS_SUBS, prefix: "", base: "/tools groups " };
        }
        if (words[0] === "groups" && words.length === 2 && !rest.endsWith(" ")) {
            return { pool: _TOOL_GROUPS_SUBS, prefix: words[1], base: "/tools groups " };
        }
        return null;
    }

    if (cmd === "/comms") {
        const words = rest.trimStart().split(/\s+/);
        if (!rest.trim()) {
            return { pool: _COMMS_SUBS, prefix: "", base: "/comms " };
        }
        if (words.length === 1 && !rest.endsWith(" ")) {
            return { pool: _COMMS_SUBS, prefix: words[0], base: "/comms " };
        }
        if (words[0] === "connection") {
            if (words.length === 1 && rest.endsWith(" ")) {
                return { pool: _COMMS_CONNECTION_SUBS, prefix: "", base: "/comms connection " };
            }
            if (words.length === 2 && !rest.endsWith(" ")) {
                return { pool: _COMMS_CONNECTION_SUBS, prefix: words[1], base: "/comms connection " };
            }
            if (!_COMMS_CONNECTION_SUBS.includes(words[1])) return null;
            const usedOptions = new Set(words.filter((word) => word.startsWith("--")));
            const pool = _COMMS_CONNECTION_OPTIONS.filter((option) => !usedOptions.has(option));
            const lastWord = rest.endsWith(" ") ? "" : words[words.length - 1];
            const base = rest.endsWith(" ") ? `${value}` : value.slice(0, value.length - lastWord.length);
            return { pool, prefix: lastWord, base };
        }
        if (words[0] !== "delivery") return null;
        if (words.length === 1 && rest.endsWith(" ")) {
            return { pool: _COMMS_DELIVERY_SUBS, prefix: "", base: "/comms delivery " };
        }
        if (words.length === 2 && !rest.endsWith(" ")) {
            return { pool: _COMMS_DELIVERY_SUBS, prefix: words[1], base: "/comms delivery " };
        }
        if (words[1] !== "bind") return null;
        const usedOptions = new Set(words.filter((word) => word.startsWith("--")));
        const pool = _COMMS_BIND_OPTIONS.filter((option) => !usedOptions.has(option));
        const lastWord = rest.endsWith(" ") ? "" : words[words.length - 1];
        const base = rest.endsWith(" ") ? `${value}` : value.slice(0, value.length - lastWord.length);
        return { pool, prefix: lastWord, base };
    }

    return null;
}

function _updateSuggest() {
    const ctx = _parseSuggestContext(dom.input().value);
    if (!ctx) { _hideSuggest(); return; }

    const pfx  = ctx.prefix.toLowerCase();
    const items = ctx.pool.filter(s => s.toLowerCase().startsWith(pfx));

    if (items.length === 0) { _hideSuggest(); return; }

    _suggestItems = items;
    _suggestBase  = ctx.base;
    _suggestIdx   = -1;
    _renderSuggest();
}

function _renderSuggest() {
    const el = $("slash-suggest");
    if (!el) return;

    el.innerHTML = "";
    _suggestItems.forEach((item, i) => {
        const row = document.createElement("div");
        row.className  = "suggest-item" + (i === _suggestIdx ? " active" : "");
        row.textContent = item;
        if (_SUGGEST_HINTS[item]) row.title = _SUGGEST_HINTS[item];
        row.addEventListener("mousedown", e => {
            e.preventDefault();   // prevent textarea from losing focus
            _selectSuggest(i);
        });
        el.appendChild(row);
    });

    // Position fixed, sitting immediately above the textarea.
    // Width: longest item in ch units (monospace) + padding allowance, capped at textarea width.
    const rect      = dom.input().getBoundingClientRect();
    const longest   = _suggestItems.reduce((m, s) => Math.max(m, s.length), 0);
    const fitWidth  = longest * 7.5 + 56;   // ~7.5px per char at 12px mono + 56px padding/scrollbar
    el.style.left   = rect.left + "px";
    el.style.width  = Math.min(fitWidth, rect.width) + "px";
    el.style.bottom = (window.innerHeight - rect.top) + "px";
    el.removeAttribute("hidden");
}

function _hideSuggest() {
    const el = $("slash-suggest");
    if (el) el.setAttribute("hidden", "");
    _suggestItems = [];
    _suggestIdx   = -1;
}

function _selectSuggest(idx) {
    const item = _suggestItems[idx];
    if (item === undefined) return;
    dom.input().value = _suggestBase + item + " ";
    _hideSuggest();
    dom.input().focus();
    // Chain: re-evaluate so the next dropdown level appears immediately.
    _updateSuggest();
}

// ====================================================================================================
// MARK: KEYBOARD HANDLER
// ====================================================================================================

function onInputKeydown(e) {
    // --- Tab: open or cycle the suggestion dropdown. ---
    if (e.key === "Tab") {
        e.preventDefault();
        if (_suggestItems.length > 0) {
            if (_suggestIdx >= 0) {
                _selectSuggest(_suggestIdx);
            } else {
                _suggestIdx = 0;
                _renderSuggest();
            }
        } else {
            _updateSuggest();
            if (_suggestItems.length === 1) _selectSuggest(0);
        }
        return;
    }

    // --- Escape: close the dropdown. ---
    if (e.key === "Escape") {
        if (_suggestItems.length > 0) {
            e.preventDefault();
            _hideSuggest();
            return;
        }
    }

    // --- Enter: select highlighted suggestion, or submit prompt. ---
    if (e.key === "Enter" && !e.shiftKey) {
        if (_suggestItems.length > 0 && _suggestIdx >= 0) {
            e.preventDefault();
            _selectSuggest(_suggestIdx);
            return;
        }
        e.preventDefault();
        submitPrompt();
        return;
    }

    // --- ArrowDown: navigate suggestion dropdown, else history. ---
    if (e.key === "ArrowDown") {
        if (_suggestItems.length > 0) {
            e.preventDefault();
            _suggestIdx = Math.min(_suggestIdx + 1, _suggestItems.length - 1);
            _renderSuggest();
            return;
        }
        // History navigation (existing behaviour).
        if (_historyIdx === -1) return;
        e.preventDefault();
        if (_historyIdx < _inputHistory.length - 1) {
            _historyIdx++;
            dom.input().value = _inputHistory[_historyIdx];
        } else {
            _historyIdx = -1;
            dom.input().value = _historyDraft ?? _restoreInputDraft();
            _historyDraft = null;
        }
        _resizeTextarea();
        const elD = dom.input();
        elD.setSelectionRange(elD.value.length, elD.value.length);
        return;
    }

    // --- ArrowUp: navigate suggestion dropdown, else history. ---
    if (e.key === "ArrowUp") {
        if (_suggestItems.length > 0) {
            e.preventDefault();
            _suggestIdx = _suggestIdx > 0 ? _suggestIdx - 1 : -1;
            _renderSuggest();
            return;
        }
        // History navigation (existing behaviour).
        if (_inputHistory.length === 0) return;
        e.preventDefault();
        if (_historyIdx === -1) {
            _historyDraft = dom.input().value;
            _saveInputDraft(_historyDraft);
            _historyIdx = _inputHistory.length - 1;
        } else if (_historyIdx > 0) {
            _historyIdx--;
        }
        dom.input().value = _inputHistory[_historyIdx];
        _resizeTextarea();
        const elU = dom.input();
        elU.setSelectionRange(elU.value.length, elU.value.length);
        return;
    }
}

function onInputChange() {
    // Update the suggestion dropdown on every keystroke.
    _updateSuggest();
    // Grow the textarea to fit its content.
    _resizeTextarea();
}

function _resizeTextarea() {
    const ta = dom.input();
    // Input panel now owns textarea sizing; keep it matched to panel height.
    ta.style.height = "100%";
    ta.style.overflowY = "auto";
}

// ====================================================================================================
// MARK: POLLING INTERVALS
// ====================================================================================================

function startPolling() {
    refreshOllamaStatus();
    refreshQueue();
    refreshLatestLogFile();
    _loadCompletions();

    setInterval(refreshOllamaStatus,  POLL_OLLAMA_MS);
    setInterval(refreshQueue,         POLL_QUEUE_MS);
    setInterval(refreshLatestLogFile, POLL_LATEST_LOG_MS);
    setInterval(_loadCompletions,     30_000);
}

// ====================================================================================================
// MARK: INIT
// ====================================================================================================

function init() {
    initTopbar({ currentService: "koreagent", urls: window.__koreSuiteUrls || {} });
    initAppBar({
        currentService: "koreagent",
        overline:       "Agent Control",
        brandLabel:     "KoreAgent",
        brandIcon:      "koreagent",
        chips: [
            { label: "Host",    value: "", valueId: "ollama-host",  tone: "info" },
            { label: "Model",   value: "", valueId: "ollama-model", tone: "info" },
            { label: "Context", value: "", valueId: "ollama-ctx",   tone: "info" },
        ],
        actions: [
            { kind: "tag", id: "btn-skills-catalog", action: "skills-catalog", label: "skill catalog",  className: "kcui-tag kcui-tag--dim kcui-tag--skills-catalog" },
            { kind: "tag", id: "btn-reset-layout",   action: "reset-layout",   label: "default layout", className: "kcui-tag kcui-tag--dim" },
        ],
    });

    _restoreSessionUiState();
    const requestedSession = _consumeRequestedSession();
    if (requestedSession?.sessionId) {
        _sessionId = requestedSession.sessionId;
        _setChatPanelTitle(_resolveSessionTitle(requestedSession.sessionId, requestedSession.name), { persist: false });
    }
    _persistActiveSession();
    _restoreWrapState();
    _refreshThinkingTimers();
    _thinkingTimer = window.setInterval(_refreshThinkingTimers, 1_000);

    // Initialise drag-resize splitters and apply stored layout.
    initSplitters();

    // Load per-conversation input history from the server for the current session.
    _loadHistory();

    // Read sandbox state from server and reflect it in the button.
    _initSandboxBtn();

    // Read web skills state from server and reflect it in the button.
    _initWebSkillsBtn();

    // Read LLM Direct state from server and reflect it in the button.
    _initDirectBtn();

    // Wire up input events.
    dom.input().addEventListener("keydown", onInputKeydown);
    dom.input().addEventListener("input", () => { _historyIdx = -1; _historyDraft = null; _saveInputDraft(dom.input().value); onInputChange(); });
    dom.input().addEventListener("blur",  () => { setTimeout(_hideSuggest, 120); });
    $("log-btn-up")?.addEventListener("click", () => { logNavStep(-1); });
    $("log-btn-down")?.addEventListener("click", () => { logNavStep(1); });
    $("log-btn-live")?.addEventListener("click", toggleLogLive);
    $("wrap-btn-log")?.addEventListener("click", () => { toggleWrap("log-body", "wrap-btn-log"); });
    $("sandbox-btn")?.addEventListener("click", toggleSandbox);
    $("webskills-btn")?.addEventListener("click", toggleWebSkills);
    $("direct-btn")?.addEventListener("click", toggleDirect);
    $("wrap-btn-chat")?.addEventListener("click", () => { toggleWrap("chat-body", "wrap-btn-chat"); });
    $("btn-skills-catalog")?.addEventListener("click", () => {
        window.location.href = "/skills-catalog";
    });
    $("btn-reset-layout")?.addEventListener("click", resetLayout);

    // Restore any in-progress draft from before the user navigated away.
    const _draft = _restoreInputDraft();
    if (_draft) {
        dom.input().value = _draft;
        _resizeTextarea();
        dom.input().focus();
    }
    dom.sendBtn().addEventListener("click", submitPrompt);
    _resizeTextarea();

    _chatScrollCtl = _createPanelScrollController(dom.chat(), { initialLive: true });
    _logScrollCtl  = _createPanelScrollController(dom.log(), {
        initialLive: true,
        allowAutoResume: false,
        onLiveChange: (live) => _setLiveBtn(live),
    });

    // Restore any existing chat session after a browser refresh.
    clearChatPanel();
    _loadSessionHistory(_sessionId);

    let _resizeTimer = null;
    window.addEventListener("resize", () => {
        clearTimeout(_resizeTimer);
        _resizeTimer = setTimeout(() => {
            _resizeTextarea();
        }, 100);
    });

    // Start live log stream.
    startLogStream();

    // Start polling for status, queue, and tasks.
    startPolling();
}

document.addEventListener("DOMContentLoaded", init);
