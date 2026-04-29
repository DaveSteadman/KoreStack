import * as topbar from '/static/commonui/js/topbar.js';
import * as appbar from '/static/commonui/js/appbar.js';
import { renderAppMenu } from '/static/commonui/js/appMenu.js';

export function initChrome() {
  topbar.initTopbar({ currentService: 'koredocs' });
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

  appbar.initAppTabs('koredoc');
}
