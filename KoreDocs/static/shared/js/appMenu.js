const ICONS = {
  koredoc: `
    <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" width="18" height="18">
      <rect x="3" y="2" width="14" height="16" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/>
      <line x1="6" y1="6" x2="14" y2="6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      <line x1="6" y1="9" x2="14" y2="9" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      <line x1="6" y1="12" x2="11" y2="12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
    </svg>`,
  koresheet: `
    <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" width="18" height="18">
      <rect x="2" y="2" width="16" height="16" rx="2" stroke="currentColor" stroke-width="1.5"/>
      <line x1="2" y1="7" x2="18" y2="7" stroke="currentColor" stroke-width="1.2"/>
      <line x1="2" y1="12" x2="18" y2="12" stroke="currentColor" stroke-width="1.2"/>
      <line x1="8" y1="2" x2="8" y2="18" stroke="currentColor" stroke-width="1.2"/>
      <line x1="13" y1="2" x2="13" y2="18" stroke="currentColor" stroke-width="1.2"/>
    </svg>`,
  kodiag: `
    <svg width="16" height="16" viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <circle cx="4" cy="10" r="2.5" stroke="currentColor" stroke-width="1.5"/>
      <circle cx="16" cy="4" r="2.5" stroke="currentColor" stroke-width="1.5"/>
      <circle cx="16" cy="16" r="2.5" stroke="currentColor" stroke-width="1.5"/>
      <line x1="6.2" y1="9" x2="13.8" y2="5" stroke="currentColor" stroke-width="1.3"/>
      <line x1="6.2" y1="11" x2="13.8" y2="15" stroke="currentColor" stroke-width="1.3"/>
    </svg>`,
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