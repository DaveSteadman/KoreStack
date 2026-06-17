/**
 * icons.js — shared icon registry for UIElements shell primitives.
 *
 * Icons are file-based SVGs imported under UIElements/assets/icons/<set-name>.
 */

import { SVG_ICON_SETS, svgIconUrl, svgIconImg, svgIconMask } from './svgicons.js';

export { fileIconForPath };

// Back-compat alias.
export const ICON_SETS = SVG_ICON_SETS;

// One icon file per app and per KoreDocs file type.
export const ICON_FILES = Object.freeze({
  korestack: 'layer-group-svgrepo-com',
  koreagent: 'circuit-svgrepo-com',
  koredata: 'globe-alt-svgrepo-com',
  korefeed: 'square-rss-svgrepo-com',
  korelibrary: 'book-user-svgrepo-com',
  korereference: 'graduation-hat-alt-1-svgrepo-com',
  korerag: 'truck-svgrepo-com',
  korescrape: 'dna-svgrepo-com',
  koregraph: 'chart-network-svgrepo-com',
  koredocs: 'pen-square-svgrepo-com',
  korecode: 'square-terminal-svgrepo-com',
  korecomms: 'send-alt-1-svgrepo-com',
  korechat: 'message-circle-chat-svgrepo-com',
  koredoc: 'text-size-svgrepo-com',
  textedit: 'text-svgrepo-com',
  koresheet: 'table-list-alt-svgrepo-com',
  kodiag: 'draw-square-svgrepo-com',
  korefile: 'class-16-svgrepo-com',
});

function makePackIcon(iconName) {
  return (size = 12) => svgIconMask(iconName, {
    setName: 'dazzle-line',
    size,
    className: 'kcui-icon kcui-icon--mask',
    color: 'var(--kcui-icon-color, currentColor)',
    alt: '',
  });
}

export const KORESTACK_ICON = makePackIcon(ICON_FILES.korestack);
export const KOREAGENT_ICON = makePackIcon(ICON_FILES.koreagent);
export const KOREDATA_ICON = makePackIcon(ICON_FILES.koredata);
export const KOREFEED_ICON = makePackIcon(ICON_FILES.korefeed);
export const KORELIBRARY_ICON = makePackIcon(ICON_FILES.korelibrary);
export const KOREREFERENCE_ICON = makePackIcon(ICON_FILES.korereference);
export const KORERAG_ICON = makePackIcon(ICON_FILES.korerag);
export const KORESCRAPE_ICON = makePackIcon(ICON_FILES.korescrape);
export const KOREGRAPH_ICON = makePackIcon(ICON_FILES.koregraph);
export const KOREDOCS_ICON = makePackIcon(ICON_FILES.koredocs);
export const KORECODE_ICON = makePackIcon(ICON_FILES.korecode);
export const KORECOMMS_ICON = makePackIcon(ICON_FILES.korecomms);
export const KORECHAT_ICON = makePackIcon(ICON_FILES.korechat);

export const KOREDOC_FILE_ICON = makePackIcon(ICON_FILES.koredoc);
export const TEXTEDIT_FILE_ICON = makePackIcon(ICON_FILES.textedit);
export const KORESHEET_FILE_ICON = makePackIcon(ICON_FILES.koresheet);
export const KODIAG_FILE_ICON = makePackIcon(ICON_FILES.kodiag);
export const KOREFILE_ICON = makePackIcon(ICON_FILES.korefile);

export const SUITE_ICONS = {
  korestack: KORESTACK_ICON,
  koreagent: KOREAGENT_ICON,
  koredata: KOREDATA_ICON,
  korefeed: KOREFEED_ICON,
  korelibrary: KORELIBRARY_ICON,
  korereference: KOREREFERENCE_ICON,
  korerag: KORERAG_ICON,
  korescrape: KORESCRAPE_ICON,
  koregraph: KOREGRAPH_ICON,
  koredocs: KOREDOCS_ICON,
  korecode: KORECODE_ICON,
  korecomms: KORECOMMS_ICON,
  korechat: KORECHAT_ICON,
  koredoc: KOREDOC_FILE_ICON,
  textedit: TEXTEDIT_FILE_ICON,
  koresheet: KORESHEET_FILE_ICON,
  kodiag: KODIAG_FILE_ICON,
  korefile: KOREFILE_ICON,
};

/**
 * Returns an SVG icon for a given file path based on extension.
 * KoreDocs file types are explicit; unknowns fall back to KOREFILE_ICON.
 */
function fileIconForPath(path, size = 12) {
  if (!path) return KOREFILE_ICON(size);
  const lower = path.toLowerCase();
  if (lower.endsWith('.txt')) return TEXTEDIT_FILE_ICON(size);
  if (lower.endsWith('.koredoc') || lower.endsWith('.md') || lower.endsWith('.markdown')) return KOREDOC_FILE_ICON(size);
  if (lower.endsWith('.koresheet') || lower.endsWith('.csv') || lower.endsWith('.tsv') || lower.endsWith('.xlsx')) return KORESHEET_FILE_ICON(size);
  if (lower.endsWith('.kodiag') || lower.endsWith('.drawio') || lower.endsWith('.mermaid')) return KODIAG_FILE_ICON(size);
  return KOREFILE_ICON(size);
}

export function resolveIcon(icons, key, size) {
  const icon = icons[key];
  if (typeof icon === 'function') return icon(size);
  if (typeof icon === 'string') return icon;
  return '';
}

// Back-compat aliases so older imports continue to work.
export function iconSetUrl(iconName, setName = 'dazzle-line') {
  return svgIconUrl(iconName, setName);
}

export function iconSetImg(iconName, options = {}) {
  return svgIconImg(iconName, options);
}

export function iconSetMask(iconName, options = {}) {
  return svgIconMask(iconName, options);
}

