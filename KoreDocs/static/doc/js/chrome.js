import * as appbar from '/ui-elements/assets/js/appbar.js';
import { renderAppMenu } from '/ui-elements/assets/js/appMenu.js';

export function initChrome() {
  appbar.initAppBar({
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

  appbar.initAppTabs('koredoc', { mountId: 'koredocs-tabs', renderBrand: false });
}
