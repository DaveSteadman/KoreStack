const SHELL_MODULE_URL = '/ui-elements/assets/js/chrome.js';

function readBootstrap() {
  const node = document.getElementById('endpoint-bootstrap');
  if (!node) return {};
  try {
    return JSON.parse(node.textContent || '{}');
  } catch (_error) {
    return {};
  }
}

const bootstrap = readBootstrap();
let catalog = bootstrap.catalog || { services: [], stats: {} };
let chromeApi = null;
let selected = null;

function escHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function $(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const node = $(id);
  if (node) node.textContent = value;
}

function safeSummary(route) {
  return route.summary || route.description || '';
}

function routeUrl(service, route) {
  return `${service.base_url}${route.path}`;
}

function allRoutes() {
  const rows = [];
  for (const service of catalog.services || []) {
    const routes = service.manifest?.routes || [];
    for (const route of routes) {
      rows.push({ service, route });
    }
  }
  return rows;
}

function matchesFilters(service, route) {
  if (!service.reachable && !$('show-unreachable').checked) return false;
  if (route.kind === 'asset' && !$('show-assets').checked) return false;
  if (!route.include_in_schema && route.kind !== 'asset' && !$('show-hidden').checked) return false;

  const serviceFilter = $('service-filter').value;
  if (serviceFilter && serviceFilter !== service.key) return false;

  const needle = ($('route-search').value || '').trim().toLowerCase();
  if (!needle) return true;

  const haystack = [
    service.label,
    service.key,
    route.path,
    route.methods.join(','),
    safeSummary(route),
    route.kind,
  ].join(' ').toLowerCase();
  return haystack.includes(needle);
}

function renderServiceFilter() {
  const select = $('service-filter');
  const current = select.value;
  const options = ['<option value="">All services</option>'];
  for (const service of catalog.services || []) {
    options.push(`<option value="${escHtml(service.key)}">${escHtml(service.label)}</option>`);
  }
  select.innerHTML = options.join('');
  select.value = current;
}

function renderCatalogMeta() {
  const visible = allRoutes().filter(({ service, route }) => matchesFilters(service, route)).length;
  const summary = `${visible} visible routes across ${catalog.stats.reachable_count || 0}/${catalog.stats.service_count || 0} reachable services`;
  $('catalog-meta').textContent = summary;
  setText('endpoint-service-count', String(catalog.stats.service_count || 0));
  setText('endpoint-reachable-count', String(catalog.stats.reachable_count || 0));
  setText('endpoint-route-count', String(catalog.stats.route_count || 0));
}

function renderCatalog() {
  renderServiceFilter();
  renderCatalogMeta();

  const root = $('endpoint-list');
  const parts = [];

  for (const service of catalog.services || []) {
    const routes = (service.manifest?.routes || []).filter((route) => matchesFilters(service, route));
    if (!routes.length && (service.reachable || !$('show-unreachable').checked)) continue;

    parts.push(`
      <section class="endpoint-service">
        <div class="endpoint-service-header">
          <div>
            <h2>${escHtml(service.label)}</h2>
            <p>${escHtml(service.base_url)}</p>
          </div>
          <span class="kcui-tag kcui-tag--${service.reachable ? 'success' : 'danger'}">${service.reachable ? `${service.route_count} routes` : escHtml(service.error || 'offline')}</span>
        </div>
    `);

    if (!service.reachable) {
      parts.push(`<div class="endpoint-service-error">${escHtml(service.error || 'Service not reachable.')}</div></section>`);
      continue;
    }

    for (const route of routes) {
      const key = `${service.key}|${route.path}|${route.methods.join(',')}`;
      const selectedClass = selected?.key === key ? ' is-selected' : '';
      parts.push(`
        <a class="endpoint-row${selectedClass}" data-route-key="${escHtml(key)}" href="${escHtml(routeUrl(service, route))}" target="_blank" rel="noreferrer">
          <span class="endpoint-methods">${escHtml(route.methods.join(', '))}</span>
          <span class="endpoint-path">
            <span>${escHtml(route.path)}</span>
            <span class="endpoint-summary">${escHtml(safeSummary(route))}</span>
          </span>
          <span class="endpoint-kind">${escHtml(route.kind)}</span>
        </a>
      `);
    }
    parts.push('</section>');
  }

  root.innerHTML = parts.join('');
  for (const node of root.querySelectorAll('[data-route-key]')) {
    node.addEventListener('click', (event) => {
      if (!event.metaKey && !event.ctrlKey && event.button === 0) {
        event.preventDefault();
        const [serviceKey, path, methods] = node.dataset.routeKey.split('|');
        const service = (catalog.services || []).find((item) => item.key === serviceKey);
        const route = service?.manifest?.routes?.find((item) => item.path === path && item.methods.join(',') === methods);
        if (service && route) {
          selectRoute(service, route);
        }
      }
    });
  }
}

function setMethodOptions(methods) {
  const select = $('request-method');
  select.innerHTML = methods.map((method) => `<option value="${method}">${method}</option>`).join('');
}

function renderParamHints(route) {
  const parts = [];
  if (route.path_params.length) {
    parts.push(`Path: ${route.path_params.map((item) => `<code>{${item.name}}</code>`).join(' ')}`);
  }
  if (route.query_params.length) {
    parts.push(`Query: ${route.query_params.map((item) => `<code>${item.name}</code>`).join(' ')}`);
  }
  if (route.body_params.length) {
    parts.push(`Body: ${route.body_params.map((item) => `<code>${item.name}</code>`).join(' ')}`);
  }
  $('param-hints').innerHTML = parts.join(' | ') || 'No declared parameters.';
}

function defaultBody(route) {
  if (!route.body_params.length) return '';
  const body = {};
  for (const param of route.body_params) {
    body[param.name] = param.default ?? '';
  }
  return JSON.stringify(body, null, 2);
}

function selectRoute(service, route) {
  selected = {
    key: `${service.key}|${route.path}|${route.methods.join(',')}`,
    service,
    route,
  };
  setMethodOptions(route.methods.length ? route.methods : ['GET']);
  $('request-method').value = route.methods[0] || 'GET';
  $('request-url').value = routeUrl(service, route);
  $('request-open').href = routeUrl(service, route);
  $('request-content-type').value = route.body_params.length ? 'application/json' : 'text/plain';
  $('request-body').value = defaultBody(route);
  $('selection-summary').textContent = `${service.label} ${route.methods.join(', ')} ${route.path}`;
  renderParamHints(route);
  renderCatalog();
}

async function refreshCatalog() {
  const response = await fetch(bootstrap.catalogUrl || '/api/endpoints/catalog', { cache: 'no-store' });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  catalog = await response.json();
  renderCatalog();
}

async function submitRequest() {
  const payload = {
    method: $('request-method').value,
    url: $('request-url').value.trim(),
    content_type: $('request-content-type').value.trim(),
    body: $('request-body').value,
  };

  $('response-meta').textContent = 'Running...';
  $('response-body').textContent = '';

  try {
    const response = await fetch(bootstrap.requestUrl || '/api/endpoints/request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    const contentType = result.content_type || result.headers?.['Content-Type'] || '';
    $('response-meta').textContent = `${result.status || response.status} ${result.reason || ''} ${contentType}`.trim();
    $('response-body').textContent = result.body_text || result.error || '';
  } catch (error) {
    $('response-meta').textContent = 'Request failed';
    $('response-body').textContent = String(error);
  }
}

function wireEvents() {
  $('route-search').addEventListener('input', renderCatalog);
  $('service-filter').addEventListener('change', renderCatalog);
  $('show-hidden').addEventListener('change', renderCatalog);
  $('show-assets').addEventListener('change', renderCatalog);
  $('show-unreachable').addEventListener('change', renderCatalog);
  $('refresh-catalog').addEventListener('click', async () => {
    $('catalog-meta').textContent = 'Refreshing...';
    try {
      await refreshCatalog();
    } catch (error) {
      $('catalog-meta').textContent = `Refresh failed: ${error}`;
    }
  });
  $('request-submit').addEventListener('click', submitRequest);
  $('request-url').addEventListener('input', () => {
    $('request-open').href = $('request-url').value.trim() || '#';
  });
}

async function initChrome() {
  try {
    chromeApi = await import(SHELL_MODULE_URL);
    chromeApi.initTopbar({ currentService: 'korestack', urls: bootstrap.suiteUrls || {} });
    chromeApi.initAppBar({
      currentService: 'korestack',
      overline: 'Inspect And Probe',
      brandLabel: 'Endpoint Explorer',
      brandIcon: 'korestack',
      chips: bootstrap.chips || [],
    });
  } catch (_error) {
    console.warn('[KoreStack] Shared UI shell could not be loaded for endpoints page.');
  }
}

wireEvents();
renderCatalog();
if ((catalog.services || []).length) {
  const firstReachable = catalog.services.find((service) => service.reachable && service.manifest?.routes?.length);
  if (firstReachable) {
    selectRoute(firstReachable, firstReachable.manifest.routes[0]);
  }
}
void initChrome();
