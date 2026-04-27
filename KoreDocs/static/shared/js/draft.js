/**
 * draft.js — Per-tab autosave for unsaved content.
 *
 * Every SPA imports this module to persist in-progress work to localStorage
 * so that tab switches (full-page navigations) don't discard unsaved content.
 *
 * Draft key is derived from the current URL:
 *   ?id=12             → 'koredocs:draft:kf:12'
 *   ?file=foo.koredoc  → 'koredocs:draft:foo.koredoc'
 *   ?new=1714000000000 → 'koredocs:draft:__new_1714000000000'
 *   (bare URL)         → null — no identity, nothing stored
 */

const PREFIX = 'koredocs:draft:';

function _key() {
  const p = new URLSearchParams(location.search);
  const id = p.get('id');
  if (id) return PREFIX + 'kf:' + id;
  const file = p.get('file');
  if (file) return PREFIX + file;
  const n = p.get('new');
  return n ? PREFIX + '__new_' + n : null;
}

/** Immediately persist content for the current tab. */
export function save(content) {
  const k = _key();
  if (k) localStorage.setItem(k, content);
}

/** Load any saved draft for the current tab. Returns null if none exists. */
export function load() {
  const k = _key();
  return k ? localStorage.getItem(k) : null;
}

/** Remove the draft for the current tab. Call after a successful file save. */
export function clear() {
  const k = _key();
  if (k) localStorage.removeItem(k);
}

/**
 * Returns a debounced wrapper around save() to avoid thrashing localStorage
 * on every keystroke or change event.
 * @param {number} [ms=600]  Debounce delay in milliseconds.
 */
export function makeSaver(ms = 600) {
  let timer = null;
  return content => {
    clearTimeout(timer);
    timer = setTimeout(() => save(content), ms);
  };
}
