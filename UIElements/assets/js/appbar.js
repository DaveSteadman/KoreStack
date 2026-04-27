import { SUITE_ICONS, resolveIcon } from './icons.js';

const DEFAULT_ACCENTS = {
  korestack: 'var(--info)',
  koreagent: 'var(--info)',
  koreconversation: 'var(--info)',
  koredata: 'var(--success)',
  koredocs: 'var(--accent-2)',
  korecomms: 'var(--warning)',
};

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
  const label = chip.label ? `<span class="kappbar-chip-label">${escapeHtml(chip.label)}</span>` : '';
  const value = chip.valueId
    ? `<strong class="kappbar-chip-value" id="${escapeHtml(chip.valueId)}">${escapeHtml(chip.value ?? '')}</strong>`
    : `<strong class="kappbar-chip-value">${escapeHtml(chip.value ?? '')}</strong>`;
  return `<span class="kappbar-chip${toneClass}"${chipId}>${label}${value}</span>`;
}

function tabMarkup(tab, icons, iconSize) {
  const activeClass = tab.active ? ' is-active' : '';
  const target = tab.target ? ` target="${escapeHtml(tab.target)}"` : '';
  const rel = tab.rel ? ` rel="${escapeHtml(tab.rel)}"` : '';
  const icon = tab.icon ? `<span class="kappbar-tabicon" aria-hidden="true">${iconMarkup(tab.icon, iconSize, icons)}</span>` : '';
  return `<a class="kappbar-tab${activeClass}" href="${escapeHtml(tab.href)}"${target}${rel}>${icon}<span>${escapeHtml(tab.label)}</span></a>`;
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
    actionsHtml = '',
  } = options;

  const host = document.getElementById(mountId);
  if (!host) return null;

  host.classList.add('kappbar-host');

  const accentValue = accent || DEFAULT_ACCENTS[currentService] || 'var(--accent)';
  host.style.setProperty('--kappbar-accent', accentValue);

  const brand = brandLabel
    ? `
      <div class="kappbar-group kappbar-brand">
        <span class="kappbar-brandmark" aria-hidden="true">${iconMarkup(brandIcon, 18, icons)}</span>
        <span class="kappbar-brandtext">
          ${overline ? `<span class="kappbar-overline">${escapeHtml(overline)}</span>` : ''}
          <span class="kappbar-title">${escapeHtml(brandLabel)}</span>
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
    : '';

  const actions = actionsHtml
    ? `<div class="kappbar-group kappbar-actions">${actionsHtml}</div>`
    : '';

  host.innerHTML = `
    <div class="kappbar" data-service="${escapeHtml(currentService ?? '')}">
      ${brand}
      ${meta}
      ${tabsHtml}
      <div class="kappbar-spacer"></div>
      ${actions}
    </div>`;

  const root = host.firstElementChild;
  if (root) {
    root.style.setProperty('--kappbar-accent', accentValue);
  }
  return host;
}