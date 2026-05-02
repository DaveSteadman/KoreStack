/**
 * svg_icons.js — single source of truth for all SVG icon markup in UIElements.
 *
 * Static string constants: fixed-size glyphs used by UI chrome (appbar, tree, filelist).
 * Parameterised functions: service and file-type icons whose size is caller-controlled.
 *
 * Consumers:
 *   UIElements/icons.js          — service registry + fileIconForPath
 *   UIElements/appbar.js         — appbar chrome icons
 *   KoreDocs korefile/js/*.js    — tree/filelist action icons
 */

// ── Appbar chrome icons ───────────────────────────────────────────────────────

export const APPBAR_BRAND_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
  <rect x="3" y="3" width="8" height="8" rx="1.5" fill="currentColor" opacity=".95"/>
  <rect x="13" y="3" width="8" height="8" rx="1.5" fill="currentColor" opacity=".65"/>
  <rect x="3" y="13" width="8" height="8" rx="1.5" fill="currentColor" opacity=".65"/>
  <rect x="13" y="13" width="8" height="8" rx="1.5" fill="currentColor" opacity=".35"/>
</svg>`;

export const APPBAR_HOME_ICON = `<svg viewBox="0 0 20 20" fill="none" width="13" height="13" aria-hidden="true">
  <path d="M2 5a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5z"
        stroke="currentColor" stroke-width="1.5"/>
</svg>`;

// ── UI action icons (fixed 12 px) ─────────────────────────────────────────────

export const CHEVRON_SVG = `<svg viewBox="0 0 20 20" fill="none" width="12" height="12">
  <path d="M7 5l5 5-5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
</svg>`;

export const EDIT_SVG = `<svg viewBox="0 0 20 20" fill="none" width="12" height="12">
  <path d="M14.5 3.5a2.121 2.121 0 0 1 3 3L6 18H3v-3L14.5 3.5z" stroke="currentColor" stroke-width="1.5"/>
</svg>`;

export const TRASH_SVG = `<svg viewBox="0 0 20 20" fill="none" width="12" height="12">
  <path d="M3 6h14M8 6V4h4v2M5 6l1 11h8l1-11" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
</svg>`;

export const MOVE_SVG = `<svg viewBox="0 0 20 20" fill="none" width="12" height="12">
  <path d="M2 5a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5z" stroke="currentColor" stroke-width="1.4"/>
  <path d="M10 9v6M7 12l3 3 3-3" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
</svg>`;

export const OPEN_SVG = `<svg viewBox="0 0 20 20" fill="none" width="12" height="12">
  <path d="M4 4h5v2H6v8h8v-3h2v5H4V4z" fill="currentColor"/>
  <path d="M10 10l6-6M11 4h5v5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
</svg>`;

// ── UI action icons (fixed 14 px) ─────────────────────────────────────────────

export const DOWNLOAD_SVG = `<svg viewBox="0 0 20 20" fill="none" width="14" height="14">
  <path d="M10 3v10m0 0-3-3m3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M3 14v2a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
</svg>`;

// ── Service icons (size-parameterised) ────────────────────────────────────────

export function koreStackIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M4 5.1 10 2.8l6 2.3L10 7.5 4 5.1Z" stroke="currentColor" stroke-width="1.45" stroke-linejoin="round"/>
      <path d="M4 9.3 10 7l6 2.3-6 2.4-6-2.4Z" stroke="currentColor" stroke-width="1.45" stroke-linejoin="round" opacity=".88"/>
      <path d="M4 13.5 10 11.2l6 2.3-6 2.4-6-2.4Z" stroke="currentColor" stroke-width="1.45" stroke-linejoin="round" opacity=".72"/>
    </svg>`;
}

export function koreAgentIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="4.5" y="4.5" width="11" height="11" rx="3" stroke="currentColor" stroke-width="1.5"/>
      <path d="M10 2.8v1.7M3.2 8.5h1.3M15.5 8.5h1.3M3.2 11.5h1.3M15.5 11.5h1.3" stroke="currentColor" stroke-width="1.35" stroke-linecap="round"/>
      <path d="M10 7.1 11 9.05l2.2.32-1.6 1.55.38 2.18L10 12.05 8.02 13.1l.38-2.18-1.6-1.55L9 9.05 10 7.1Z" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round"/>
    </svg>`;
}

export function koreDataIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <ellipse cx="10" cy="5" rx="5.5" ry="2.5" stroke="currentColor" stroke-width="1.4"/>
      <path d="M4.5 5v7c0 1.4 2.46 2.5 5.5 2.5s5.5-1.1 5.5-2.5V5" stroke="currentColor" stroke-width="1.4"/>
      <path d="M4.5 8.5c0 1.4 2.46 2.5 5.5 2.5s5.5-1.1 5.5-2.5" stroke="currentColor" stroke-width="1.4"/>
    </svg>`;
}

export function koreDocsIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M6 2.8h5.7L15.2 6v10.2a1.8 1.8 0 0 1-1.8 1.8H6a1.8 1.8 0 0 1-1.8-1.8V4.6A1.8 1.8 0 0 1 6 2.8Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
      <path d="M11.7 2.8V6h3.5" stroke="currentColor" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M7 9h6M7 11.9h6M7 14.8h3.9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
    </svg>`;
}

export function koreCommsIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="2.8" y="4.8" width="14.4" height="10.4" rx="1.8" stroke="currentColor" stroke-width="1.45"/>
      <path d="M3.8 6 10 10.5 16.2 6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
}

export function koreChatIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M4.6 4.5h6.7A2.1 2.1 0 0 1 13.4 6.6v3.2a2.1 2.1 0 0 1-2.1 2.1H8.1l-2.7 2v-2H4.6a2.1 2.1 0 0 1-2.1-2.1V6.6a2.1 2.1 0 0 1 2.1-2.1Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
      <path d="M9.5 7.6h5.8A2.2 2.2 0 0 1 17.5 9.8v3a2.2 2.2 0 0 1-2.2 2.2h-.7v2l-2.7-2H9.5a2.2 2.2 0 0 1-2-1.2" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
    </svg>`;
}

export function koreCodeIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <path d="M8 6.8 4.8 10 8 13.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="square" stroke-linejoin="miter"/>
    <path d="M12 6.8 15.2 10 12 13.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="square" stroke-linejoin="miter"/>
    <path d="M10.9 5.9 9.1 14.1" stroke="currentColor" stroke-width="1.5" stroke-linecap="square"/>
  </svg>`;
}

// ── File-type icons (size-parameterised) ──────────────────────────────────────

export function koreDocFileIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="3" y="2" width="14" height="16" rx="2" stroke="currentColor" stroke-width="1.5"/>
      <line x1="6" y1="7" x2="14" y2="7" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
      <line x1="6" y1="10" x2="14" y2="10" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
      <line x1="6" y1="13" x2="11" y2="13" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
    </svg>`;
}

export function koreSheetFileIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="2" y="2" width="16" height="16" rx="2" stroke="currentColor" stroke-width="1.5"/>
      <line x1="2" y1="8" x2="18" y2="8" stroke="currentColor" stroke-width="1.2"/>
      <line x1="2" y1="13" x2="18" y2="13" stroke="currentColor" stroke-width="1.2"/>
      <line x1="8" y1="2" x2="8" y2="18" stroke="currentColor" stroke-width="1.2"/>
      <line x1="13" y1="2" x2="13" y2="18" stroke="currentColor" stroke-width="1.2"/>
    </svg>`;
}

export function koreDiagFileIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <circle cx="4" cy="10" r="2.5" stroke="currentColor" stroke-width="1.5"/>
    <circle cx="16" cy="4" r="2.5" stroke="currentColor" stroke-width="1.5"/>
    <circle cx="16" cy="16" r="2.5" stroke="currentColor" stroke-width="1.5"/>
    <line x1="6.2" y1="9" x2="13.8" y2="5" stroke="currentColor" stroke-width="1.3"/>
    <line x1="6.2" y1="11" x2="13.8" y2="15" stroke="currentColor" stroke-width="1.3"/>
  </svg>`;
}

export function pyFileIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <rect x="3" y="2" width="14" height="16" rx="2" stroke="currentColor" stroke-width="1.5"/>
    <path d="M7 7h2.5a1.5 1.5 0 0 1 0 3H7M13 13h-2.5a1.5 1.5 0 0 1 0-3H13" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
  </svg>`;
}

export function jsFileIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <rect x="3" y="2" width="14" height="16" rx="2" stroke="currentColor" stroke-width="1.5"/>
    <path d="M7.5 8v4.5a1.5 1.5 0 0 1-3 0M11 8v4a1.5 1.5 0 0 0 3 0V8" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

export function jsonFileIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <rect x="3" y="2" width="14" height="16" rx="2" stroke="currentColor" stroke-width="1.5"/>
    <path d="M7.5 7.5A1.5 1.5 0 0 0 6 9v2a1.5 1.5 0 0 0 1.5 1.5M12.5 7.5A1.5 1.5 0 0 1 14 9v2a1.5 1.5 0 0 1-1.5 1.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
    <circle cx="10" cy="10" r="1" fill="currentColor"/>
  </svg>`;
}

export function htmlFileIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <rect x="3" y="2" width="14" height="16" rx="2" stroke="currentColor" stroke-width="1.5"/>
    <path d="M8 8.5 6 10l2 1.5M12 8.5l2 1.5-2 1.5M10.5 7.5l-1 5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

export function cssFileIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <rect x="3" y="2" width="14" height="16" rx="2" stroke="currentColor" stroke-width="1.5"/>
    <path d="M12.5 8a2 2 0 0 0-4 0c0 1 1 1.5 2 2s2 1 2 2a2 2 0 0 1-4 0" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
  </svg>`;
}

export function genericFileIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
    <path d="M6 2.8h5.7L15.2 6v10.2a1.8 1.8 0 0 1-1.8 1.8H6a1.8 1.8 0 0 1-1.8-1.8V4.6A1.8 1.8 0 0 1 6 2.8Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
    <path d="M11.7 2.8V6h3.5" stroke="currentColor" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}
