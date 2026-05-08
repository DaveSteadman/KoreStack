/**
 * svgicons.js — helpers for file-based SVG icon packs.
 *
 * Works with icons mirrored under /ui-elements/assets/icons/<set-name>/.
 */

export const SVG_ICON_SETS = Object.freeze({
  'dazzle-line': '/ui-elements/assets/icons/dazzle-line',
});

function sanitizeIconName(name) {
  return String(name ?? '')
    .trim()
    .toLowerCase()
    .replace(/\.svg$/i, '')
    .replace(/[^a-z0-9-_]+/g, '-')
    .replace(/-+/g, '-');
}

function escapeAttr(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

export function svgIconUrl(iconName, setName = 'dazzle-line') {
  const base = SVG_ICON_SETS[setName];
  const safeName = sanitizeIconName(iconName);
  if (!base || !safeName) return '';
  return `${base}/${safeName}.svg`;
}

export function svgIconImg(iconName, options = {}) {
  const {
    setName = 'dazzle-line',
    size = 16,
    className = 'kcui-icon',
    alt = '',
  } = options;

  const src = svgIconUrl(iconName, setName);
  if (!src) return '';

  const px = Number.isFinite(size) ? String(size) : '16';
  return `<img src="${escapeAttr(src)}" class="${escapeAttr(className)}" width="${px}" height="${px}" alt="${escapeAttr(alt)}" loading="lazy" decoding="async" />`;
}

/**
 * Render icon via CSS mask so draw color can be controlled with CSS/option.
 */
export function svgIconMask(iconName, options = {}) {
  const {
    setName = 'dazzle-line',
    size = 16,
    className = 'kcui-icon kcui-icon--mask',
    color = 'var(--kcui-icon-color, currentColor)',
    alt = '',
  } = options;

  const src = svgIconUrl(iconName, setName);
  if (!src) return '';

  const px = Number.isFinite(size) ? String(size) : '16';
  const safeAlt = escapeAttr(alt);
  const a11y = safeAlt ? `role="img" aria-label="${safeAlt}"` : 'aria-hidden="true"';
  const style = [
    `display:inline-block`,
    `width:${px}px`,
    `height:${px}px`,
    `background-color:${escapeAttr(color)}`,
    `-webkit-mask:url('${escapeAttr(src)}') center / contain no-repeat`,
    `mask:url('${escapeAttr(src)}') center / contain no-repeat`,
    `vertical-align:middle`,
    `flex:0 0 ${px}px`,
  ].join(';');

  return `<span class="${escapeAttr(className)}" ${a11y} style="${style}"></span>`;
}

/**
 * Set an element's contents to an SVG icon <img>.
 */
export function mountSvgIcon(element, iconName, options = {}) {
  if (!element) return null;
  const { render = 'mask', ...rest } = options;
  element.innerHTML = render === 'img' ? svgIconImg(iconName, rest) : svgIconMask(iconName, rest);
  return element;
}

/**
 * Render a small gallery into a container from icon names.
 */
export function renderSvgIconGallery(container, iconNames = [], options = {}) {
  if (!container || !Array.isArray(iconNames)) return null;
  const { render = 'mask', ...rest } = options;
  container.innerHTML = iconNames
    .map((name) => `<span class="kcui-icon-cell">${render === 'img' ? svgIconImg(name, rest) : svgIconMask(name, rest)}</span>`)
    .join('');
  return container;
}
