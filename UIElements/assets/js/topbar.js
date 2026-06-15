/**
 * topbar.js — shared top bar for Kore suite applications.
 */

import { SUITE_ICONS, resolveIcon } from './icons.js';
import { SUITE_VERSION } from './suiteMeta.js';
import { applyTheme, themeFor } from './theme.js';

function currentHost() {
	return (typeof window !== 'undefined' && window.location?.hostname) || '127.0.0.1';
}

const DEFAULT_SERVICES = [
	{ key: 'korestack', label: 'KoreStack', path: '/', port: 9600, icon: 'korestack' },
	{ key: 'koreagent', label: 'KoreAgent', path: '/', port: 9601, icon: 'koreagent' },
	{ key: 'korechat', label: 'KoreChat', path: '/ui', port: 9602, icon: 'korechat' },
	{ key: 'koredata', label: 'KoreData', path: '/', port: 9603, icon: 'koredata' },
	{ key: 'koredocs', label: 'KoreDocs', path: '/ui', port: 9610, icon: 'koredocs' },
	{ key: 'korecode', label: 'KoreCode', path: '/ui', port: 9611, icon: 'korecode' },
	{ key: 'korecomms', label: 'KoreComms', path: '/', port: 9609, icon: 'korecomms' },
];

const SUITE_VERSION_RE = /export\s+const\s+SUITE_VERSION\s*=\s*['\"]([^'\"]+)['\"]/;

function serviceUrl(service, currentService, urls) {
	if (urls[service.key]) return urls[service.key];
	try {
		const cached = JSON.parse(localStorage.getItem('kore.suite-urls') || 'null');
		if (cached?.[service.key]) return cached[service.key];
	} catch (_) {}
	if (typeof window !== 'undefined' && service.key === currentService) {
		return new URL(service.path, window.location.origin).href;
	}
	return `http://${currentHost()}:${service.port}${service.path}`;
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
			<span class="ktopbar-label">${service.label}</span>
		</a>`;
}

let versionRefreshPromise = null;

async function refreshVersionChip(host) {
	const chip = host.querySelector('#version-chip');
	if (!chip) return;
	if (versionRefreshPromise === null) {
		versionRefreshPromise = fetch('/ui-elements/assets/js/suiteMeta.js', { cache: 'no-store' })
			.then((response) => (response.ok ? response.text() : ''))
			.catch(() => '');
	}
	try {
		const text = await versionRefreshPromise;
		const match = SUITE_VERSION_RE.exec(text);
		if (match?.[1]) chip.textContent = match[1];
	} catch (_) {}
}

let _lastTopbarOptions = null;

// On a fresh browser session, seed the URL registry from KoreStack's /suite-urls endpoint.
// Renders immediately with whatever is available (localStorage or defaults), then re-renders
// once the fetch returns if localStorage was empty.
function _seedUrlsFromKoreStack(koreStackUrl = null) {
	const fallbackBase = koreStackUrl
		? koreStackUrl.replace(/\/$/, '')
		: (() => {
			const korestack = DEFAULT_SERVICES.find((s) => s.key === 'korestack');
			if (!korestack) return null;
			return `http://${currentHost()}:${korestack.port}`;
		})();
	if (!fallbackBase) return;
	fetch(`${fallbackBase}/suite-urls`, { cache: 'no-store' })
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
		_seedUrlsFromKoreStack(urls.korestack || null);
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
			${versionText ? `<div class="ktopbar-trailing"><span id="version-chip" class="kcui-tag kcui-tag--dim" title="Suite version">${versionText}</span></div>` : ''}
		</nav>`;

	if (versionText) {
		refreshVersionChip(host);
	}

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
