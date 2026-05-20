import { initAppBar, initTopbar, SUITE_ICONS, resolveIcon } from '/ui-elements/assets/js/chrome.js?v=20260508b';

const SERVICE_KEY_BY_SLUG = {
  koreagent: 'koreagent',
  korechat: 'korechat',
  koredatagateway: 'koredata',
  koredocs: 'koredocs',
  korecode: 'korecode',
  korecomms: 'korecomms',
};

const STATE_COLOR = {
  up: 'accent',
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

function initServicePanelIcons() {
  for (const row of document.querySelectorAll('[data-service-card]')) {
    const serviceKey = SERVICE_KEY_BY_SLUG[row.dataset.serviceCard];
    const glyph = row.querySelector('.service-glyph');
    if (!serviceKey || !glyph) continue;
    const iconHtml = resolveIcon(SUITE_ICONS, serviceKey, SERVICES_PANEL_ICON_SIZE);
    if (iconHtml) glyph.innerHTML = iconHtml;
  }
}

function stateForService(service) {
  return service.reachable ? 'up' : (service.running ? 'starting' : 'down');
}

function stateLabel(service) {
  return service.reachable ? 'Reachable' : (service.running ? 'Starting' : 'Stopped');
}

function setText(node, value) {
  if (node) node.textContent = value;
}

function setSuiteUrls(urls) {
  try {
    localStorage.setItem('kore.suite-urls', JSON.stringify(urls));
  } catch (_error) {
  }
}

function suiteUrlsFromSnapshot(snapshot) {
  const urls = { korestack: window.location.origin + '/' };
  for (const service of snapshot.services || []) {
    const key = SERVICE_KEY_BY_SLUG[service.slug];
    if (key) urls[key] = service.url;
  }
  return urls;
}

function updateCard(service) {
  const card = document.querySelector(`[data-service-card="${service.slug}"]`);
  if (!card) return;
  card.classList.remove('up', 'starting', 'down');
  card.classList.add(stateForService(service));

  const stateTag = card.querySelector('[data-field="state"]');
  setText(stateTag, stateLabel(service));
  if (stateTag) {
    stateTag.classList.remove('kcui-tag--accent', 'kcui-tag--warning', 'kcui-tag--danger');
    stateTag.classList.add(`kcui-tag--${STATE_COLOR[stateForService(service)] || 'dim'}`);
  }

  const hostInput = card.querySelector('[data-field="host"]');
  if (hostInput && hostInput !== document.activeElement && !hostInput.dataset.dirty) {
    hostInput.value = service.host ?? '-';
  }

  const portInput = card.querySelector('[data-field="port"]');
  if (portInput && portInput !== document.activeElement && !portInput.dataset.dirty) {
    portInput.value = String(service.port ?? '');
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
  const previous = localStorage.getItem('kore.suite-urls');
  setSuiteUrls(urls);
  if (previous !== JSON.stringify(urls) && typeof window._refreshTopbar === 'function') {
    window._refreshTopbar(urls);
  }

  const metrics = next.stack.metrics;
  setText(document.querySelector('[data-stack-field="running"] strong'), `${metrics.running} / ${metrics.selected}`);
  setText(document.querySelector('[data-stack-field="reachable"] strong'), String(metrics.reachable));
  setText(document.querySelector('[data-stack-field="dashboard"] strong'), window.location.href);
  setText(document.querySelector('[data-stack-field="ui"] strong'), next.stack.uiElementsMounted ? 'available' : 'missing');

  for (const service of next.services) {
    updateCard(service);
  }
}

async function refresh() {
  try {
    const response = await fetch('/status', { cache: 'no-store' });
    if (!response.ok) return;
    applySnapshot(await response.json());
  } catch (_error) {
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
    await refresh();
    if (action === 'stop') {
      showNotice(card, 'Stopped', 'warn');
    } else if (action === 'start' || action === 'restart') {
      showNotice(card, result?.reachable ? 'Reachable' : 'Starting - check logs', result?.reachable ? 'ok' : 'warn');
    }
  } finally {
    window.setTimeout(() => buttons.forEach((button) => { button.disabled = false; }), 300);
  }
}

async function setAddressAction(slug, host, port) {
  const button = document.querySelector(`[data-service="${slug}"][data-action="setaddress"]`);
  const card = document.querySelector(`[data-service-card="${slug}"]`);
  const cell = button?.closest('.address-edit');
  const hostInput = cell?.querySelector('.host-input');
  const portInput = cell?.querySelector('.port-input');
  if (button) button.disabled = true;
  try {
    const response = await fetch(`/api/services/${slug}/setaddress`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ host, port: parseInt(port, 10) }),
    });
    const result = response.ok ? await response.json().catch(() => null) : null;
    if (hostInput) delete hostInput.dataset.dirty;
    if (portInput) delete portInput.dataset.dirty;
    await refresh();
    showNotice(card, result?.reachable ? 'Reachable' : 'Starting - check logs', result?.reachable ? 'ok' : 'warn');
  } finally {
    window.setTimeout(() => { if (button) button.disabled = false; }, 600);
  }
}

function wireControls() {
  for (const button of document.querySelectorAll('[data-service][data-action]:not([data-action="setaddress"])')) {
    button.addEventListener('click', () => serviceAction(button.dataset.service, button.dataset.action));
  }

  for (const button of document.querySelectorAll('[data-action="setaddress"]')) {
    button.addEventListener('click', () => {
      const cell = button.closest('.address-edit');
      const host = cell?.querySelector('.host-input')?.value?.trim();
      const port = cell?.querySelector('.port-input')?.value;
      if (host && port) setAddressAction(button.dataset.service, host, port);
    });
  }

  for (const input of document.querySelectorAll('.port-input, .host-input')) {
    input.addEventListener('input', () => { input.dataset.dirty = '1'; });
    input.addEventListener('blur', () => { if (input.dataset.dirty) delete input.dataset.dirty; });
  }
}

setSuiteUrls(bootstrap.suiteUrls || {});
initTopbar({ currentService: 'korestack', urls: bootstrap.suiteUrls || {} });
initAppBar({
  currentService: 'korestack',
  overline: 'Landing page & Config',
  brandLabel: 'KoreStack',
  brandIcon: 'korestack',
  chips: bootstrap.chips || [],
});
initServicePanelIcons();
window._refreshTopbar = (urls) => {
  initTopbar({ currentService: 'korestack', urls });
  initServicePanelIcons();
};

wireControls();
applySnapshot(current);
window.setInterval(refresh, 2000);
