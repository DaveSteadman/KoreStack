import { initAppBar, initTopbar } from '/ui-elements/assets/js/chrome.js?v=20260501a';
import { initPanels } from '/ui-elements/assets/js/panels.js';
import { createEditor } from './editor.js';
import { initFind, runFind, runFindNext, runFindPrevious, closeFindBar, applyFindQuery, getCurrentFindQuery } from './find.js';
import { initExplorer, refreshTree, renderTree, expandAncestors } from './explorer.js';
import { initChat } from './chat.js';

initTopbar({ currentService: 'korecode', urls: window.__koreSuiteUrls || {} });
initAppBar({
  currentService: 'korecode',
  overline: 'Code Editor',
  brandLabel: 'KoreCode',
  brandIcon: 'korecode',
  editorTabsSlot: 'kc-editor-tabs',
});

const editorApi = createEditor({
  runFind,
  runFindNext,
  runFindPrevious,
  closeFindBar,
  applyFindQuery,
  getCurrentFindQuery,
  renderTree,
  expandAncestors,
  onTabChange: (path) => chat.onTabChange(path),
  onSelectionChange: (text) => chat.onSelectionChange(text),
});
const { editorView, openFile, getActiveTab, renderTabs, renderMeta, restoreTabs } = editorApi;

const chat = initChat({
  getActiveTab,
  getContinueContext: () => editorApi.getContinueContext(),
  insertContinuation: (text) => editorApi.insertContinuation(text),
  insertFromChat: (text) => editorApi.insertTextAtSelection(text),
  getEditorSelection: () => editorApi.getEditorSelection(),
  getCursorInfo: () => editorApi.getCursorInfo(),
});

initFind({ editorView, getActiveTab });
initExplorer({ openFile });
initPanels({
  panelsEl: document.getElementById('code-app'),
  leftEl: document.getElementById('code-sidebar'),
  splitterEl: document.getElementById('code-splitter'),
  minLeft: 160,
  maxLeft: 600,
  storageKey: 'korecode-sidebar-w',
});

void boot();

async function boot() {
  await refreshTree();
  await restoreTabs();
  renderTabs();
  renderMeta();
}