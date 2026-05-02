import { koreDocFileIcon, koreSheetFileIcon, koreDiagFileIcon } from '/ui-elements/assets/js/svg_icons.js';

const ICONS = {
  koredoc:   koreDocFileIcon(18),
  koresheet: koreSheetFileIcon(18),
  kodiag:    koreDiagFileIcon(18),
};

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

/**
 * Wire the open/close behaviour for the rendered app menu bar.
 * Must be called after renderAppMenu() so the DOM elements exist.
 *
 * @param {function(string):void} onAction  Called with the action string when
 *   a menu item is clicked. Provide the app-specific handler here.
 */
export function initAppMenuEvents(onAction) {
  function _closeAll() {
    document.querySelectorAll('.menu-item.open').forEach(m => m.classList.remove('open'));
  }

  document.querySelectorAll('.menu-item').forEach(item => {
    item.addEventListener('mousedown', e => {
      e.stopPropagation();
      const wasOpen = item.classList.contains('open');
      _closeAll();
      if (!wasOpen) item.classList.add('open');
    });
  });

  document.addEventListener('mousedown', _closeAll);

  document.querySelectorAll('.dropdown li[data-action]').forEach(li => {
    li.addEventListener('mousedown', e => {
      e.stopPropagation();
      _closeAll();
      onAction(li.dataset.action);
    });
  });
}

export function renderAppMenu({ app, appLabel, titleId, dirtyId, initialTitle, menus, editableTitle = false }) {
  const host = document.getElementById('app-menu-host');
  if (!host) return;

  host.innerHTML = `
    <nav id="menu-bar">
      <div id="app-brand">
        ${ICONS[app] ?? ''}
        ${appLabel}
      </div>
      ${menus.map(_renderMenuGroup).join('')}
      <span id="${titleId}" data-role="app-title" class="${editableTitle ? 'is-editable' : ''}"${editableTitle ? ' title="Double-click to rename"' : ''}>${initialTitle}</span>
      <span id="${dirtyId}" data-role="app-dirty" class="hidden">●</span>
    </nav>`;
}