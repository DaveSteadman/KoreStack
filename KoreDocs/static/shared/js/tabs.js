/**
 * tabs.js — Compatibility wrapper target for the shared top bar.
 *
 * New code should import '/static/shared/js/topbar.js'.
 * This file retains the existing implementation so older references keep working.
 */

const STORE_KEY = 'koredocs:tabs';

const TYPE_URL = {
  koredoc:   '/doc',
  koresheet: '/sheet',
  kodiag:    '/diag',
};

// ── SVG icons ───────────────────────────────────────────────────────────────

function _icon(type, size = 12) {
  const s = `width="${size}" height="${size}"`;
  if (type === 'koredoc')
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="3" y="2" width="14" height="16" rx="2" stroke="currentColor" stroke-width="1.8"/>
      <line x1="6" y1="7"  x2="14" y2="7"  stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
      <line x1="6" y1="10" x2="14" y2="10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
      <line x1="6" y1="13" x2="11" y2="13" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
    </svg>`;
  if (type === 'koresheet')
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="2" y="2" width="16" height="16" rx="2" stroke="currentColor" stroke-width="1.8"/>
      <line x1="2"  y1="8"  x2="18" y2="8"  stroke="currentColor" stroke-width="1.2"/>
      <line x1="2"  y1="13" x2="18" y2="13" stroke="currentColor" stroke-width="1.2"/>
      <line x1="8"  y1="2"  x2="8"  y2="18" stroke="currentColor" stroke-width="1.2"/>
      <line x1="13" y1="2"  x2="13" y2="18" stroke="currentColor" stroke-width="1.2"/>
    </svg>`;
  if (type === 'kodiag')
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <circle cx="4"  cy="10" r="2.5" stroke="currentColor" stroke-width="1.5"/>
      <circle cx="16" cy="4"  r="2.5" stroke="currentColor" stroke-width="1.5"/>
      <circle cx="16" cy="16" r="2.5" stroke="currentColor" stroke-width="1.5"/>
      <line x1="6.2"  y1="9"    x2="13.8" y2="5"    stroke="currentColor" stroke-width="1.3"/>
      <line x1="6.2"  y1="11"   x2="13.8" y2="15"   stroke="currentColor" stroke-width="1.3"/>
    </svg>`;
  return '';
}

// ── State helpers ───────────────────────────────────────────────────────────

let _currentType = null;

function _loadTabs() {
  try { return JSON.parse(localStorage.getItem(STORE_KEY) || '[]'); }
  catch { return []; }
}

function _saveTabs(tabs) {
  localStorage.setItem(STORE_KEY, JSON.stringify(tabs));
}

function _tabKey(tab) {
  return tab.id != null ? `kf:${tab.id}` : tab.name;
}

/**
 * Unique identifier for the current page — the ?file= value for named files,
 * or '__new_<ts>' for untitled pages opened via ?new=<ts>.
 */
function _currentId() {
  const p = new URLSearchParams(location.search);
  const id = p.get('id');
  if (id) return `kf:${id}`;
  const file = p.get('file');
  if (file) return file;
  const newParam = p.get('new');
  return newParam ? '__new_' + newParam : null;
}

/** Build a navigation URL for a tab entry. */
function _tabUrl(tab) {
  if (tab.untitled) {
    return TYPE_URL[tab.type] + '?new=' + tab.name.slice('__new_'.length);
  }
  if (tab.id != null) {
    return TYPE_URL[tab.type] + '?id=' + encodeURIComponent(tab.id) + '&file=' + encodeURIComponent(tab.name);
  }
  return TYPE_URL[tab.type] + '?file=' + encodeURIComponent(tab.name);
}

function _shortName(name) {
  if (name && name.startsWith('__new_')) return 'Untitled';
  return name.replace(/\.(koredoc|koresheet|kodiag)$/, '');
}

/**
 * If the current page was opened as a new (untitled) doc via ?new=<ts>,
 * register it in localStorage so it appears as a real tab.
 */
function _autoRegisterNew() {
  const p = new URLSearchParams(location.search);
  const newParam = p.get('new');
  if (!newParam || p.get('file')) return;
  const id   = '__new_' + newParam;
  const tabs = _loadTabs();
  if (!tabs.find(t => t.name === id)) {
    tabs.push({ name: id, type: _currentType, untitled: true });
    _saveTabs(tabs);
  }
}

// ── Public API ──────────────────────────────────────────────────────────────

/**
 * Initialise the tab bar. Call once per SPA on page load.
 * @param {'koredoc'|'koresheet'|'kodiag'} currentType
 */
export function init(currentType) {
  _currentType = currentType;
  _autoRegisterNew(); // persist untitled tab before first render
  _render();
  // Stay in sync when another browser tab changes localStorage
  window.addEventListener('storage', e => {
    if (e.key === STORE_KEY) _render();
  });
}

/**
 * Register a named file as an open tab.
 * Safe to call multiple times — won't duplicate.
 * @param {string} name   e.g. 'notes.koredoc'
 * @param {string} [type] defaults to currentType
 */
export function track(name, type, id = null) {
  if (!name) return;
  const tabs      = _loadTabs();
  const currentId = _currentId();
  const key       = id != null ? `kf:${id}` : name;
  // If the current page is an untitled placeholder, replace it with the real name
  const untitledIdx = currentId
    ? tabs.findIndex(t => _tabKey(t) === currentId && t.untitled)
    : -1;
  if (untitledIdx !== -1) {
    if (!tabs.find(t => _tabKey(t) === key)) {
      tabs[untitledIdx] = { name, type: type || _currentType, id };
    } else {
      tabs.splice(untitledIdx, 1); // real name already tracked; drop duplicate
    }
  } else {
    const existing = tabs.find(t => _tabKey(t) === key);
    if (existing) {
      existing.name = name;
      existing.type = type || _currentType;
      if (id != null) existing.id = id;
    } else {
      tabs.push({ name, type: type || _currentType, id });
    }
  }
  _saveTabs(tabs);
  _render();
}

// ── Internal ────────────────────────────────────────────────────────────────

// ── Navigation helper ───────────────────────────────────────────────────────

/**
 * Navigate to a URL, first dispatching 'kd:before-navigate' synchronously so
 * every SPA can flush unsaved state to localStorage before the page unloads.
 * Custom events are always dispatched synchronously, so listeners run to
 * completion before location.href is assigned.
 */
function _navigate(url) {
  document.dispatchEvent(new CustomEvent('kd:before-navigate', { detail: url }));
  location.href = url;
}

function _closeTab(name) {
  const current   = _currentId();
  const remaining = _loadTabs().filter(t => _tabKey(t) !== name);
  _saveTabs(remaining);
  if (name === current) {
    // Navigate away — to the last remaining tab, or bare editor
    if (remaining.length) {
      const next = remaining[remaining.length - 1];
      _navigate(_tabUrl(next));
    } else {
      _navigate('/kf');
    }
  } else {
    _render();
  }
}

function _render() {
  const bar = document.getElementById('tab-bar');
  if (!bar) return;

  const tabs      = _loadTabs();
  const currentId = _currentId();

  // ── Brand markup — static logo, no dropdown ─────────────────────────────
  const brandHtml = `
    <div id="kd-brand" style="order:1">
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <rect x="3"  y="3"  width="8" height="8" rx="1.5" fill="currentColor" opacity=".95"/>
        <rect x="13" y="3"  width="8" height="8" rx="1.5" fill="currentColor" opacity=".65"/>
        <rect x="3"  y="13" width="8" height="8" rx="1.5" fill="currentColor" opacity=".65"/>
        <rect x="13" y="13" width="8" height="8" rx="1.5" fill="currentColor" opacity=".35"/>
      </svg>
      <span>KoreDocs</span>
    </div>`;

  // ── KoreFile explorer link ───────────────────────────────────────────────
  const kfActive = location.pathname === '/kf' ? ' kd-kf-active' : '';
  const kfHtml = `
    <a id="kd-kf-link" class="kd-kf-link${kfActive}" href="/kf" title="KoreFile Explorer"
       style="order:2;flex-shrink:0">
      <svg viewBox="0 0 20 20" fill="none" width="13" height="13" aria-hidden="true">
        <path d="M2 5a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5z"
              stroke="currentColor" stroke-width="1.5"/>
      </svg>
      <span>Files</span>
    </a>`;

  // ── Tabs markup ──────────────────────────────────────────────────────────
  let tabsHtml = '<div class="kd-tabs" role="tablist" style="order:3;flex:1">';

  tabs.forEach(tab => {
    const active     = _tabKey(tab) === currentId ? ' active' : '';
    const key        = _tabKey(tab);
    const label      = tab.untitled ? 'Untitled' : _shortName(tab.name);
    const nameClass  = tab.untitled ? 'kd-tab-name kd-untitled-label' : 'kd-tab-name';
    const titleAttr  = tab.untitled ? 'Untitled — unsaved' : tab.name;
    const closeTitle = tab.untitled ? 'Discard' : 'Close ' + tab.name;
    tabsHtml += `
      <div class="kd-tab${active}" data-tab="${key}" data-type="${tab.type}" data-name="${tab.name}" data-id="${tab.id ?? ''}"
           data-untitled="${tab.untitled ? '1' : ''}" role="tab" title="${titleAttr}">
        <span class="kd-tab-icon">${_icon(tab.type)}</span>
        <span class="${nameClass}">${label}</span>
        <button class="kd-tab-close" data-close="${key}"
                title="${closeTitle}" aria-label="Close">×</button>
      </div>`;
  });

  tabsHtml += '</div>';

  // ── Add button ───────────────────────────────────────────────────────────
  const addHtml = `
    <button class="kd-add" title="Open KoreFiles" aria-label="Open KoreFiles" style="order:4">
      <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true">
        <line x1="6" y1="1" x2="6" y2="11" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
        <line x1="1" y1="6" x2="11" y2="6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
      </svg>
    </button>`;

  bar.innerHTML = brandHtml + kfHtml + tabsHtml + addHtml;

  // ── Wire: tab clicks ─────────────────────────────────────────────────────
  bar.querySelectorAll('.kd-tab').forEach(el => {
    el.addEventListener('mousedown', e => {
      if (e.target.closest('.kd-tab-close')) return;
      e.preventDefault();
      const key = el.dataset.tab;
      if (_currentId() === key) return; // already here
      _navigate(_tabUrl({
        name: el.dataset.name,
        id: el.dataset.id ? parseInt(el.dataset.id, 10) : null,
        type: el.dataset.type,
        untitled: el.dataset.untitled === '1',
      }));
    });
  });

  bar.querySelectorAll('.kd-tab-close').forEach(btn => {
    btn.addEventListener('mousedown', e => {
      e.stopPropagation();
      _closeTab(btn.dataset.close);
    });
  });

  // ── Wire: + add button ───────────────────────────────────────────────────
  bar.querySelector('.kd-add').addEventListener('click', () => _navigate('/kf'));
}
