/**
 * colors.js — KoreCommonUI color definitions.
 *
 * Single source of truth for every color used in the UI.  Two namespaces:
 *
 *   SERVICE  — one accent per service; tints topbar entries, app bars, and
 *              any service-branded chrome.
 *
 *   UI       — semantic colors for buttons, labels, and status indicators.
 *              Synced with the equivalent tokens in tokens.css.
 *
 * Rule: no page or component may define a color literal.  If a new color is
 * needed, define it here first, then reference it.
 */

// ── Service colors ────────────────────────────────────────────────────────────
// Must stay in sync with --svc-color-* in tokens.css.

export const SERVICE = {
  stack:  '#2dd4bf',
  agent:  '#4af77a',
  chat:   '#f0c060',
  data:   '#c4a050',
  docs:   '#6eb5ff',
  code:   '#fb923c',
  comms:  '#f472b6',
  graph:  '#c084fc',
};

// Canonical key aliases — full Kore names and sub-app variants
const SERVICE_ALIASES = {
  korestack:  'stack',
  koreagent:  'agent',
  korechat:   'chat',
  koredata:   'data',
  koredocs:   'docs',  koredoc:    'docs',  koresheet:  'docs',  kodiag:     'docs',  korefile:   'docs',
  korecode:   'code',
  korecomms:  'comms',
  koregraph:  'graph',
};

// ── Semantic UI colors ────────────────────────────────────────────────────────
// Used by buttons, labels, and status indicators.
// Must stay in sync with --success / --warning / --danger / --info in tokens.css.

export const UI = {
  success: '#4af77a',   // positive action, confirmation          → --success
  warning: '#f0c060',   // caution, pending state                 → --warning
  danger:  '#ff5f5f',   // destructive action, error              → --danger
  info:    '#6eb5ff',   // neutral information                    → --info
  muted:   '#4e5466',   // disabled, secondary, de-emphasised
};

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Resolve an alias or full Kore key to a canonical short key.
 * e.g. 'koredocs' → 'docs', 'koresheet' → 'docs'
 */
export function resolveServiceKey(key) {
  if (!key) return null;
  const k = String(key).trim().toLowerCase();
  return SERVICE_ALIASES[k] ?? k;
}

/** Return the accent color for a service key, or null if not found. */
export function serviceColor(key) {
  return SERVICE[resolveServiceKey(key)] ?? null;
}

/**
 * Apply a service's accent color as CSS custom properties on a DOM element.
 * Defaults to documentElement when no target is given.
 */
export function applyServiceColor(target, key) {
  const color = serviceColor(key);
  const el    = target ?? document?.documentElement ?? null;
  if (!color || !el) return color;

  el.style.setProperty('--accent',          color);
  el.style.setProperty('--kc-entry-color',  color);

  if (el === document?.documentElement && document.body) {
    document.body.dataset.kcService = resolveServiceKey(key) ?? '';
  }
  return color;
}
