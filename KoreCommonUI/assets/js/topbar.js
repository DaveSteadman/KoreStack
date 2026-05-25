/**
 * topbar.js — KoreCommonUI suite navigation bar renderer.
 *
 * This file is the single source of truth for which services exist, in what
 * order, and how they are labelled and coloured.  Never write service entries
 * by hand in HTML — the list here drives every page automatically.
 *
 * Usage in HTML:
 *   <div
 *     class="kc-bar kc-bar--1"
 *     data-kc-topbar
 *     data-active="agent"
 *     data-kc-items="stack,agent,chat"
 *     data-kc-links='{"stack":"/","agent":"/agent","chat":"/chat"}'
 *     data-kc-labels='{"chat":"KoreChat Pro"}'>
 *     <span class="kc-bar__end">v1.0.0</span>
 *   </div>
 *   <script type="module" src="../assets/js/topbar.js"></script>
 *
 * Attributes on the bar element:
 *   data-kc-topbar         marks the element for auto-init
 *   data-active="<key>"    which service entry gets is-active (omit for none)
 *   data-kc-items          comma list of service keys and order for this page
 *   data-kc-links          JSON map of key -> href for this page
 *   data-kc-labels         JSON map of key -> label override for this page
 *
 * The icon CSS classes (kc-icon--*) are defined in assets/css/icons.css.
 * Consuming pages should include tokens.css, fonts.css, common.css, and icons.css.
 */

/** ── Service registry ──────────────────────────────────────────────────────
 *  Each entry:
 *    key        — matches data-active value and --svc-<key> token in tokens.css
 *    name       — display label
 *    colorVar   — CSS custom property that supplies the accent colour
 *    iconClass  — CSS class that sets --icon-url via mask-image technique
 */
const SERVICES = [
  { key: 'stack', name: 'KoreStack', colorVar: '--svc-stack', iconClass: 'kc-icon--stack', href: '#' },
  { key: 'agent', name: 'KoreAgent', colorVar: '--svc-agent', iconClass: 'kc-icon--agent', href: '#' },
  { key: 'chat',  name: 'KoreChat',  colorVar: '--svc-chat',  iconClass: 'kc-icon--chat',  href: '#' },
  { key: 'data',  name: 'KoreData',  colorVar: '--svc-data',  iconClass: 'kc-icon--data',  href: '#' },
  { key: 'docs',  name: 'KoreDocs',  colorVar: '--svc-docs',  iconClass: 'kc-icon--docs',  href: '#' },
  { key: 'code',  name: 'KoreCode',  colorVar: '--svc-code',  iconClass: 'kc-icon--code',  href: '#' },
  { key: 'comms', name: 'KoreComms', colorVar: '--svc-comms', iconClass: 'kc-icon--comms', href: '#' },
];

const SERVICE_BY_KEY = Object.fromEntries(SERVICES.map((svc) => [svc.key, svc]));

function parseJsonMap(raw) {
  if (!raw) return {};
  try {
    const value = JSON.parse(raw);
    return value && typeof value === 'object' ? value : {};
  } catch (_) {
    return {};
  }
}

function parseItemOrder(raw) {
  if (!raw) return SERVICES.map((svc) => svc.key);
  return raw
    .split(',')
    .map((v) => v.trim())
    .filter((v) => v.length > 0);
}

function resolveBarServices(barEl) {
  const order = parseItemOrder(barEl.dataset.kcItems);
  const links = parseJsonMap(barEl.dataset.kcLinks);
  const labels = parseJsonMap(barEl.dataset.kcLabels);
  const list = [];

  for (const key of order) {
    const base = SERVICE_BY_KEY[key];
    if (!base) continue;
    list.push({
      ...base,
      href: typeof links[key] === 'string' ? links[key] : base.href,
      name: typeof labels[key] === 'string' ? labels[key] : base.name,
    });
  }

  return list;
}

/** Render all service entries into a topbar element. */
function renderTopbar(barEl) {
  const activeKey = barEl.dataset.active ?? '';
  const services  = resolveBarServices(barEl);
  const existingEnd = barEl.querySelector('.kc-bar__end');
  const trailingHtml = existingEnd ? existingEnd.outerHTML : '';

  // Build a UIElements2-like nav structure on every render to keep behavior stable.
  barEl.innerHTML = `
    <nav class="kc-topbar-nav" aria-label="Kore suite services">
      <div class="kc-topbar-main"></div>
      ${trailingHtml}
    </nav>`;

  const mainStrip = barEl.querySelector('.kc-topbar-main');
  if (!mainStrip) return;

  for (const svc of services) {
    const a = document.createElement('a');
    a.className = `kc-entry${svc.key === activeKey ? ' is-active' : ''}`;
    a.href      = svc.href;
    a.style.setProperty('--kc-entry-color', `var(${svc.colorVar})`);

    const iconWrap = document.createElement('span');
    iconWrap.className = 'kc-entry__icon';

    const icon = document.createElement('span');
    icon.className = `kc-icon ${svc.iconClass}`;
    icon.setAttribute('aria-hidden', 'true');
    iconWrap.appendChild(icon);

    const label = document.createElement('span');
    label.className   = 'kc-entry__name';
    label.textContent = svc.name;

    a.appendChild(iconWrap);
    a.appendChild(label);

    mainStrip.appendChild(a);
  }
}

// Auto-init every [data-kc-topbar] element on the page.
document.querySelectorAll('[data-kc-topbar]').forEach(renderTopbar);
