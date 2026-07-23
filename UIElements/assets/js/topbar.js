/**
 * topbar.js — shared top bar for Kore suite applications.
 */

import { SUITE_ICONS, resolveIcon } from './icons.js';
import { SUITE_VERSION } from './suiteMeta.js';
import { applyTheme, themeFor } from './theme.js';
import { KCUI_STORAGE_KEYS } from './constants.js';

function currentHost() {
	return (typeof window !== 'undefined' && window.location?.hostname) || '127.0.0.1';
}

function cachedSuiteUrls() {
	try {
		return JSON.parse(localStorage.getItem(KCUI_STORAGE_KEYS.suiteUrls) || 'null');
	} catch (_) {
		return null;
	}
}

const DEFAULT_SERVICES = [
	{ key: 'korestack',  label: 'KoreStack',  path: '/',   icon: 'korestack'  },
	{ key: 'koreagent',  label: 'KoreAgent',  path: '/',   icon: 'koreagent'  },
	{ key: 'korechat',   label: 'KoreChat',   path: '/ui', icon: 'korechat'   },
	{ key: 'koredata',   label: 'KoreData',   path: '/',   icon: 'koredata'   },
	{ key: 'koreliveweb',label: 'KoreLiveWeb',path: '/ui', icon: 'koreliveweb'},
	{ key: 'koredocs',   label: 'KoreDocs',   path: '/ui', icon: 'koredocs'   },
	{ key: 'korecode',   label: 'KoreCode',   path: '/ui', icon: 'korecode'   },
	{ key: 'korecomms',  label: 'KoreComms',  path: '/',   icon: 'korecomms'  },
];

const SUITE_VERSION_RE = /export\s+const\s+SUITE_VERSION\s*=\s*['\"]([^'\"]+)['\"]/;

function suiteRegistry(urls) {
	return {
		...(cachedSuiteUrls() || {}),
		...((typeof window !== 'undefined' && window.__koreSuiteUrls) || {}),
		...(urls || {}),
	};
}

function serviceUrl(service, currentService, urls) {
	const registry = suiteRegistry(urls);
	if (registry[service.key]) return registry[service.key];
	if (typeof window !== 'undefined' && service.key === currentService) {
		return new URL(service.path, window.location.origin).href;
	}
	return null;
}

function serviceIcon(service, iconSize) {
	return resolveIcon(SUITE_ICONS, service.icon, iconSize);
}

function serviceItemHtml(service, currentService, urls, iconSize) {
	const url = serviceUrl(service, currentService, urls);
	const active = service.key === currentService ? ' is-active' : '';
	const disabled = !url ? ' is-disabled' : '';
	const accent = themeFor(service.key)?.accent || 'var(--accent)';
	const tag = url ? 'a' : 'span';
	const hrefAttr = url ? ` href="${url}"` : '';
	return `
		<${tag} class="ktopbar-item${active}${disabled}" data-service="${service.key}" title="${service.label}" style="--topbar-accent:${accent}"${hrefAttr}>
			<span class="ktopbar-icon" aria-hidden="true">${serviceIcon(service, iconSize)}</span>
			<span class="ktopbar-label">${service.label}</span>
		</${tag}>`;
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
		: (
			(typeof window !== 'undefined' && window.__koreSuiteUrls?.korestack)
			|| cachedSuiteUrls()?.korestack
			|| null
		);
	if (!fallbackBase) return;
	fetch(`${fallbackBase}/suite-urls`, { cache: 'no-store' })
		.then((r) => (r.ok ? r.json() : null))
		.then((data) => {
			if (!data) return;
			localStorage.setItem(KCUI_STORAGE_KEYS.suiteUrls, JSON.stringify(data));
			if (_lastTopbarOptions !== null) initTopbar(_lastTopbarOptions);
		})
		.catch(() => {});
}

function visibleServices(services, urls, currentService) {
	return services;
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
	if (typeof window !== 'undefined' && !localStorage.getItem(KCUI_STORAGE_KEYS.suiteUrls)) {
		_seedUrlsFromKoreStack(urls.korestack || null);
	}

	if (currentService) {
		applyTheme(document.documentElement, currentService);
	}

	host.style.setProperty('--topbar-pad-x', padX);
	const renderedServices = visibleServices(services, urls, currentService);

	host.innerHTML = `
		<nav class="ktopbar-nav" aria-label="Kore suite services">
			<div class="ktopbar-main">
				${renderedServices.map((service) => serviceItemHtml(service, currentService, urls, iconSize)).join('')}
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
		if (e.key === KCUI_STORAGE_KEYS.suiteUrls && _lastTopbarOptions !== null) {
			initTopbar(_lastTopbarOptions);
		}
	});
}
