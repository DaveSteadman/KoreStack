/**
 * suitebar.js — shared Kore suite service navigation.
 */

import { SUITE_ICONS, resolveIcon } from './icons.js';
import { applyTheme, themeFor } from './theme.js';

const DEFAULT_HOST = '127.0.0.1';

const DEFAULT_SERVICES = [
  { key: 'korestack', label: 'KoreStack', path: '/', port: 8600, icon: 'korestack' },
  { key: 'koreagent', label: 'KoreAgent', path: '/', port: 8000, icon: 'koreagent' },
  { key: 'koreconversation', label: 'KoreConversation', path: '/ui', port: 8700, icon: 'koreconversation' },
  { key: 'koredata', label: 'KoreData', path: '/', port: 8800, icon: 'koredata' },
  { key: 'koredocs', label: 'KoreDocs', path: '/kf', port: 5500, icon: 'koredocs' },
  { key: 'korecomms', label: 'KoreComms', path: '/', port: 8900, icon: 'korecomms' },
];

function serviceUrl(service, currentService, urls) {
  if (urls[service.key]) return urls[service.key];
  if (typeof window !== 'undefined' && service.key === currentService) {
    return new URL(service.path, window.location.origin).href;
  }
  return `http://${DEFAULT_HOST}:${service.port}${service.path}`;
}

function serviceIcon(service, iconSize) {
  return resolveIcon(SUITE_ICONS, service.icon, iconSize);
}

function serviceTabHtml(service, currentService, urls, iconSize) {
  const url = serviceUrl(service, currentService, urls);
  const active = service.key === currentService ? ' is-active' : '';
  const accent = themeFor(service.key)?.accent || 'var(--accent)';
  return `
    <a class="ksuite-tab${active}" data-service="${service.key}" href="${url}" title="${service.label}" style="--suite-accent:${accent}">
      <span class="ksuite-icon" aria-hidden="true">${serviceIcon(service, iconSize)}</span>
      <span class="ksuite-label">${service.label}</span>
    </a>`;
}

export function initSuiteTopbar(options = {}) {
  const {
    mountId = 'suite-topbar',
    currentService = null,
    urls = {},
    iconSize = 14,
    padX = '16px',
    services = DEFAULT_SERVICES,
  } = options;

  const host = document.getElementById(mountId);
  if (!host) return null;

  if (currentService) {
    applyTheme(document.documentElement, currentService);
  }

  host.style.setProperty('--suite-topbar-pad-x', padX);

  host.innerHTML = `
    <nav class="ksuite-nav" aria-label="Kore suite services">
      ${services.map((service) => serviceTabHtml(service, currentService, urls, iconSize)).join('')}
    </nav>`;
  const nav = host.querySelector('.ksuite-nav');
  if (nav) {
    nav.style.padding = `0 ${padX}`;
  }
  return host;
}

export function suiteServiceDefaults() {
  return DEFAULT_SERVICES.map((service) => ({ ...service }));
}