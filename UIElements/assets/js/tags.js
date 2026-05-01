/**
 * tags.js — UITag component helpers.
 *
 * createTag(options)   → HTMLElement
 * updateTag(el, patch) → void
 *
 * These helpers are optional — tags can also be written as plain HTML using
 * the .kcui-tag CSS classes.
 */

const COLOR_CLASSES = /** @type {const} */ ([
  'kcui-tag--accent',
  'kcui-tag--success',
  'kcui-tag--warning',
  'kcui-tag--danger',
  'kcui-tag--info',
  'kcui-tag--dim',
]);

/**
 * Create a UITag element.
 *
 * @param {object}   [opts]
 * @param {string}   [opts.text='']        Label text
 * @param {string}   [opts.color='']       'accent' | 'success' | 'warning' | 'danger' | 'info' | 'dim'
 * @param {boolean}  [opts.filled=false]   Show background tint (static fill)
 * @param {boolean}  [opts.pill=false]     Rounded pill shape
 * @param {boolean}  [opts.interactive=false] Render as <button>
 * @param {boolean}  [opts.active]         Initial is-on / is-off state (interactive only)
 * @param {Function} [opts.onClick]        Click handler — implies interactive
 * @param {string}   [opts.id]             Element id
 * @returns {HTMLElement}
 */
export function createTag({
  text        = '',
  color       = '',
  filled      = false,
  pill        = false,
  interactive = false,
  active,
  onClick     = null,
  id          = null,
} = {}) {
  const isButton = interactive || onClick != null;
  const el = document.createElement(isButton ? 'button' : 'span');

  el.className = 'kcui-tag';

  if (color)  el.classList.add(`kcui-tag--${color}`);
  if (filled) el.classList.add('kcui-tag--filled');
  if (pill)   el.classList.add('kcui-tag--pill');
  if (id)     el.id = id;

  if (isButton) {
    el.type = 'button';
    if (active !== undefined) {
      el.classList.toggle('is-on',  !!active);
      el.classList.toggle('is-off', !active);
    }
  }

  el.textContent = text;

  if (onClick) {
    el.addEventListener('click', onClick);
  }

  return el;
}

/**
 * Update an existing UITag element in-place.
 *
 * @param {HTMLElement} el
 * @param {object}      patch
 * @param {string}      [patch.text]   New label text
 * @param {string}      [patch.color]  New color variant (replaces previous)
 * @param {boolean}     [patch.active] Toggle is-on / is-off state
 */
export function updateTag(el, { text, color, active } = {}) {
  if (text !== undefined) {
    el.textContent = text;
  }

  if (color !== undefined) {
    for (const cls of COLOR_CLASSES) {
      el.classList.remove(cls);
    }
    if (color) el.classList.add(`kcui-tag--${color}`);
  }

  if (active !== undefined) {
    el.classList.toggle('is-on',  !!active);
    el.classList.toggle('is-off', !active);
  }
}
