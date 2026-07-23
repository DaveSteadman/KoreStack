import { KCUI_STORAGE_KEYS } from "/ui-elements/assets/js/constants.js";

export const DEFAULT_CHAT_AGE_STORAGE_KEY = "kc_max_default_chat_age_days";
export const DEFAULT_CHAT_AGE_FALLBACK_DAYS = 7;
export const DEFAULT_CHAT_AGE_CULL_MS     = 60 * 60 * 1000;
export const AUTO_REFRESH_MS              = 30_000;
export const PANEL_COLLAPSE_STORAGE_KEY   = "kc_collapsed_panels";
export const SIDEBAR_WIDTH_STORAGE_KEY    = "kc_sidebar_width";

const TAG_COLORS = {
    awaiting_inbound: "accent",
    active:           "accent",
    waiting_agent:    "warning",
    agent_processing: "info",
    archived:         "dim",
    deleted:          "danger",
    inbound:          "warning",
    outbound:         "info",
    pending:          "warning",
    claimed:          "info",
    completed:        "accent",
    failed:           "danger",
    admin:            "warning",
    external:         "dim",
    readonly:         "danger",
};

export function cacheSet(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {}
}

export function cacheGet(key) {
    try {
        const raw = localStorage.getItem(key);
        return raw ? JSON.parse(raw) : null;
    } catch (_) {
        return null;
    }
}

export function cachedSuiteUrls() {
    try {
        const raw = localStorage.getItem(KCUI_STORAGE_KEYS.suiteUrls);
        return raw ? JSON.parse(raw) : null;
    } catch (_) {
        return null;
    }
}

export function defaultKoreAgentBase() {
    return (
        window.__koreSuiteUrls?.koreagent
        || cachedSuiteUrls()?.koreagent
        || null
    );
}

export function escHtml(value) {
    if (value === null || value === undefined) return "";
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

export function pill(text) {
    const color = TAG_COLORS[text] || "dim";
    return `<span class="kcui-tag kcui-tag--pill kcui-tag--${color}">${escHtml(text)}</span>`;
}

export function formatDateTime(iso) {
    if (!iso) return "-";
    try {
        const d    = new Date(iso);
        const date = d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" });
        const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        return `${date} ${time}`;
    } catch (_) {
        return iso;
    }
}

export function getDisplayStatus(status) {
    if (status === "active") {
        return { label: "awaiting_inbound", className: "awaiting_inbound" };
    }
    return { label: status || "-", className: status || "active" };
}

export function normalizeChatAgeDays(raw) {
    const n = Number.parseInt(String(raw ?? ""), 10);
    if ([1, 3, 7, 30].includes(n)) return n;
    return DEFAULT_CHAT_AGE_FALLBACK_DAYS;
}
