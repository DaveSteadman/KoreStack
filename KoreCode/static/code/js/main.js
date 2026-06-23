import { initAppBar, initTopbar } from '/ui-elements/assets/js/chrome.js';
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

window.__kcApplyStructuredEdits = async (edits) => editorApi.applyStructuredEdits(edits);
window.__kcSaveTabs = async (paths) => editorApi.saveTabs(paths);
window.__kcReloadTabs = async (paths) => editorApi.reloadTabs(paths);

const chat = initChat({
  getActiveTab,
  getContinueContext: () => editorApi.getContinueContext(),
  insertContinuation: (text) => editorApi.insertContinuation(text),
  insertFromChat: (text) => editorApi.insertTextAtSelection(text),
  getEditorSelection: () => editorApi.getEditorSelection(),
  getCursorInfo: () => editorApi.getCursorInfo(),
});

initFind({ editorView, getActiveTab });
initExplorer({
  openFile,
  onRootChanged: () => {
    editorApi.resetWorkspaceContext();
    void chat.handleWorkspaceRootChanged();
  },
});
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
