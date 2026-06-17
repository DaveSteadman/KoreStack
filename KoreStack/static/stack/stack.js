const SHELL_MODULE_URL = '/ui-elements/assets/js/chrome.js';

const SERVICE_KEY_BY_SLUG = {
  koreagent: 'koreagent',
  korechat: 'korechat',
  koredatagateway: 'koredata',
  koredocs: 'koredocs',
  korecode: 'korecode',
  korecomms: 'korecomms',
};

const STATE_COLOR = {
  up: 'success',
  starting: 'warning',
  down: 'danger',
};

const SERVICES_PANEL_ICON_SIZE = 39;

function readBootstrap() {
  const node = document.getElementById('stack-bootstrap');
  if (!node) return {};
  try {
    return JSON.parse(node.textContent || '{}');
  } catch (_error) {
    return {};
  }
}

const bootstrap = readBootstrap();
let current = bootstrap.snapshot || { stack: { metrics: {} }, services: [] };
let chromeApi = null;
let refreshTimer = null;

function setText(node, value) {
  if (node) node.textContent = value;
}

function setValueById(id, value) {
  setText(document.getElementById(id), value);
}

function setSuiteUrls(urls) {
  try {
    localStorage.setItem('kore.suite-urls', JSON.stringify(urls));
  } catch (_error) {
  }
}

function suiteUrlsFromSnapshot(snapshot) {
  const urls = { korestack: `${window.location.origin}/` };
  for (const service of snapshot.services || []) {
    const key = SERVICE_KEY_BY_SLUG[service.slug];
    if (key) urls[key] = service.url;
  }
  return urls;
}

function stateForService(service) {
  return service.reachable ? 'up' : (service.running ? 'starting' : 'down');
}

function stateLabel(service) {
  return service.reachable ? 'Running' : (service.running ? 'Starting' : 'Stopped');
}

function initServicePanelIcons() {
  if (!chromeApi?.resolveIcon || !chromeApi?.SUITE_ICONS) return;
  for (const row of document.querySelectorAll('[data-service-card]')) {
    const serviceKey = SERVICE_KEY_BY_SLUG[row.dataset.serviceCard];
    const glyph = row.querySelector('.service-glyph');
    if (!serviceKey || !glyph) continue;
    const iconHtml = chromeApi.resolveIcon(chromeApi.SUITE_ICONS, serviceKey, SERVICES_PANEL_ICON_SIZE);
    if (iconHtml) glyph.innerHTML = iconHtml;
  }
}

function updateCard(service) {
  const card = document.querySelector(`[data-service-card="${service.slug}"]`);
  if (!card) return;
  card.classList.remove('up', 'starting', 'down');
  card.classList.add(stateForService(service));

  const stateTag = card.querySelector('[data-field="state"]');
  setText(stateTag, stateLabel(service));
  if (stateTag) {
    stateTag.classList.remove('kcui-tag--accent', 'kcui-tag--success', 'kcui-tag--warning', 'kcui-tag--danger');
    stateTag.classList.add(`kcui-tag--${STATE_COLOR[stateForService(service)] || 'dim'}`);
  }

  const urlLink = card.querySelector('[data-field="url"]');
  if (urlLink) {
    urlLink.textContent = service.url;
    urlLink.href = service.url;
  }
}

function applySnapshot(next) {
  current = next;
  const urls = suiteUrlsFromSnapshot(next);
  let previous = null;
  try {
    previous = localStorage.getItem('kore.suite-urls');
  } catch (_error) {
  }
  setSuiteUrls(urls);
  if (previous !== JSON.stringify(urls) && typeof window._refreshTopbar === 'function') {
    window._refreshTopbar(urls);
  }

  const metrics = next.stack?.metrics || {};
  setValueById('stack-running-value', `${metrics.running ?? 0} / ${metrics.selected ?? 0}`);
  setValueById('stack-reachable-value', String(metrics.reachable ?? 0));
  setValueById('stack-dashboard-value', window.location.href);
  setValueById('stack-ui-value', next.stack?.uiElementsMounted ? 'available' : 'missing');

  if (!next.stack?.uiElementsMounted) {
    console.warn('[KoreStack] Shared UI assets are not mounted from UIElements. Using local fallback shell.');
  }

  for (const service of next.services || []) {
    updateCard(service);
  }
}

async function refresh() {
  try {
    const response = await fetch('/status', { cache: 'no-store' });
    if (!response.ok) return;
    applySnapshot(await response.json());
  } catch (_error) {
    console.warn('[KoreStack] Status refresh failed.');
  }
}

function showNotice(card, message, tone) {
  if (!card) return;
  let notice = card.querySelector('.service-notice');
  if (!notice) {
    notice = document.createElement('div');
    notice.className = 'service-notice';
    card.appendChild(notice);
  }
  notice.textContent = message;
  notice.dataset.tone = tone || '';
  notice.classList.add('is-visible');
  clearTimeout(notice._timer);
  notice._timer = window.setTimeout(() => notice.classList.remove('is-visible'), 4000);
}

async function serviceAction(service, action) {
  const buttons = document.querySelectorAll(`[data-service="${service}"]`);
  const card = document.querySelector(`[data-service-card="${service}"]`);
  buttons.forEach((button) => { button.disabled = true; });
  try {
    const response = await fetch(`/api/services/${service}/${action}`, { method: 'POST' });
    const result = response.ok ? await response.json().catch(() => null) : null;
    if (!response.ok) {
      showNotice(card, `Action failed (${response.status})`, 'error');
      return;
    }
    await refresh();
    if (action === 'stop') {
      showNotice(card, 'Stopped', 'warn');
    } else if (action === 'start' || action === 'restart') {
      showNotice(card, result?.reachable ? 'Reachable' : 'Starting - check logs', result?.reachable ? 'ok' : 'warn');
    }
  } catch (_error) {
    showNotice(card, 'Action failed', 'error');
  } finally {
    window.setTimeout(() => buttons.forEach((button) => { button.disabled = false; }), 300);
  }
}

function wireControls() {
  for (const button of document.querySelectorAll('[data-service][data-action]')) {
    button.addEventListener('click', () => serviceAction(button.dataset.service, button.dataset.action));
  }
}

function startRefreshLoop() {
  if (refreshTimer !== null) return;
  refreshTimer = window.setInterval(() => {
    if (document.visibilityState === 'visible') {
      refresh();
    }
  }, 2000);
}

async function initChrome() {
  try {
    chromeApi = await import(SHELL_MODULE_URL);
    setSuiteUrls(bootstrap.suiteUrls || {});
    chromeApi.initTopbar({ currentService: 'korestack', urls: bootstrap.suiteUrls || {} });
    chromeApi.initAppBar({
      currentService: 'korestack',
      overline: 'Landing page & Config',
      brandLabel: 'KoreStack',
      brandIcon: 'korestack',
      chips: bootstrap.chips || [],
    });
    initServicePanelIcons();
    window._refreshTopbar = (urls) => {
      chromeApi.initTopbar({ currentService: 'korestack', urls });
      initServicePanelIcons();
    };
  } catch (_error) {
    console.warn('[KoreStack] Shared UI shell could not be loaded. Falling back to local landing-page styling.', _error);
  }
}

wireControls();
applySnapshot(current);
startRefreshLoop();
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    refresh();
  }
});
void initChrome();
