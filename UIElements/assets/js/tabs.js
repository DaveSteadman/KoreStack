/**
 * tabs.js — shared suite tab bar implementation.
 *
 * This implementation defaults to the current KoreDocs file-suite shell but
 * supports per-app configuration so other Kore products can reuse the same tab
 * frame without inheriting KoreDocs routes or branding.
 */

import { SUITE_ICONS, resolveIcon } from './icons.js';
import { applyTheme, themeFor } from './theme.js';

const DEFAULT_TYPE_URL = {
  koredoc: '/doc',
  koresheet: '/sheet',
  kodiag: '/diag',
};

const DEFAULT_BRAND_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
  <rect x="3" y="3" width="8" height="8" rx="1.5" fill="currentColor" opacity=".95"/>
  <rect x="13" y="3" width="8" height="8" rx="1.5" fill="currentColor" opacity=".65"/>
  <rect x="3" y="13" width="8" height="8" rx="1.5" fill="currentColor" opacity=".65"/>
  <rect x="13" y="13" width="8" height="8" rx="1.5" fill="currentColor" opacity=".35"/>
</svg>`;

const DEFAULT_HOME_ICON = `<svg viewBox="0 0 20 20" fill="none" width="13" height="13" aria-hidden="true">
  <path d="M2 5a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5z"
        stroke="currentColor" stroke-width="1.5"/>
</svg>`;

const DEFAULT_CONFIG = {
  storeKey: 'koredocs:tabs',
  serviceKey: 'koredocs',
  typeUrl: DEFAULT_TYPE_URL,
  brandLabel: 'KoreDocs',
  brandIcon: DEFAULT_BRAND_ICON,
  homePath: '/kf',
  homeLabel: 'Files',
  homeTitle: 'KoreFile Explorer',
  homeIcon: DEFAULT_HOME_ICON,
  currentParams: {
    id: 'id',
    file: 'file',
    untitled: 'new',
  },
  currentUntitledPrefix: '__new_',
  closeEventName: 'kcui:before-navigate',
  iconSize: 12,
  icons: SUITE_ICONS,
  titleNormalizer(name) {
    if (name && name.startsWith('__new_')) return 'Untitled';
    return name.replace(/\.(koredoc|koresheet|kodiag)$/, '');
  },
};

let _currentType = null;
let _config = DEFAULT_CONFIG;

function _mergeConfig(config = {}) {
  return {
    ...DEFAULT_CONFIG,
    ...config,
    currentParams: {
      ...DEFAULT_CONFIG.currentParams,
      ...(config.currentParams || {}),
    },
    typeUrl: {
      ...DEFAULT_CONFIG.typeUrl,
      ...(config.typeUrl || {}),
    },
    icons: {
      ...DEFAULT_CONFIG.icons,
      ...(config.icons || {}),
    },
  };
}

function _storageKey() {
  return _config.storeKey;
}

function _icon(type, size = _config.iconSize) {
  return resolveIcon(_config.icons, type, size);
}

function _loadTabs() {
  try { return JSON.parse(localStorage.getItem(_storageKey()) || '[]'); }
  catch { return []; }
}

function _saveTabs(tabs) {
  localStorage.setItem(_storageKey(), JSON.stringify(tabs));
}

function _tabKey(tab) {
  return tab.id != null ? `kf:${tab.id}` : tab.name;
}

function _currentId() {
  const params = new URLSearchParams(location.search);
  const id = params.get(_config.currentParams.id);
  if (id) return `kf:${id}`;
  const file = params.get(_config.currentParams.file);
  if (file) return file;
  const newParam = params.get(_config.currentParams.untitled);
  return newParam ? _config.currentUntitledPrefix + newParam : null;
}

function _tabUrl(tab) {
  const baseUrl = _config.typeUrl[tab.type];
  if (!baseUrl) return _config.homePath;
  if (tab.untitled) {
    return baseUrl + '?' + _config.currentParams.untitled + '=' + encodeURIComponent(tab.name.slice(_config.currentUntitledPrefix.length));
  }
  if (tab.id != null) {
    return baseUrl + '?' + _config.currentParams.id + '=' + encodeURIComponent(tab.id) + '&' + _config.currentParams.file + '=' + encodeURIComponent(tab.name);
  }
  return baseUrl + '?' + _config.currentParams.file + '=' + encodeURIComponent(tab.name);
}

function _shortName(name) {
  return _config.titleNormalizer(name);
}

function _autoRegisterNew() {
  const params = new URLSearchParams(location.search);
  const newParam = params.get(_config.currentParams.untitled);
  if (!newParam || params.get(_config.currentParams.file)) return;
  const id = _config.currentUntitledPrefix + newParam;
  const tabs = _loadTabs();
  if (!tabs.find(tab => tab.name === id)) {
    tabs.push({ name: id, type: _currentType, untitled: true });
    _saveTabs(tabs);
  }
}

export function init(currentType, config = {}) {
  _currentType = currentType;
  _config = _mergeConfig(config);
  const themeKey = _config.serviceKey || currentType;
  applyTheme(document.documentElement, themeKey);
  _autoRegisterNew();
  _render();
  window.addEventListener('storage', event => {
    if (event.key === _storageKey()) _render();
  });
}

export function track(name, type, id = null) {
  if (!name) return;
  const tabs = _loadTabs();
  const currentId = _currentId();
  const key = id != null ? `kf:${id}` : name;
  const untitledIdx = currentId
    ? tabs.findIndex(tab => _tabKey(tab) === currentId && tab.untitled)
    : -1;
  if (untitledIdx !== -1) {
    if (!tabs.find(tab => _tabKey(tab) === key)) {
      tabs[untitledIdx] = { name, type: type || _currentType, id };
    } else {
      tabs.splice(untitledIdx, 1);
    }
  } else {
    const existing = tabs.find(tab => _tabKey(tab) === key);
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

function _navigate(url) {
  document.dispatchEvent(new CustomEvent(_config.closeEventName, { detail: url }));
  location.href = url;
}

function _closeTab(name) {
  const current = _currentId();
  const remaining = _loadTabs().filter(tab => _tabKey(tab) !== name);
  _saveTabs(remaining);
  if (name === current) {
    if (remaining.length) {
      const next = remaining[remaining.length - 1];
      _navigate(_tabUrl(next));
    } else {
      _navigate(_config.homePath);
    }
  } else {
    _render();
  }
}

function _render() {
  const bar = document.getElementById('tab-bar');
  if (!bar) return;

  const theme = themeFor(_config.serviceKey || _currentType);
  if (theme) {
    bar.style.setProperty('--tabs-accent', theme.accent);
    bar.style.setProperty('--tabs-accent-2', theme.accent2);
  }

  const tabs = _loadTabs();
  const currentId = _currentId();
  const brandHtml = `
    <div id="kd-brand" style="order:1">
      ${_config.brandIcon}
      <span>${_config.brandLabel}</span>
    </div>`;

  const kfActive = location.pathname === _config.homePath ? ' kd-kf-active' : '';
  const kfHtml = `
    <a id="kd-kf-link" class="kd-kf-link${kfActive}" href="${_config.homePath}" title="${_config.homeTitle}"
       style="order:2;flex-shrink:0">
      ${_config.homeIcon}
      <span>${_config.homeLabel}</span>
    </a>`;

  let tabsHtml = '<div class="kd-tabs" role="tablist" style="order:3;flex:1">';
  tabs.forEach(tab => {
    const active = _tabKey(tab) === currentId ? ' active' : '';
    const key = _tabKey(tab);
    const label = tab.untitled ? 'Untitled' : _shortName(tab.name);
    const nameClass = tab.untitled ? 'kd-tab-name kd-untitled-label' : 'kd-tab-name';
    const titleAttr = tab.untitled ? 'Untitled - unsaved' : tab.name;
    const closeTitle = tab.untitled ? 'Discard' : 'Close ' + tab.name;
    tabsHtml += `
      <div class="kd-tab${active}" data-tab="${key}" data-type="${tab.type}" data-name="${tab.name}" data-id="${tab.id ?? ''}"
           data-untitled="${tab.untitled ? '1' : ''}" role="tab" title="${titleAttr}">
        <span class="kd-tab-icon">${_icon(tab.type)}</span>
        <span class="${nameClass}">${label}</span>
        <button class="kd-tab-close" data-close="${key}" title="${closeTitle}" aria-label="Close">×</button>
      </div>`;
  });
  tabsHtml += '</div>';

  const addHtml = `
    <button class="kd-add" title="${_config.homeTitle}" aria-label="${_config.homeTitle}" style="order:4">
      <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true">
        <line x1="6" y1="1" x2="6" y2="11" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
        <line x1="1" y1="6" x2="11" y2="6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
      </svg>
    </button>`;

  bar.innerHTML = brandHtml + kfHtml + tabsHtml + addHtml;

  bar.querySelectorAll('.kd-tab').forEach(el => {
    el.addEventListener('mousedown', event => {
      if (event.target.closest('.kd-tab-close')) return;
      event.preventDefault();
      const key = el.dataset.tab;
      if (_currentId() === key) return;
      _navigate(_tabUrl({
        name: el.dataset.name,
        id: el.dataset.id ? parseInt(el.dataset.id, 10) : null,
        type: el.dataset.type,
        untitled: el.dataset.untitled === '1',
      }));
    });
  });

  bar.querySelectorAll('.kd-tab-close').forEach(btn => {
    btn.addEventListener('mousedown', event => {
      event.stopPropagation();
      _closeTab(btn.dataset.close);
    });
  });

  bar.querySelector('.kd-add').addEventListener('click', () => _navigate(_config.homePath));
}

export function configureTabs(config = {}) {
  _config = _mergeConfig(config);
  return _config;
}
