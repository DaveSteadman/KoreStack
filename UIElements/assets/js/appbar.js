import { SUITE_ICONS, resolveIcon } from './icons.js?v=20260501a';
import { applyTheme, themeFor } from './theme.js?v=20260501a';

const DEFAULT_TYPE_URL = {
  koredoc: '/doc',
  koresheet: '/sheet',
  kodiag: '/diag',
};

const DEFAULT_APPBAR_TAB_BRAND_ICON = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
  <rect x="3" y="3" width="8" height="8" rx="1.5" fill="currentColor" opacity=".95"/>
  <rect x="13" y="3" width="8" height="8" rx="1.5" fill="currentColor" opacity=".65"/>
  <rect x="3" y="13" width="8" height="8" rx="1.5" fill="currentColor" opacity=".65"/>
  <rect x="13" y="13" width="8" height="8" rx="1.5" fill="currentColor" opacity=".35"/>
</svg>`;

const DEFAULT_APPBAR_HOME_ICON = `<svg viewBox="0 0 20 20" fill="none" width="13" height="13" aria-hidden="true">
  <path d="M2 5a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5z"
        stroke="currentColor" stroke-width="1.5"/>
</svg>`;

const DEFAULT_APPBAR_TABS_CONFIG = {
  storeKey: 'koredocs:tabs',
  serviceKey: 'koredocs',
  typeUrl: DEFAULT_TYPE_URL,
  brandLabel: 'KoreDocs',
  brandIcon: 'koredocs',
  homePath: '/kf',
  homeLabel: 'Files',
  homeTitle: 'KoreFile Explorer',
  homeIcon: DEFAULT_APPBAR_HOME_ICON,
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

let currentAppTabsType = null;
let currentAppTabsConfig = DEFAULT_APPBAR_TABS_CONFIG;

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function iconMarkup(icon, iconSize, icons) {
  if (typeof icon === 'function') return icon(iconSize);
  if (typeof icon === 'string') return resolveIcon(icons, icon, iconSize);
  return '';
}

function chipMarkup(chip) {
  const toneClass = chip.tone ? ` is-${chip.tone}` : '';
  const chipId = chip.id ? ` id="${escapeHtml(chip.id)}"` : '';
  const label = chip.label ? `<span class="kappbar-chip-label kcui-text-caption">${escapeHtml(chip.label)}</span>` : '';
  const value = chip.valueId
    ? `<strong class="kappbar-chip-value kcui-text-topbar" id="${escapeHtml(chip.valueId)}">${escapeHtml(chip.value ?? '')}</strong>`
    : `<strong class="kappbar-chip-value kcui-text-topbar">${escapeHtml(chip.value ?? '')}</strong>`;
  return `<span class="kappbar-chip${toneClass}"${chipId}>${label}${value}</span>`;
}

function tabMarkup(tab, icons, iconSize) {
  const activeClass = tab.active ? ' is-active' : '';
  const target = tab.target ? ` target="${escapeHtml(tab.target)}"` : '';
  const rel = tab.rel ? ` rel="${escapeHtml(tab.rel)}"` : '';
  const icon = tab.icon ? `<span class="kappbar-tabicon" aria-hidden="true">${iconMarkup(tab.icon, iconSize, icons)}</span>` : '';
  return `<a class="kappbar-tab${activeClass}" href="${escapeHtml(tab.href)}"${target}${rel}>${icon}<span class="kcui-text-topbar">${escapeHtml(tab.label)}</span></a>`;
}

function noteMarkup(note) {
  if (!note) return '';
  const noteId = note.id ? ` id="${escapeHtml(note.id)}"` : '';
  const noteTitle = note.title ? ` title="${escapeHtml(note.title)}"` : '';
  const noteClass = note.className ? ` ${escapeHtml(note.className)}` : '';
  return `<span class="kappbar-note kcui-text-topbar${noteClass}"${noteId}${noteTitle}>${escapeHtml(note.text ?? '')}</span>`;
}

export function initAppBar(options = {}) {
  const {
    mountId = 'app-bar',
    currentService = null,
    accent = null,
    overline = null,
    brandLabel,
    brandIcon = currentService,
    iconSize = 16,
    icons = SUITE_ICONS,
    statusDot = null,
    chips = [],
    tabs = [],
    note = null,
    actionsHtml = '',
    editorTabsSlot = null,
  } = options;

  const host = document.getElementById(mountId);
  if (!host) return null;

  host.classList.add('kappbar-host');

  const theme = themeFor(currentService);
  const accentValue = accent || theme?.accent || 'var(--accent)';
  if (currentService) {
    applyTheme(document.documentElement, currentService);
  }
  host.style.setProperty('--kappbar-accent', accentValue);

  const brand = brandLabel
    ? `
      <div class="kappbar-group kappbar-brand">
        <span class="kappbar-brandmark" aria-hidden="true">${iconMarkup(brandIcon, 18, icons)}</span>
        <span class="kappbar-brandtext">
          ${overline ? `<span class="kappbar-overline kcui-text-caption">${escapeHtml(overline)}</span>` : ''}
          <span class="kappbar-title kcui-text-topbar-title">${escapeHtml(brandLabel)}</span>
        </span>
      </div>`
    : '';

  const metaItems = [];
  if (statusDot) {
    const dotId = statusDot.id ? ` id="${escapeHtml(statusDot.id)}"` : '';
    const dotClass = statusDot.className ? ` ${escapeHtml(statusDot.className)}` : '';
    const dotTitle = statusDot.title ? ` title="${escapeHtml(statusDot.title)}"` : '';
    metaItems.push(`<span class="kappbar-presence${dotClass}"${dotId}${dotTitle}></span>`);
  }
  metaItems.push(...chips.map(chipMarkup));

  const meta = metaItems.length
    ? `<div class="kappbar-group kappbar-meta">${metaItems.join('')}</div>`
    : '';

  const tabsHtml = tabs.length
    ? `<div class="kappbar-group kappbar-tabs" role="navigation" aria-label="Application navigation">${tabs.map((tab) => tabMarkup(tab, icons, 13)).join('')}</div>`
    : editorTabsSlot
      ? `<div class="kappbar-group kappbar-editortabs" id="${escapeHtml(editorTabsSlot)}"></div>`
      : '';

  const noteHtml = noteMarkup(note);

  const actionsContent = `${noteHtml}${actionsHtml}`;

  const actionsGroup = (noteHtml || actionsHtml)
    ? `<div class="kappbar-group kappbar-actions">${actionsContent}</div>`
    : '';

  host.innerHTML = `
    <div class="kappbar" data-service="${escapeHtml(currentService ?? '')}">
      ${brand}
      ${meta}
      ${tabsHtml}
      <div class="kappbar-spacer"></div>
      ${actionsGroup}
    </div>`;

  const root = host.firstElementChild;
  if (root) {
    root.style.setProperty('--kappbar-accent', accentValue);
  }
  return host;
}

function mergeAppTabsConfig(config = {}) {
  return {
    ...DEFAULT_APPBAR_TABS_CONFIG,
    ...config,
    currentParams: {
      ...DEFAULT_APPBAR_TABS_CONFIG.currentParams,
      ...(config.currentParams || {}),
    },
    typeUrl: {
      ...DEFAULT_APPBAR_TABS_CONFIG.typeUrl,
      ...(config.typeUrl || {}),
    },
    icons: {
      ...DEFAULT_APPBAR_TABS_CONFIG.icons,
      ...(config.icons || {}),
    },
  };
}

function appTabsStorageKey() {
  return currentAppTabsConfig.storeKey;
}

function appTabsIcon(type, size = currentAppTabsConfig.iconSize) {
  return resolveIcon(currentAppTabsConfig.icons, type, size);
}

function loadAppTabs() {
  try {
    return JSON.parse(localStorage.getItem(appTabsStorageKey()) || '[]');
  } catch {
    return [];
  }
}

function saveAppTabs(tabs) {
  localStorage.setItem(appTabsStorageKey(), JSON.stringify(tabs));
}

function appTabKey(tab) {
  return tab.id != null ? `kf:${tab.id}` : tab.name;
}

function currentAppTabId() {
  const params = new URLSearchParams(location.search);
  const id = params.get(currentAppTabsConfig.currentParams.id);
  if (id) return `kf:${id}`;
  const file = params.get(currentAppTabsConfig.currentParams.file);
  if (file) return file;
  const untitled = params.get(currentAppTabsConfig.currentParams.untitled);
  return untitled ? currentAppTabsConfig.currentUntitledPrefix + untitled : null;
}

function appTabUrl(tab) {
  const baseUrl = currentAppTabsConfig.typeUrl[tab.type];
  if (!baseUrl) return currentAppTabsConfig.homePath;
  if (tab.untitled) {
    return `${baseUrl}?${currentAppTabsConfig.currentParams.untitled}=${encodeURIComponent(tab.name.slice(currentAppTabsConfig.currentUntitledPrefix.length))}`;
  }
  if (tab.id != null) {
    return `${baseUrl}?${currentAppTabsConfig.currentParams.id}=${encodeURIComponent(tab.id)}&${currentAppTabsConfig.currentParams.file}=${encodeURIComponent(tab.name)}`;
  }
  return `${baseUrl}?${currentAppTabsConfig.currentParams.file}=${encodeURIComponent(tab.name)}`;
}

function shortAppTabName(name) {
  return currentAppTabsConfig.titleNormalizer(name);
}

function autoRegisterUntitledAppTab() {
  const params = new URLSearchParams(location.search);
  const untitled = params.get(currentAppTabsConfig.currentParams.untitled);
  if (!untitled || params.get(currentAppTabsConfig.currentParams.file)) return;
  const name = currentAppTabsConfig.currentUntitledPrefix + untitled;
  const tabs = loadAppTabs();
  if (!tabs.find((tab) => tab.name === name)) {
    tabs.push({ name, type: currentAppTabsType, untitled: true });
    saveAppTabs(tabs);
  }
}

function navigateAppTab(url) {
  document.dispatchEvent(new CustomEvent(currentAppTabsConfig.closeEventName, { detail: url }));
  location.href = url;
}

function closeAppTab(key) {
  const current = currentAppTabId();
  const remaining = loadAppTabs().filter((tab) => appTabKey(tab) !== key);
  saveAppTabs(remaining);
  if (key === current) {
    if (remaining.length) {
      navigateAppTab(appTabUrl(remaining[remaining.length - 1]));
    } else {
      navigateAppTab(currentAppTabsConfig.homePath);
    }
    return;
  }
  renderAppTabs();
}

function renderAppTabs() {
  const host = document.getElementById('tab-bar');
  if (!host) return;

  const theme = themeFor(currentAppTabsConfig.serviceKey || currentAppTabsType);
  if (theme) {
    host.style.setProperty('--tabs-accent', theme.accent);
    host.style.setProperty('--tabs-accent-2', theme.accent2);
  }

  const tabs = loadAppTabs();
  const current = currentAppTabId();
  const brandHtml = `
    <div id="kd-brand" style="order:1">
      ${iconMarkup(currentAppTabsConfig.brandIcon, 15, currentAppTabsConfig.icons)}
      <span>${escapeHtml(currentAppTabsConfig.brandLabel)}</span>
    </div>`;

  const isHome = location.pathname === currentAppTabsConfig.homePath ? ' kd-kf-active' : '';
  const homeHtml = `
    <a id="kd-kf-link" class="kd-kf-link${isHome}" href="${escapeHtml(currentAppTabsConfig.homePath)}" title="${escapeHtml(currentAppTabsConfig.homeTitle)}" style="order:2;flex-shrink:0">
      ${currentAppTabsConfig.homeIcon}
      <span>${escapeHtml(currentAppTabsConfig.homeLabel)}</span>
    </a>`;

  let tabsHtml = '<div class="kd-tabs" role="tablist" style="order:3;flex:1">';
  for (const tab of tabs) {
    const active = appTabKey(tab) === current ? ' active' : '';
    const key = appTabKey(tab);
    const label = tab.untitled ? 'Untitled' : shortAppTabName(tab.name);
    const nameClass = tab.untitled ? 'kd-tab-name kd-untitled-label' : 'kd-tab-name';
    const title = tab.untitled ? 'Untitled - unsaved' : tab.name;
    const closeTitle = tab.untitled ? 'Discard' : `Close ${tab.name}`;
    tabsHtml += `
      <div class="kd-tab${active}" data-tab="${escapeHtml(key)}" data-type="${escapeHtml(tab.type)}" data-name="${escapeHtml(tab.name)}" data-id="${tab.id ?? ''}" data-untitled="${tab.untitled ? '1' : ''}" role="tab" title="${escapeHtml(title)}">
        <span class="kd-tab-icon">${appTabsIcon(tab.type)}</span>
        <span class="${nameClass}">${escapeHtml(label)}</span>
        <button class="kd-tab-close" data-close="${escapeHtml(key)}" title="${escapeHtml(closeTitle)}" aria-label="Close">×</button>
      </div>`;
  }
  tabsHtml += '</div>';

  const addHtml = `
    <button class="kd-add" title="${escapeHtml(currentAppTabsConfig.homeTitle)}" aria-label="${escapeHtml(currentAppTabsConfig.homeTitle)}" style="order:4">
      <svg width="11" height="11" viewBox="0 0 12 12" fill="none" aria-hidden="true">
        <line x1="6" y1="1" x2="6" y2="11" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
        <line x1="1" y1="6" x2="11" y2="6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
      </svg>
    </button>`;

  host.innerHTML = brandHtml + homeHtml + tabsHtml + addHtml;

  for (const tab of host.querySelectorAll('.kd-tab')) {
    tab.addEventListener('mousedown', (event) => {
      if (event.target.closest('.kd-tab-close')) return;
      event.preventDefault();
      const key = tab.dataset.tab;
      if (currentAppTabId() === key) return;
      navigateAppTab(appTabUrl({
        name: tab.dataset.name,
        id: tab.dataset.id ? parseInt(tab.dataset.id, 10) : null,
        type: tab.dataset.type,
        untitled: tab.dataset.untitled === '1',
      }));
    });
  }

  for (const button of host.querySelectorAll('.kd-tab-close')) {
    button.addEventListener('mousedown', (event) => {
      event.stopPropagation();
      closeAppTab(button.dataset.close);
    });
  }

  host.querySelector('.kd-add')?.addEventListener('click', () => navigateAppTab(currentAppTabsConfig.homePath));
}

export function initAppTabs(currentType, config = {}) {
  currentAppTabsType = currentType;
  currentAppTabsConfig = mergeAppTabsConfig(config);
  applyTheme(document.documentElement, currentAppTabsConfig.serviceKey || currentType);
  autoRegisterUntitledAppTab();
  renderAppTabs();
  window.addEventListener('storage', (event) => {
    if (event.key === appTabsStorageKey()) renderAppTabs();
  });
}

export function trackAppTab(name, type, id = null) {
  if (!name) return;
  const tabs = loadAppTabs();
  const current = currentAppTabId();
  const key = id != null ? `kf:${id}` : name;
  const untitledIndex = current ? tabs.findIndex((tab) => appTabKey(tab) === current && tab.untitled) : -1;

  if (untitledIndex !== -1) {
    if (!tabs.find((tab) => appTabKey(tab) === key)) {
      tabs[untitledIndex] = { name, type: type || currentAppTabsType, id };
    } else {
      tabs.splice(untitledIndex, 1);
    }
  } else {
    const existing = tabs.find((tab) => appTabKey(tab) === key);
    if (existing) {
      existing.name = name;
      existing.type = type || currentAppTabsType;
      if (id != null) existing.id = id;
    } else {
      tabs.push({ name, type: type || currentAppTabsType, id });
    }
  }

  saveAppTabs(tabs);
  renderAppTabs();
}

export function configureAppTabs(config = {}) {
  currentAppTabsConfig = mergeAppTabsConfig(config);
  return currentAppTabsConfig;
}

export { initAppTabs as init, trackAppTab as track, configureAppTabs as configureTabs };