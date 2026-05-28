// ====================================================================================================
// KoreChat Debug UI - conversations.js
// ====================================================================================================
// Fetches data from the KoreChat REST API (same origin, port 8700) and renders:
//   - Left sidebar: list of all conversations with key metadata
//   - Right pane:   selected conversation's full detail - metadata, background context,
//                   thread summary, scratchpad, messages, and events
//
// No external dependencies. Vanilla JS only.
// ====================================================================================================

"use strict";

// ====================================================================================================
// STATE
// ====================================================================================================

let _selectedId         = null;
let _selectedExternalId = null;
let _selectedConv       = null;
let _autoInterval       = null;
let _sse                = null;   // EventSource for /stream push notifications
let _allConversations   = [];
let _dragStartX         = null;
let _dragStartW         = null;
let _defaultCullInterval = null;

const DEFAULT_CHAT_AGE_STORAGE_KEY = "kc_max_default_chat_age_days";
const DEFAULT_CHAT_AGE_FALLBACK_DAYS = 7;
const DEFAULT_CHAT_AGE_CULL_MS = 60 * 60 * 1000;
const AUTO_REFRESH_MS = 30_000;

function _cachedSuiteUrls() {
    try {
        const raw = localStorage.getItem("kore.suite-urls");
        return raw ? JSON.parse(raw) : null;
    } catch (_) {
        return null;
    }
}

function _defaultKoreAgentBase() {
    const host = window.location?.hostname || "127.0.0.1";
    return `http://${host}:8605`;
}

// ====================================================================================================
// CACHE HELPERS
// ====================================================================================================
// Persist the last-known API responses in localStorage so the page can render instantly
// on load before the network response arrives (stale-while-revalidate pattern).

function _cacheSet(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {}
}

function _cacheGet(key) {
    try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : null;
    } catch (_) { return null; }
}

// ====================================================================================================
// INIT
// ====================================================================================================

document.addEventListener("DOMContentLoaded", () => {
    // Render from localStorage cache immediately - before any network request.
    const cachedList = _cacheGet("kc_conv_list");
    if (cachedList) { _allConversations = cachedList; renderConversationListState(); }

    const saved = parseInt(localStorage.getItem("kc_selected_id"), 10);
    if (saved && !isNaN(saved)) {
        const cachedDetail = _cacheGet("kc_detail_" + saved);
        if (cachedDetail) { _renderDetail(cachedDetail); }
    }

    // Fetch fresh data in parallel - updates the display when it arrives.
    const loadDetail = (saved && !isNaN(saved)) ? selectConversation(saved) : Promise.resolve();
    Promise.all([loadStatus(), loadConversations(), loadDetail]);
    initSplitter();

    bindUiEvents();
    initDefaultChatAgeCulling();

    // Connect to the SSE push stream - this replaces the 5-second poll interval.
    // A 30-second fallback interval handles SSE gaps (reconnect window, etc.).
    const chk = document.getElementById("chk-auto");
    chk.checked = true;
    _connectSSE();
    _autoInterval = setInterval(refreshAll, AUTO_REFRESH_MS);

    // Also refresh immediately when the tab becomes visible again.
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) refreshAll();
    });
    window.addEventListener("focus", refreshAll);
});

function bindUiEvents() {
    document.getElementById("max-default-chat-age")?.addEventListener("change", onMaxDefaultChatAgeChanged);

    document.getElementById("btn-create-conv").addEventListener("click", toggleNewConvForm);
    document.getElementById("btn-refresh")?.addEventListener("click", refreshAll);
    document.getElementById("new-conv-submit").addEventListener("click", createConversation);
    document.getElementById("new-conv-cancel").addEventListener("click", hideNewConvForm);
    document.getElementById("new-conv-name").addEventListener("keydown", e => {
        if (e.key === "Enter")  createConversation();
        if (e.key === "Escape") hideNewConvForm();
    });
    document.getElementById("agent-resume-btn").addEventListener("click", agentResume);
    document.getElementById("delete-conv-btn").addEventListener("click", deleteConversation);
    document.getElementById("compose-btn").addEventListener("click", sendMessage);
    document.getElementById("chk-summarised").addEventListener("change", reloadMessages);
    document.getElementById("chk-auto")?.addEventListener("change", toggleAuto);
    document.getElementById("meta-table")?.addEventListener("click", onMetaTableClick);

    document.getElementById("conv-list").addEventListener("click", (event) => {
        const row = event.target.closest(".conv-item[data-id]");
        if (!row) return;
        const id = Number.parseInt(row.dataset.id, 10);
        if (!Number.isNaN(id)) {
            selectConversation(id);
        }
    });

    document.getElementById("compose-text").addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    });
}

// ====================================================================================================
// STATUS
// ====================================================================================================

async function loadStatus() {
    try {
        const r = await fetch("/status");
        if (!r.ok) { throw new Error(`HTTP ${r.status}`); }
        await r.json();
        document.getElementById("status-label").textContent = "connected";
    } catch {
        document.getElementById("status-label").textContent = "offline";
    }
}

// ====================================================================================================
// CONVERSATION LIST
// ====================================================================================================

async function loadConversations() {
    try {
        const r = await fetch("/conversations?limit=500");
        if (!r.ok) { throw new Error(`HTTP ${r.status}`); }
        _allConversations = await r.json();
        _cacheSet("kc_conv_list", _allConversations);
        renderConversationListState();
    } catch (e) {
        console.error("loadConversations:", e);
    }
    return _allConversations;
}

function renderConversationListState() {
    renderConvList(_allConversations);
}

function renderConvList(conversations) {
    const el = document.getElementById("conv-list");
    document.getElementById("conv-count").textContent = conversations.length;

    if (conversations.length === 0) {
        el.innerHTML = "<div class='empty-note'>No conversations.</div>";
        return;
    }

    // Sort by last_activity_at descending
    const sorted = [...conversations].sort((a, b) =>
        (b.last_activity_at || "").localeCompare(a.last_activity_at || "")
    );

    el.innerHTML = sorted.map(c => {
        const subject  = c.subject || "(no subject)";
        const selected = c.id === _selectedId ? " selected" : "";
        const ts       = formatDateTime(c.last_activity_at);
        const displayStatus = getDisplayStatus(c.status);
        return `
<div class="conv-item${selected}" data-id="${c.id}">
    <div class="conv-item-top">
        <span class="conv-id">#${c.id}</span>
        <span class="conv-subject">${escHtml(subject)}</span>
    </div>
    <div class="conv-item-mid">
        ${pill(displayStatus.label)}
        ${pill(c.profile)}
    </div>
    <div class="conv-item-bot">${ts}</div>
</div>`;
    }).join("");
}

// ====================================================================================================
// CONVERSATION DETAIL
// ====================================================================================================

async function selectConversation(id) {
    _selectedId = id;
    localStorage.setItem("kc_selected_id", id);

    document.getElementById("detail-empty").hidden = true;
    document.getElementById("detail").hidden        = false;

    try {
        const r = await fetch(`/conversations/${id}/detail`);
        if (!r.ok) return;
        const data = await r.json();
        _cacheSet("kc_detail_" + id, data);
        _renderDetail(data);
    } catch (e) {
        console.error("selectConversation:", e);
    }
}

function _renderDetail(data) {
    const id   = _selectedId;
    const conv = data.conversation;
    const msgs = data.messages;
    const evts = data.events;

    if (conv) {
        _selectedExternalId = conv.external_id || null;
        _selectedConv = conv;
        renderMeta(conv);
        renderBackground(conv.background_context || "");
        renderSummary(conv.thread_summary || "");
        renderScratchpad(conv.scratchpad);
        renderInputHistory(conv.input_history || []);
    }
    document.querySelectorAll(".conv-item").forEach(el => {
        el.classList.toggle("selected", parseInt(el.dataset.id) === id);
    });
    renderMessages(msgs);
    renderEvents(evts);
}

// ====================================================================================================
// META TABLE
// ====================================================================================================

function _protectUntilLabel(conv) {
    const isProtected = Number(conv.protected || 0) === 1;
    if (isProtected) return "Protected";
    const lastActivity = conv.last_activity_at;
    if (!lastActivity) return "(unknown)";
    const ageDays = _normalizeChatAgeDays(localStorage.getItem(DEFAULT_CHAT_AGE_STORAGE_KEY));
    const expiryMs = new Date(lastActivity).getTime() + ageDays * 86_400_000;
    if (isNaN(expiryMs)) return "(unknown)";
    return formatDateTime(new Date(expiryMs).toISOString());
}

function renderMeta(conv) {
    const displayStatus = getDisplayStatus(conv.status);
    const isProtected = Number(conv.protected || 0) === 1;
    const protectedButton = `<button type="button" id="meta-protected-toggle" class="kcui-tag ${isProtected ? "kcui-tag--success" : "kcui-tag--warning"}" data-protected="${isProtected ? 1 : 0}" title="Toggle protected">${isProtected ? "Yes" : "No"}</button>`;
    const nameRow = `<div class="meta-name-row"><input class="meta-name-input" id="meta-name-input" type="text" value="${escHtml(conv.subject || '')}" autocomplete="off"><button id="meta-name-apply" class="kcui-tag kcui-tag--dim" type="button">apply</button></div>`;
    const rows = [
        ["id",              conv.id],
        ["name",            nameRow],
        ["status",          pill(displayStatus.label)],
        ["profile",         pill(conv.profile)],
        ["protected",       `<span class="meta-protected-row">${protectedButton}<span class="meta-protected-until">${escHtml(_protectUntilLabel(conv))}</span></span>`],
        ["turn_count",      conv.turn_count ?? 0],
        ["token_estimate",  (conv.token_estimate ?? 0).toLocaleString()],
        ["last_activity_at",formatDateTime(conv.last_activity_at)],
        ["created_at",      formatDateTime(conv.created_at)],
        ["updated_at",      formatDateTime(conv.updated_at)],
    ];
    const midpoint = Math.ceil(rows.length / 2);
    document.getElementById("meta-table").innerHTML =
        renderMetaColumn(rows.slice(0, midpoint)) +
        renderMetaColumn(rows.slice(midpoint));
}

function renderMetaColumn(rows) {
    return `<table class="kv-table meta-col">${rows.map(([k, v]) => `<tr><td>${k}</td><td>${v}</td></tr>`).join("")}</table>`;
}

async function onMetaTableClick(event) {
    if (_selectedId === null) return;

    const protectedBtn = event.target.closest("#meta-protected-toggle");
    if (protectedBtn) {
        const current = String(protectedBtn.dataset.protected || "0") === "1";
        const next = !current;
        protectedBtn.disabled = true;
        try {
            const resp = await fetch(`/conversations/${_selectedId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ protected: next }),
            });
            if (!resp.ok) {
                const err = await resp.text().catch(() => "");
                throw new Error(`HTTP ${resp.status}: ${err}`);
            }
            await refreshAll();
        } catch (e) {
            console.error("toggleProtected:", e);
            window.alert(`Protected toggle failed: ${e.message}`);
        } finally {
            protectedBtn.disabled = false;
        }
        return;
    }

    const applyBtn = event.target.closest("#meta-name-apply");
    if (applyBtn) {
        const input = document.getElementById("meta-name-input");
        const subject = (input?.value || "").trim();
        if (!subject) return;
        applyBtn.disabled = true;
        try {
            const resp = await fetch(`/conversations/${_selectedId}`, {
                method:  "PATCH",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ subject }),
            });
            if (!resp.ok) {
                const err = await resp.text().catch(() => "");
                throw new Error(`HTTP ${resp.status}: ${err}`);
            }
            await refreshAll();
        } catch (e) {
            console.error("renameConversation:", e);
            window.alert(`Rename failed: ${e.message}`);
        } finally {
            applyBtn.disabled = false;
        }
    }
}

// ====================================================================================================
// BACKGROUND CONTEXT
// ====================================================================================================

function renderBackground(text) {
    document.getElementById("sec-bg").classList.toggle("is-empty", text.length === 0);
    document.getElementById("bg-empty").hidden = text.length > 0;
    document.getElementById("bg-text").textContent = text;
}

// ====================================================================================================
// THREAD SUMMARY
// ====================================================================================================

function renderSummary(text) {
    document.getElementById("sec-summary").classList.toggle("is-empty", text.length === 0);
    document.getElementById("summary-empty").hidden = text.length > 0;
    document.getElementById("summary-text").textContent = text;
}

// ====================================================================================================
// SCRATCHPAD
// ====================================================================================================

const SCRATCHPAD_PREVIEW_LIMIT = 96;

function renderScratchpad(scratchpad) {
    let data = scratchpad;
    if (typeof data === "string") {
        try { data = JSON.parse(data); } catch { data = {}; }
    }
    data = data || {};
    const keys = Object.keys(data).filter(k => k !== "__datasets");
    document.getElementById("scratchpad-empty").hidden = keys.length > 0;

    if (keys.length === 0) {
        document.getElementById("scratchpad-list").innerHTML = "";
        return;
    }

    document.getElementById("scratchpad-list").innerHTML = keys.map(k =>
        _renderScratchpadEntry(k, data[k])
    ).join("");
}

function _renderScratchpadEntry(key, value) {
    const analysis = _scratchpadAnalyzeValue(key, value);
    const type = analysis.type;
    const detailText = analysis.detailText;
    const compactText = _scratchpadCompactText(analysis.summaryValue);
    const preview = _scratchpadPreviewText(key, analysis.summaryValue, type, compactText);
    const meta = _scratchpadMetaText(analysis.summaryValue, type, analysis.charCount);
    const previewHtml = preview ? `&quot;${escHtml(preview)}&quot;` : `<span class="scratchpad-preview-empty">(empty)</span>`;

    return `
<details class="scratchpad-entry scratchpad-entry--${type}">
    <summary class="scratchpad-summary">
        <span class="scratchpad-key">${escHtml(key)}</span>
        <span class="scratchpad-preview">${previewHtml}</span>
        <span class="scratchpad-meta">${escHtml(meta)}</span>
    </summary>
    <pre class="scratchpad-content">${escHtml(detailText)}</pre>
</details>`;
}

function _scratchpadAnalyzeValue(key, value) {
    if (typeof value === "string") {
        const parsed = _scratchpadParseJsonObject(value);
        if (parsed !== null) {
            return {
                type: _scratchpadValueType(key, parsed),
                summaryValue: parsed,
                detailText: _scratchpadDetailText(parsed),
                charCount: value.length,
            };
        }
    }

    const detailText = _scratchpadDetailText(value);
    return {
        type: _scratchpadValueType(key, value),
        summaryValue: value,
        detailText,
        charCount: detailText.length,
    };
}

function _scratchpadParseJsonObject(text) {
    const trimmed = String(text || "").trim();
    if (!trimmed || (trimmed[0] !== "{" && trimmed[0] !== "[")) return null;
    try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === "object") return parsed;
    } catch (_) { /* ignore */ }
    return null;
}

function _scratchpadValueType(key, value) {
    if (key === "__datasets" && value && typeof value === "object" && !Array.isArray(value)) return "datasets";
    if (typeof value === "string") return "string";
    if (Array.isArray(value)) return "array";
    if (value && typeof value === "object") {
        if (_looksLikeDataset(value)) return "dataset";
        return "object";
    }
    if (value === null || value === undefined) return "empty";
    return typeof value;
}

function _looksLikeDataset(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    if (Object.prototype.hasOwnProperty.call(value, "dataset_id")) return true;
    if (Array.isArray(value.records)) {
        return Array.isArray(value.fields)
            || Array.isArray(value.schema)
            || typeof value.count === "number"
            || typeof value.total_count === "number"
            || typeof value.returned === "number";
    }
    return false;
}

function _scratchpadDetailText(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    try {
        return JSON.stringify(value, null, 2);
    } catch (_) {
        return String(value);
    }
}

function _scratchpadCompactText(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "string") return value;
    try {
        return JSON.stringify(value);
    } catch (_) {
        return String(value);
    }
}

function _scratchpadPreviewText(key, value, type, compactText) {
    if (type === "string") return _scratchpadExcerpt(compactText);
    if (type === "dataset") {
        const datasetName = typeof value?.name === "string" && value.name.trim() ? value.name.trim() : key;
        const fields = Array.isArray(value?.fields) ? value.fields : (Array.isArray(value?.schema) ? value.schema : []);
        const fieldPreview = fields.length ? `: ${fields.slice(0, 4).join(", ")}${fields.length > 4 ? ", ..." : ""}` : "";
        return _scratchpadExcerpt(`${datasetName}${fieldPreview}`);
    }
    if (type === "datasets") {
        const names = Object.keys(value || {});
        if (!names.length) return "";
        return _scratchpadExcerpt(names.slice(0, 4).join(", ") + (names.length > 4 ? ", ..." : ""));
    }
    if (type === "empty") return "";
    return _scratchpadExcerpt(_scratchpadOneLine(compactText));
}

function _scratchpadMetaText(value, type, charCountRaw) {
    const charCount = Number(charCountRaw || 0).toLocaleString("en-GB");
    if (type === "string") return `[string ${charCount} characters]`;
    if (type === "dataset") return `[dataset ${_scratchpadDatasetRowCount(value)} rows, ${charCount} characters]`;
    if (type === "datasets") return `[datasets ${Object.keys(value || {}).length} items, ${charCount} characters]`;
    if (type === "array") return `[array ${value.length} items, ${charCount} characters]`;
    if (type === "object") return `[object ${Object.keys(value || {}).length} keys, ${charCount} characters]`;
    if (type === "empty") return "[empty]";
    return `[${type} ${charCount} characters]`;
}

function _scratchpadDatasetRowCount(value) {
    if (!value || typeof value !== "object") return 0;
    if (typeof value.total_count === "number") return value.total_count;
    if (typeof value.count === "number") return value.count;
    if (Array.isArray(value.records)) return value.records.length;
    if (typeof value.returned === "number") return value.returned;
    return 0;
}

function _scratchpadExcerpt(text, limit = SCRATCHPAD_PREVIEW_LIMIT) {
    const normalized = _scratchpadOneLine(text);
    if (!normalized) return "";
    if (normalized.length <= limit) return normalized;
    return normalized.slice(0, Math.max(0, limit - 3)).trimEnd() + "...";
}

function _scratchpadOneLine(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
}

// ====================================================================================================
// INPUT HISTORY
// ====================================================================================================

function renderInputHistory(entries) {
    const list  = document.getElementById("history-list");
    const count = document.getElementById("history-count");
    const empty = document.getElementById("history-empty");
    const items = Array.isArray(entries) ? entries : [];
    count.textContent = items.length;
    empty.hidden      = items.length > 0;
    // Render newest-first so the most recent prompt is at the top.
    list.innerHTML    = items.slice().reverse().map(e =>
        `<li class="history-item">${escHtml(e)}</li>`
    ).join("");
}

// ====================================================================================================
// MESSAGES
// ====================================================================================================
// COMPOSE
// ====================================================================================================

async function sendMessage() {
    if (_selectedId === null) return;
    const input  = document.getElementById("compose-text");
    const dirSel = document.getElementById("compose-direction");
    const btn    = document.getElementById("compose-btn");

    const text      = input.value.trim();
    const direction = dirSel.value;
    if (!text) return;

    input.disabled = true;
    btn.disabled   = true;

    // Inbound messages for webchat conversations route through the MAF agent so the agent
    // processes them and writes the response back to KC - exactly like typing in the agent page.
    const MAF_BASE     = String(
        window.__koreSuiteUrls?.koreagent
        || _cachedSuiteUrls()?.koreagent
        || _defaultKoreAgentBase()
    ).replace(/\/$/, "");
    const wcPrefix     = "webchat_";
    const isWebchat    = direction === "inbound" && _selectedExternalId && _selectedExternalId.startsWith(wcPrefix);

    try {
        if (isWebchat) {
            const sessionId = _selectedExternalId.slice(wcPrefix.length);
            const resp = await fetch(`${MAF_BASE}/sessions/${encodeURIComponent(sessionId)}/prompt`, {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({ prompt: text }),
            });
            if (!resp.ok) {
                const err = await resp.text();
                console.error("sendMessage (MAF) failed:", resp.status, err);
                return;
            }
            const data = await resp.json();
            input.value = "";
            // Refresh immediately to show the inbound message, then again when the agent responds.
            await refreshAll();
            _listenForResponse(data.run_id, MAF_BASE);
        } else {
            const resp = await fetch(`/conversations/${_selectedId}/messages`, {
                method:  "POST",
                headers: { "Content-Type": "application/json" },
                body:    JSON.stringify({
                    direction,
                    content:        text,
                    sender_display: "debug-ui",
                }),
            });
            if (!resp.ok) {
                const err = await resp.text();
                console.error("sendMessage (KC) failed:", resp.status, err);
                return;
            }
            input.value = "";
            await refreshAll();
        }
    } catch (e) {
        console.error("sendMessage:", e);
    } finally {
        input.disabled = false;
        btn.disabled   = false;
        input.focus();
    }
}

function _listenForResponse(runId, mafBase) {
    // Subscribe to the MAF run SSE stream and refresh the conversations page when the
    // agent finishes - so both the inbound message and the agent reply become visible.
    const es = new EventSource(`${mafBase}/runs/${encodeURIComponent(runId)}/stream`);
    const done = () => { try { es.close(); } catch (_) {} };
    es.onmessage = async e => {
        try {
            const ev = JSON.parse(e.data);
            if (ev.type === "response" || ev.type === "error") {
                done();
                await refreshAll();
            }
        } catch (_) {}
    };
    es.onerror = done;
    // Safety net - close after 3 minutes regardless.
    setTimeout(done, 180000);
}

async function agentResume() {
    if (_selectedId === null || !_selectedConv) return;

    const agentUrl = (window.__koreSuiteUrls || {}).koreagent;
    if (!agentUrl) {
        window.alert("KoreAgent URL is not known. Is KoreStack running?");
        return;
    }

    // Derive the display name the same way the server does (_display_name in slash_command_handlers_sessions.py).
    const subject    = String(_selectedConv.subject || "").trim();
    const externalId = String(_selectedConv.external_id || "");
    const name = subject
        || (externalId.startsWith("webchat_") ? externalId.slice("webchat_".length) : externalId)
        || "";
    if (!name) {
        window.alert("This conversation has no name.");
        return;
    }

    const btn = document.getElementById("agent-resume-btn");
    btn.disabled = true;
    try {
        const base = agentUrl.replace(/\/$/, "");
        const resp = await fetch(`${base}/sessions/request-switch`, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ name }),
        });
        if (!resp.ok) {
            const err = await resp.text();
            throw new Error(`HTTP ${resp.status}: ${err}`);
        }
        window.location.href = agentUrl;
    } catch (e) {
        console.error("agentResume:", e);
        window.alert(`Agent resume failed: ${e.message}`);
    } finally {
        btn.disabled = false;
    }
}

async function deleteConversation() {
    if (_selectedId === null) return;

    const id  = _selectedId;
    const btn = document.getElementById("delete-conv-btn");
    const ok  = window.confirm(
        `Delete conversation #${id}?\n\nThis permanently removes it from KoreChat.`
    );
    if (!ok) return;

    btn.disabled = true;
    try {
        const resp = await fetch(`/conversations/${id}`, { method: "DELETE" });
        if (!resp.ok) {
            const err = await resp.text();
            throw new Error(`HTTP ${resp.status}: ${err}`);
        }

        _selectedId = null;
        localStorage.removeItem("kc_selected_id");
        document.getElementById("detail").hidden = true;
        document.getElementById("detail-empty").hidden = false;
        await loadConversations();
    } catch (e) {
        console.error("deleteConversation:", e);
        window.alert(`Delete failed: ${e.message}`);
    } finally {
        btn.disabled = false;
    }
}

function toggleNewConvForm() {
    const form = document.getElementById("new-conv-form");
    form.hidden = !form.hidden;
    if (!form.hidden) {
        const input = document.getElementById("new-conv-name");
        input.value = "";
        input.focus();
    }
}

function hideNewConvForm() {
    document.getElementById("new-conv-form").hidden = true;
}

async function createConversation() {
    const input = document.getElementById("new-conv-name");
    const subject = (input?.value || "").trim() || "New conversation";
    const submitBtn = document.getElementById("new-conv-submit");
    if (submitBtn) submitBtn.disabled = true;

    try {
        const resp = await fetch("/conversations", {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({
                channel_type: "webchat",
                profile:      "admin",
                subject,
            }),
        });
        if (!resp.ok) {
            const err = await resp.text();
            throw new Error(`HTTP ${resp.status}: ${err}`);
        }

        hideNewConvForm();
        const conv = await resp.json();
        await loadConversations();
        await selectConversation(conv.id);
    } catch (e) {
        console.error("createConversation:", e);
        window.alert(`Create failed: ${e.message}`);
    } finally {
        if (submitBtn) submitBtn.disabled = false;
    }
}

async function renameConversation() {
    if (_selectedId === null) return;

    const current = _allConversations.find(c => c.id === _selectedId);
    const nextSubject = window.prompt("Rename conversation:", current?.subject || "");
    if (nextSubject === null) return;

    try {
        const resp = await fetch(`/conversations/${_selectedId}`, {
            method:  "PATCH",
            headers: { "Content-Type": "application/json" },
            body:    JSON.stringify({ subject: nextSubject.trim() }),
        });
        if (!resp.ok) {
            const err = await resp.text();
            throw new Error(`HTTP ${resp.status}: ${err}`);
        }

        await refreshAll();
    } catch (e) {
        console.error("renameConversation:", e);
        window.alert(`Rename failed: ${e.message}`);
    }
}

// ====================================================================================================

async function reloadMessages() {
    if (_selectedId === null) return;
    try {
        const r    = await fetch(`/conversations/${_selectedId}/messages?limit=1000`);
        const msgs = r.ok ? await r.json() : [];
        renderMessages(msgs);
    } catch (e) {
        console.error("reloadMessages:", e);
    }
}

function renderMessages(msgs) {
    const showSummarised = document.getElementById("chk-summarised").checked;
    const visible        = showSummarised ? msgs : msgs.filter(m => !m.summarised);

    document.getElementById("msg-count").textContent = msgs.length;

    if (visible.length === 0) {
        document.getElementById("messages-body").innerHTML =
            "<div class='empty-note'>No messages.</div>";
        return;
    }

    document.getElementById("messages-body").innerHTML = visible.map(m => {
        const summarisedClass = m.summarised ? " summarised-row" : "";
        const ts = formatDateTime(m.created_at);
        return `
<div class="msg-row${summarisedClass}">
    <span class="msg-id">#${m.id}</span>
    <span>
        ${pill(m.direction)}
    </span>
    <span class="msg-content">${escHtml(m.content)}</span>
    <span class="msg-time">${ts}</span>
    <span class="msg-flags">
        ${pill(m.status)}
        ${m.summarised ? '<span class="kcui-tag kcui-tag--pill kcui-tag--dim">summ</span>' : ""}
    </span>
</div>`;
    }).join("");
}

// ====================================================================================================
// EVENTS
// ====================================================================================================

function renderEvents(evts) {
    document.getElementById("evt-count").textContent = evts.length;

    if (evts.length === 0) {
        document.getElementById("events-body").innerHTML =
            "<div class='empty-note'>No events.</div>";
        return;
    }

    const hdr = `
<table class="evt-table">
<thead>
<tr>
    <th>#</th>
    <th>type</th>
    <th>status</th>
    <th>priority</th>
    <th>claimed_by</th>
    <th>created_at</th>
    <th>completed_at</th>
    <th>payload</th>
</tr>
</thead>
<tbody>
`;
    const rows = evts.map(e => {
        let payload = "";
        try {
            const p = typeof e.payload === "string" ? JSON.parse(e.payload) : e.payload;
            if (p && Object.keys(p).length > 0) {
                payload = escHtml(JSON.stringify(p, null, 2));
            }
        } catch { /* ignore */ }
        return `
<tr>
    <td class="mono">${e.id}</td>
    <td>${escHtml(e.event_type)}</td>
    <td>${pill(e.status)}</td>
    <td>${e.priority ?? 0}</td>
    <td class="evt-meta">${escHtml(e.claimed_by || "-")}</td>
    <td class="evt-meta evt-meta--nowrap">${formatDateTime(e.created_at)}</td>
    <td class="evt-meta evt-meta--nowrap">${formatDateTime(e.completed_at)}</td>
    <td><pre class="evt-payload">${payload}</pre></td>
</tr>`;
    }).join("");

    document.getElementById("events-body").innerHTML = hdr + rows + "</tbody></table>";
}

// ====================================================================================================
// REFRESH
// ====================================================================================================

async function refreshAll() {
    await Promise.all([
        loadStatus(),
        loadConversations(),
        _selectedId !== null ? selectConversation(_selectedId) : Promise.resolve(),
    ]);
}

// ====================================================================================================
// SSE PUSH
// ====================================================================================================
// The /stream endpoint pushes a small notification whenever a conversation or message changes.
// On each push the client makes targeted refresh calls rather than a full blind poll.

let _sseReconnectTimer = null;

function _connectSSE() {
    if (_sse) { try { _sse.close(); } catch (_) {} }
    _sse = new EventSource("/stream");

    _sse.onmessage = async e => {
        try {
            const ev = JSON.parse(e.data);
            const cid = ev.conversation_id ?? null;

            if (ev.type === "conv_deleted") {
                // Remove from list; clear detail if it was selected.
                _allConversations = _allConversations.filter(c => c.id !== cid);
                _cacheSet("kc_conv_list", _allConversations);
                renderConversationListState();
                if (_selectedId === cid) {
                    _selectedId         = null;
                    _selectedExternalId = null;
                    document.getElementById("detail-empty").hidden = false;
                    document.getElementById("detail").hidden        = true;
                }
                return;
            }

            // For all other events: reload the conversation list (status/subject may have changed)
            // and reload the detail panel if the affected conversation is currently selected.
            await loadConversations();
            if (cid !== null && cid === _selectedId) {
                await selectConversation(_selectedId);
            }
        } catch (_) {}
    };

    _sse.onerror = () => {
        // On error, close and reconnect after 3 seconds so a KC restart heals automatically.
        try { _sse.close(); } catch (_) {}
        _sse = null;
        if (_sseReconnectTimer) clearTimeout(_sseReconnectTimer);
        _sseReconnectTimer = setTimeout(_connectSSE, 3000);
    };
}

function toggleAuto() {
    const on = document.getElementById("chk-auto").checked;
    if (on) {
        _connectSSE();
        if (!_autoInterval) _autoInterval = setInterval(refreshAll, AUTO_REFRESH_MS);
    } else {
        if (_sse)          { try { _sse.close(); } catch (_) {} _sse = null; }
        if (_autoInterval) { clearInterval(_autoInterval); _autoInterval = null; }
    }
}

// ====================================================================================================
// DEFAULT CHAT CULLING
// ====================================================================================================

function _normalizeChatAgeDays(raw) {
    const n = Number.parseInt(String(raw ?? ""), 10);
    if ([1, 3, 7, 30].includes(n)) return n;
    return DEFAULT_CHAT_AGE_FALLBACK_DAYS;
}

function _selectedChatAgeDays() {
    const sel = document.getElementById("max-default-chat-age");
    return _normalizeChatAgeDays(sel?.value);
}

function _setChatAgeSelect(days) {
    const sel = document.getElementById("max-default-chat-age");
    if (!sel) return;
    sel.value = String(_normalizeChatAgeDays(days));
}

function initDefaultChatAgeCulling() {
    const saved = _normalizeChatAgeDays(localStorage.getItem(DEFAULT_CHAT_AGE_STORAGE_KEY));
    _setChatAgeSelect(saved);
    void runDefaultChatCull(saved);

    if (_defaultCullInterval) clearInterval(_defaultCullInterval);
    _defaultCullInterval = setInterval(() => {
        void runDefaultChatCull(_selectedChatAgeDays());
    }, DEFAULT_CHAT_AGE_CULL_MS);
}

function onMaxDefaultChatAgeChanged() {
    const days = _selectedChatAgeDays();
    localStorage.setItem(DEFAULT_CHAT_AGE_STORAGE_KEY, String(days));
    if (_selectedConv) renderMeta(_selectedConv);
    void runDefaultChatCull(days);
}

async function runDefaultChatCull(maxDefaultChatAgeDays) {
    try {
        const resp = await fetch("/maintenance/default-chat-cull", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ max_default_chat_age_days: _normalizeChatAgeDays(maxDefaultChatAgeDays) }),
        });
        if (!resp.ok) {
            const err = await resp.text().catch(() => "");
            console.warn("default chat cull failed:", resp.status, err);
            return;
        }
        const result = await resp.json();
        if ((result.deleted_count || 0) > 0) {
            await refreshAll();
        }
    } catch (e) {
        console.warn("default chat cull failed:", e);
    }
}

// ====================================================================================================
// DRAG SPLITTER
// ====================================================================================================

function initSplitter() {
    const splitter = document.getElementById("splitter");
    const sidebar  = document.getElementById("sidebar");
    const grid     = document.getElementById("main-grid");

    splitter.addEventListener("mousedown", e => {
        _dragStartX = e.clientX;
        _dragStartW = sidebar.getBoundingClientRect().width;
        document.body.style.userSelect = "none";
        document.body.style.cursor     = "col-resize";
    });

    document.addEventListener("mousemove", e => {
        if (_dragStartX === null) return;
        const delta = e.clientX - _dragStartX;
        const newW  = Math.max(160, Math.min(600, _dragStartW + delta));
        grid.style.gridTemplateColumns = `${newW}px 4px 1fr`;
        document.documentElement.style.setProperty("--sidebar-w", `${newW}px`);
    });

    document.addEventListener("mouseup", () => {
        if (_dragStartX === null) return;
        _dragStartX = null;
        _dragStartW = null;
        document.body.style.userSelect = "";
        document.body.style.cursor     = "";
    });
}

// ====================================================================================================
// HELPERS
// ====================================================================================================

function escHtml(s) {
    if (s === null || s === undefined) return "";
    return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

// Color map for kcui-tag — maps status/direction/role values to --color modifier
const TAG_COLORS = {
    // conversation status
    awaiting_inbound: "accent",
    active:           "accent",
    waiting_agent:    "warning",
    agent_processing: "info",
    archived:         "dim",
    deleted:          "danger",
    // message direction
    inbound:          "warning",
    outbound:         "info",
    // message/event status
    pending:          "warning",
    claimed:          "info",
    completed:        "accent",
    failed:           "danger",
    // profile / role
    admin:            "warning",
    external:         "dim",
    readonly:         "danger",
};

function pill(text, _unused) {
    const color = TAG_COLORS[text] || "dim";
    return `<span class="kcui-tag kcui-tag--pill kcui-tag--${color}">${escHtml(text)}</span>`;
}

function formatDateTime(iso) {
    if (!iso) return "-";
    try {
        const d    = new Date(iso);
        const date = d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" });
        const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        return `${date} ${time}`;
    } catch {
        return iso;
    }
}

function getDisplayStatus(status) {
    if (status === "active") {
        return { label: "awaiting_inbound", className: "awaiting_inbound" };
    }
    return { label: status || "-", className: status || "active" };
}
