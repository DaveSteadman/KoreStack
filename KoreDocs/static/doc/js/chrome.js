import { initTopbar, initAppBar, initAppTabs, renderAppMenu } from '/ui-elements/assets/js/chrome.js?v=20260508b';

export function initChrome() {
  initTopbar({ currentService: 'koredocs', urls: window.__koreSuiteUrls || {} });
  initAppBar({
    mountId: 'tab-bar',
    currentService: 'koredocs',
    overline: 'Document Editor',
    brandLabel: 'KoreDoc',
    brandIcon: 'koredoc',
    editorTabsSlot: 'koredocs-tabs',
  });

  renderAppMenu({
    app: 'koredoc',
    appLabel: 'KoreDoc',
    titleId: 'doc-title',
    dirtyId: 'doc-dirty',
    initialTitle: 'Untitled',
    menus: [
      {
        id: 'edit',
        label: 'Edit',
        items: [
          { action: 'undo', label: 'Undo', shortcut: 'Ctrl+Z' },
          { action: 'redo', label: 'Redo', shortcut: 'Ctrl+Y' },
          { separator: true },
          { action: 'select-all', label: 'Select All', shortcut: 'Ctrl+A' },
        ],
      },
      {
        id: 'view',
        label: 'View',
        items: [
          { action: 'focus-editor', label: 'Focus Editor' },
          { action: 'focus-properties', label: 'Focus Properties' },
          { action: 'focus-map', label: 'Focus Map' },
        ],
      },
    ],
  });

  initAppTabs('koredoc', { mountId: 'koredocs-tabs', renderBrand: false });
}
