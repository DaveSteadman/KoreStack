/**
 * icons.js — shared icon registry for KoreCommonUI.
 *
 * Icons are local SVG files under assets/icons/<set-name>/.
 */

const DEFAULT_ICON_SET = 'dazzle-line';

export const ICON_SETS = Object.freeze({
  dazzleLine: DEFAULT_ICON_SET,
});

// One icon file per app and per KoreDocs file type.
export const ICON_FILES = Object.freeze({
  korestack: 'layer-group-svgrepo-com',
  koreagent: 'circuit-svgrepo-com',
  koredata: 'globe-alt-svgrepo-com',
  korefeed: 'square-rss-svgrepo-com',
  korelibrary: 'book-user-svgrepo-com',
  korereference: 'graduation-hat-alt-1-svgrepo-com',
  korerag: 'truck-svgrepo-com',
  koregraph: 'chart-network-svgrepo-com',
  koredocs: 'pen-square-svgrepo-com',
  korecode: 'square-terminal-svgrepo-com',
  korecomms: 'send-alt-1-svgrepo-com',
  korechat: 'message-circle-chat-svgrepo-com',
  koredoc: 'text-svgrepo-com',
  koresheet: 'table-list-alt-svgrepo-com',
  kodiag: 'draw-square-svgrepo-com',
  korefile: 'class-16-svgrepo-com',
});

function attr(name, value) {
  if (value === undefined || value === null || value === '') return '';
  const escaped = String(value).replace(/"/g, '&quot;');
  return ` ${name}="${escaped}"`;
}

export function iconSetUrl(iconName, setName = DEFAULT_ICON_SET) {
  return `../icons/${setName}/${iconName}.svg`;
}

export function iconSetImg(iconName, options = {}) {
  const {
    setName = DEFAULT_ICON_SET,
    size,
    className = '',
    alt = '',
    loading,
    decoding,
  } = options;
  const src = iconSetUrl(iconName, setName);
  const style = size ? `width:${size}px;height:${size}px;` : '';
  return `<img${attr('class', className)}${attr('src', src)}${attr('alt', alt)}${attr('loading', loading)}${attr('decoding', decoding)}${attr('style', style)}>`;
}

export function iconSetMask(iconName, options = {}) {
  const {
    setName = DEFAULT_ICON_SET,
    size,
    className = '',
    color = 'currentColor',
    alt,
  } = options;
  const src = iconSetUrl(iconName, setName);
  const dim = size ? `${size}px` : '1em';
  const style = [
    'display:inline-block',
    `width:${dim}`,
    `height:${dim}`,
    `background-color:${color}`,
    `-webkit-mask-image:url('${src}')`,
    '-webkit-mask-repeat:no-repeat',
    '-webkit-mask-position:center',
    '-webkit-mask-size:contain',
    `mask-image:url('${src}')`,
    'mask-repeat:no-repeat',
    'mask-position:center',
    'mask-size:contain',
  ].join(';');
  const ariaHidden = alt ? '' : 'true';
  return `<span${attr('class', className)}${attr('style', style)}${attr('role', 'img')}${attr('aria-label', alt)}${attr('aria-hidden', ariaHidden)}></span>`;
}

function makePackIcon(iconName) {
  return (size = 12) => iconSetMask(iconName, {
    setName: DEFAULT_ICON_SET,
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
export const KOREGRAPH_ICON = makePackIcon(ICON_FILES.koregraph);
export const KOREDOCS_ICON = makePackIcon(ICON_FILES.koredocs);
export const KORECODE_ICON = makePackIcon(ICON_FILES.korecode);
export const KORECOMMS_ICON = makePackIcon(ICON_FILES.korecomms);
export const KORECHAT_ICON = makePackIcon(ICON_FILES.korechat);

export const KOREDOC_FILE_ICON = makePackIcon(ICON_FILES.koredoc);
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
  koregraph: KOREGRAPH_ICON,
  koredocs: KOREDOCS_ICON,
  korecode: KORECODE_ICON,
  korecomms: KORECOMMS_ICON,
  korechat: KORECHAT_ICON,
  koredoc: KOREDOC_FILE_ICON,
  koresheet: KORESHEET_FILE_ICON,
  kodiag: KODIAG_FILE_ICON,
  korefile: KOREFILE_ICON,
};

/**
 * Returns an SVG icon for a given file path based on extension.
 * KoreDocs file types are explicit; unknowns fall back to KOREFILE_ICON.
 */
export function fileIconForPath(path, size = 12) {
  if (!path) return KOREFILE_ICON(size);
  const lower = path.toLowerCase();
  if (lower.endsWith('.koredoc') || lower.endsWith('.md') || lower.endsWith('.markdown') || lower.endsWith('.txt')) return KOREDOC_FILE_ICON(size);
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

