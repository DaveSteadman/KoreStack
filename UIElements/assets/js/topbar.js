/**
 * topbar.js — shared top bar for Kore suite applications.
 */

import { SUITE_ICONS, resolveIcon } from './icons.js';
import { SUITE_VERSION } from './suiteMeta.js';
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

function serviceItemHtml(service, currentService, urls, iconSize) {
	const url = serviceUrl(service, currentService, urls);
	const active = service.key === currentService ? ' is-active' : '';
	const accent = themeFor(service.key)?.accent || 'var(--accent)';
	return `
		<a class="ktopbar-item${active}" data-service="${service.key}" href="${url}" title="${service.label}" style="--topbar-accent:${accent}">
			<span class="ktopbar-icon" aria-hidden="true">${serviceIcon(service, iconSize)}</span>
			<span class="ktopbar-label kcui-text-topbar">${service.label}</span>
		</a>`;
}

export function initTopbar(options = {}) {
	const {
		mountId = 'topbar',
		currentService = null,
		urls = {},
		iconSize = 14,
		padX = '16px',
		services = DEFAULT_SERVICES,
		versionText = SUITE_VERSION,
	} = options;

	const host = document.getElementById(mountId);
	if (!host) return null;

	if (currentService) {
		applyTheme(document.documentElement, currentService);
	}

	host.style.setProperty('--topbar-pad-x', padX);

	host.innerHTML = `
		<nav class="ktopbar-nav" aria-label="Kore suite services">
			<div class="ktopbar-main">
				${services.map((service) => serviceItemHtml(service, currentService, urls, iconSize)).join('')}
			</div>
			${versionText ? `<div class="ktopbar-trailing"><span id="version-chip" class="kcui-text-topbar" title="Suite version">${versionText}</span></div>` : ''}
		</nav>`;

	const nav = host.querySelector('.ktopbar-nav');
	if (nav) {
		nav.style.padding = `0 ${padX}`;
	}
	return host;
}

export function topbarServiceDefaults() {
	return DEFAULT_SERVICES.map((service) => ({ ...service }));
}
