/**
 * topbar.js — shared top bar for Kore suite applications.
 */

import { SUITE_ICONS, resolveIcon } from './icons.js?v=20260501a';
import { SUITE_VERSION } from './suiteMeta.js';
import { applyTheme, themeFor } from './theme.js?v=20260501a';

const DEFAULT_HOST = '127.0.0.1';

const DEFAULT_SERVICES = [
	{ key: 'korestack', label: 'KoreStack', path: '/', port: 8600, icon: 'korestack' },
	{ key: 'koreagent', label: 'KoreAgent', path: '/', port: 8000, icon: 'koreagent' },
	{ key: 'korechat', label: 'KoreChat', path: '/ui', port: 8700, icon: 'korechat' },
	{ key: 'koredata', label: 'KoreData', path: '/ui', port: 8800, icon: 'koredata' },
	{ key: 'koredocs', label: 'KoreDocs', path: '/ui', port: 5500, icon: 'koredocs' },
	{ key: 'korecode', label: 'KoreCode', path: '/ui', port: 5600, icon: 'korecode' },
	{ key: 'korecomms', label: 'KoreComms', path: '/ui', port: 8900, icon: 'korecomms' },
];

function serviceUrl(service, currentService, urls) {
	if (urls[service.key]) return urls[service.key];
	try {
		const cached = JSON.parse(localStorage.getItem('kore.suite-urls') || 'null');
		if (cached?.[service.key]) return cached[service.key];
	} catch (_) {}
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

let _lastTopbarOptions = null;

// On a fresh browser session, seed the URL registry from KoreStack's /suite-urls endpoint.
// Renders immediately with whatever is available (localStorage or defaults), then re-renders
// once the fetch returns if localStorage was empty.
function _seedUrlsFromKoreStack() {
	const korestack = DEFAULT_SERVICES.find((s) => s.key === 'korestack');
	if (!korestack) return;
	fetch(`http://127.0.0.1:${korestack.port}/suite-urls`, { cache: 'no-store' })
		.then((r) => (r.ok ? r.json() : null))
		.then((data) => {
			if (!data) return;
			localStorage.setItem('kore.suite-urls', JSON.stringify(data));
			if (_lastTopbarOptions !== null) initTopbar(_lastTopbarOptions);
		})
		.catch(() => {});
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

	const host = document.getElementById(mountId)
		|| (mountId === 'topbar' ? document.getElementById('suite-topbar') : null);
	if (!host) return null;

	_lastTopbarOptions = options;

	// If the URL registry is absent (fresh browser session), fetch from KoreStack once to seed it.
	if (typeof window !== 'undefined' && !localStorage.getItem('kore.suite-urls')) {
		_seedUrlsFromKoreStack();
	}

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

// Re-render topbar on any page when KoreStack updates the URL registry in localStorage.
// (The 'storage' event only fires in tabs *other* than the one that wrote the value,
//  so KoreStack's own _refreshTopbar bridge handles the same-tab case.)
if (typeof window !== 'undefined') {
	window.addEventListener('storage', (e) => {
		if (e.key === 'kore.suite-urls' && _lastTopbarOptions !== null) {
			initTopbar(_lastTopbarOptions);
		}
	});
}
