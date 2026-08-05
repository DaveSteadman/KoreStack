import { SUITE_ICONS, resolveIcon } from './icons.js';

const DEFAULT_CONFIG = {
  icons: SUITE_ICONS,
  iconSize: 18,
};

function _mergeConfig(config = {}) {
  return {
    ...DEFAULT_CONFIG,
    ...config,
    icons: {
      ...DEFAULT_CONFIG.icons,
      ...(config.icons || {}),
    },
  };
}

function _menuIcon(config, app, appIcon) {
  if (typeof appIcon === 'string') return appIcon;
  if (typeof appIcon === 'function') return appIcon(config.iconSize);
  return resolveIcon(config.icons, app, config.iconSize);
}

function _renderMenuItem(item) {
  if (item.separator) return '<li class="sep"></li>';
  const shortcut = item.shortcut ? `<span class="shortcut">${item.shortcut}</span>` : '';
  return `<li data-action="${item.action}">${item.label}${shortcut}</li>`;
}

function _renderMenuGroup(group) {
  return `
    <div class="menu-item" data-menu="${group.id}">${group.label}
      <ul class="dropdown">${group.items.map(_renderMenuItem).join('')}</ul>
    </div>`;
}

export function initAppMenuEvents(onAction) {
  function _closeAll() {
    document.querySelectorAll('.menu-item.open').forEach(item => item.classList.remove('open'));
  }

  document.querySelectorAll('.menu-item').forEach(item => {
    item.addEventListener('mousedown', event => {
      event.stopPropagation();
      const wasOpen = item.classList.contains('open');
      _closeAll();
      if (!wasOpen) item.classList.add('open');
    });
  });

  document.addEventListener('mousedown', _closeAll);

  document.querySelectorAll('.dropdown li[data-action]').forEach(item => {
    item.addEventListener('mousedown', event => {
      event.stopPropagation();
      _closeAll();
      onAction(item.dataset.action);
    });
  });
}

export function renderAppMenu({ app, appLabel, titleId, dirtyId, initialTitle, menus, editableTitle = false, appIcon, config = {} }) {
  const host = document.getElementById('app-menu-host');
  if (!host) return;
  const resolvedConfig = _mergeConfig(config);

  host.innerHTML = `
    <nav id="menu-bar">
      <div id="app-brand">
        ${_menuIcon(resolvedConfig, app, appIcon)}
        ${appLabel}
      </div>
      ${menus.map(_renderMenuGroup).join('')}
      <span id="${titleId}" data-role="app-title" class="${editableTitle ? 'is-editable' : ''}"${editableTitle ? ' title="Double-click to rename"' : ''}>${initialTitle}</span>
      <span id="${dirtyId}" data-role="app-dirty" class="hidden">●</span>
    </nav>`;
}

export function configureAppMenu(config = {}) {
  return _mergeConfig(config);
}
