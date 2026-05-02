import { EditorState, Compartment } from 'https://jspm.dev/@codemirror/state';
import {
  drawSelection,
  dropCursor,
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from 'https://jspm.dev/@codemirror/view';
import {
  bracketMatching,
  foldGutter,
  foldKeymap,
  HighlightStyle,
  indentOnInput,
  syntaxHighlighting,
} from 'https://jspm.dev/@codemirror/language';
import { tags as t } from 'https://jspm.dev/@lezer/highlight';
import { history, historyKeymap, indentWithTab, defaultKeymap } from 'https://jspm.dev/@codemirror/commands';
import { highlightSelectionMatches } from 'https://jspm.dev/@codemirror/search';
import { autocompletion, closeBrackets, closeBracketsKeymap } from 'https://jspm.dev/@codemirror/autocomplete';
import { python } from 'https://jspm.dev/@codemirror/lang-python';
import { javascript } from 'https://jspm.dev/@codemirror/lang-javascript';
import { json } from 'https://jspm.dev/@codemirror/lang-json';
import { markdown } from 'https://jspm.dev/@codemirror/lang-markdown';
import { html } from 'https://jspm.dev/@codemirror/lang-html';
import { css } from 'https://jspm.dev/@codemirror/lang-css';
import { state, api, STORAGE_TABS, STORAGE_ACTIVE } from './state.js';
import { fileIconForPath } from '/ui-elements/assets/js/icons.js';

let tabsHost = null;
const breadcrumb = document.getElementById('file-breadcrumb');
const fileState = document.getElementById('file-state');
const editorEmpty = document.getElementById('editor-empty');
const editorHost = document.getElementById('editor-host');
const findButton = document.getElementById('btn-find');
const saveButton = document.getElementById('btn-save');

const editorTheme = EditorView.theme(
  {
    '&': {
      height: '100%',
      color: 'var(--text)',
      backgroundColor: 'transparent',
    },
    '.cm-gutters': {
      backgroundColor: 'color-mix(in srgb, var(--bg-2) 90%, black 10%)',
      color: 'var(--text-dim)',
      borderRight: '1px solid var(--border)',
    },
    '.cm-activeLine': {
      backgroundColor: 'color-mix(in srgb, var(--accent) 10%, transparent)',
    },
    '.cm-activeLineGutter': {
      backgroundColor: 'color-mix(in srgb, var(--accent) 14%, transparent)',
      color: 'var(--text)',
    },
    '.cm-selectionBackground, ::selection': {
      backgroundColor: 'color-mix(in srgb, var(--accent) 28%, transparent)',
    },
    '.cm-cursor': {
      borderLeftColor: 'var(--accent-2)',
    },
    '.cm-foldPlaceholder': {
      backgroundColor: 'var(--surface-2)',
      borderColor: 'var(--border)',
      color: 'var(--text-2)',
    },
  },
  { dark: true },
);

const codeHighlightStyle = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.definitionKeyword, t.moduleKeyword], color: '#c586c0' },
  { tag: [t.operatorKeyword, t.modifier], color: '#c586c0' },
  { tag: [t.name, t.deleted, t.character, t.macroName], color: '#9cdcfe' },
  { tag: [t.propertyName], color: '#dcdcaa' },
  { tag: [t.variableName, t.self, t.className, t.namespace], color: '#4ec9b0' },
  { tag: [t.labelName, t.typeName], color: '#4ec9b0' },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: '#d7aefb' },
  { tag: [t.special(t.variableName)], color: '#d7aefb' },
  { tag: [t.string, t.special(t.string)], color: '#ce9178' },
  { tag: [t.number, t.integer, t.float, t.bool, t.null], color: '#b5cea8' },
  { tag: [t.comment, t.lineComment, t.blockComment, t.docComment], color: '#6a9955', fontStyle: 'italic' },
  { tag: [t.regexp, t.escape], color: '#d7ba7d' },
  { tag: [t.operator, t.punctuation, t.separator, t.bracket], color: '#d4d4d4' },
  { tag: [t.url, t.attributeName], color: '#9cdcfe' },
  { tag: [t.heading], color: '#4fc1ff', fontWeight: '700' },
  { tag: [t.emphasis], fontStyle: 'italic', color: '#d4d4d4' },
  { tag: [t.strong], fontWeight: '700', color: '#d4d4d4' },
]);

export function createEditor({ runFind, runFindNext, runFindPrevious, closeFindBar, applyFindQuery, getCurrentFindQuery, renderTree, expandAncestors }) {
  const languageCompartment = new Compartment();
  const editableCompartment = new Compartment();
  const readonlyCompartment = new Compartment();
  let suppressEditorSync = false;

  function getActiveTab() {
    return state.openTabs.find((tab) => tab.path === state.activePath) ?? null;
  }

  function updateSaveButton() {
    const active = getActiveTab();
    findButton.disabled = !active;
    saveButton.disabled = !active || !active.dirty;
  }

  function persistTabs() {
    const payload = state.openTabs.map((tab) => ({ path: tab.path }));
    localStorage.setItem(STORAGE_TABS, JSON.stringify(payload));
    if (state.activePath) {
      localStorage.setItem(STORAGE_ACTIVE, state.activePath);
    } else {
      localStorage.removeItem(STORAGE_ACTIVE);
    }
  }

  function renderTabs() {
    tabsHost ??= document.getElementById('kc-editor-tabs');
    if (!tabsHost) { updateSaveButton(); return; }
    tabsHost.innerHTML = '';
    if (!state.openTabs.length) {
      updateSaveButton();
      return;
    }
    for (const tab of state.openTabs) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'kappbar-editortab';
      if (tab.path === state.activePath) {
        button.classList.add('is-active');
      }
      button.title = tab.path;
      const iconHtml = `<span class="kappbar-editortab-icon" aria-hidden="true">${fileIconForPath(tab.path, 13)}</span>`;
      const dirtyHtml = tab.dirty ? '<span class="kappbar-editortab-dirty" aria-label="Unsaved">\u25cf</span>' : '';
      button.innerHTML = `${iconHtml}<span class="kappbar-editortab-name"></span>${dirtyHtml}<button type="button" class="kappbar-editortab-close" aria-label="Close file">\u00d7</button>`;
      button.querySelector('.kappbar-editortab-name').textContent = tab.name;
      button.addEventListener('click', (event) => {
        if (event.target.closest('.kappbar-editortab-close')) {
          return;
        }
        setActiveTab(tab.path);
      });
      button.querySelector('.kappbar-editortab-close').addEventListener('click', (event) => {
        event.stopPropagation();
        closeTab(tab.path);
      });
      tabsHost.appendChild(button);
    }
    updateSaveButton();
  }

  function renderMeta() {
    const active = getActiveTab();
    if (!active) {
      breadcrumb.textContent = 'No file open';
      fileState.textContent = 'Editor unavailable';
      editorEmpty.classList.remove('is-hidden');
      editorHost.classList.remove('is-ready');
      updateSaveButton();
      return;
    }
    breadcrumb.textContent = active.path;
    fileState.textContent = active.dirty ? 'Unsaved changes' : 'Saved';
    editorEmpty.classList.add('is-hidden');
    editorHost.classList.add('is-ready');
    updateSaveButton();
  }

  function languageForPath(path) {
    if (!path) {
      return [];
    }
    const lower = path.toLowerCase();
    if (lower.endsWith('.py') || lower.endsWith('.pyi')) {
      return python();
    }
    if (lower.endsWith('.js')) {
      return javascript();
    }
    if (lower.endsWith('.json')) {
      return json();
    }
    if (lower.endsWith('.md')) {
      return markdown();
    }
    if (lower.endsWith('.html')) {
      return html();
    }
    if (lower.endsWith('.css')) {
      return css();
    }
    return [];
  }

  function applyActiveTabToEditor() {
    const active = getActiveTab();
    suppressEditorSync = true;
    editorView.dispatch({
      changes: { from: 0, to: editorView.state.doc.length, insert: active?.content ?? '' },
      effects: [
        languageCompartment.reconfigure(languageForPath(active?.path)),
        editableCompartment.reconfigure(EditorView.editable.of(Boolean(active))),
        readonlyCompartment.reconfigure(EditorState.readOnly.of(!active)),
      ],
    });
    suppressEditorSync = false;
    applyFindQuery(getCurrentFindQuery());
    if (active) {
      editorView.focus();
    }
  }

  function setActiveTab(path) {
    state.activePath = path;
    applyActiveTabToEditor();
    persistTabs();
    renderTabs();
    renderMeta();
    renderTree();
  }

  function closeTab(path) {
    const tab = state.openTabs.find((item) => item.path === path);
    if (!tab) {
      return;
    }
    if (tab.dirty && !window.confirm(`Discard unsaved changes in ${tab.name}?`)) {
      return;
    }
    state.openTabs = state.openTabs.filter((item) => item.path !== path);
    if (state.activePath === path) {
      const next = state.openTabs[state.openTabs.length - 1] || null;
      state.activePath = next?.path ?? null;
      applyActiveTabToEditor();
    }
    persistTabs();
    renderTabs();
    renderMeta();
    renderTree();
  }

  async function saveActiveTab() {
    const active = getActiveTab();
    if (!active) {
      return false;
    }
    if (!active.dirty) {
      return true;
    }
    fileState.textContent = 'Saving\u2026';
    const payload = await api(`/api/file?path=${encodeURIComponent(active.path)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: active.content }),
    });
    active.savedContent = active.content;
    active.modifiedAt = payload.modified_at;
    active.dirty = false;
    persistTabs();
    renderTabs();
    renderMeta();
    return true;
  }

  async function openFile(path) {
    const existing = state.openTabs.find((tab) => tab.path === path);
    if (existing) {
      setActiveTab(path);
      return;
    }
    const treeStatus = document.getElementById('tree-status');
    treeStatus.textContent = `Opening ${path}\u2026`;
    const payload = await api(`/api/file?path=${encodeURIComponent(path)}`);
    const tab = {
      path: payload.path,
      name: payload.name,
      content: payload.content,
      savedContent: payload.content,
      modifiedAt: payload.modified_at,
      dirty: false,
    };
    state.openTabs.push(tab);
    await expandAncestors(path);
    setActiveTab(path);
    treeStatus.textContent = `Opened ${path}`;
  }

  async function restoreTabs() {
    let savedTabs = [];
    try {
      savedTabs = JSON.parse(localStorage.getItem(STORAGE_TABS) || '[]');
    } catch {
      savedTabs = [];
    }
    for (const entry of savedTabs.slice(0, 8)) {
      if (!entry?.path) {
        continue;
      }
      try {
        await openFile(entry.path);
      } catch {
        continue;
      }
    }
    const desiredActive = localStorage.getItem(STORAGE_ACTIVE);
    if (desiredActive && state.openTabs.some((tab) => tab.path === desiredActive)) {
      setActiveTab(desiredActive);
      return;
    }
    if (state.openTabs[0]) {
      setActiveTab(state.openTabs[0].path);
    }
  }

  const editorView = new EditorView({
    state: EditorState.create({
      doc: '',
      extensions: [
        lineNumbers(),
        highlightActiveLineGutter(),
        history(),
        foldGutter(),
        drawSelection(),
        dropCursor(),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        autocompletion(),
        highlightActiveLine(),
        highlightSelectionMatches(),
        syntaxHighlighting(codeHighlightStyle, { fallback: true }),
        languageCompartment.of([]),
        editableCompartment.of(EditorView.editable.of(false)),
        readonlyCompartment.of(EditorState.readOnly.of(true)),
        keymap.of([
          { key: 'Mod-s', run: () => { void saveActiveTab(); return true; } },
          { key: 'Mod-f', run: () => runFind() },
          { key: 'F3', run: () => { runFindNext(); return true; } },
          { key: 'Shift-F3', run: () => { runFindPrevious(); return true; } },
          {
            key: 'Escape',
            run: () => {
              const findBar = document.getElementById('editor-findbar');
              if (findBar.hidden) {
                return false;
              }
              closeFindBar();
              return true;
            },
          },
          indentWithTab,
          ...closeBracketsKeymap,
          ...defaultKeymap,
          ...historyKeymap,
          ...foldKeymap,
        ]),
        editorTheme,
        EditorView.updateListener.of((update) => {
          if (!update.docChanged || suppressEditorSync) {
            return;
          }
          const activeTab = getActiveTab();
          if (!activeTab) {
            return;
          }
          activeTab.content = update.state.doc.toString();
          activeTab.dirty = activeTab.content !== activeTab.savedContent;
          persistTabs();
          renderTabs();
          renderMeta();
        }),
      ],
    }),
    parent: editorHost,
  });

  saveButton.addEventListener('click', () => {
    void saveActiveTab();
  });

  return {
    editorView,
    openFile,
    getActiveTab,
    setActiveTab,
    renderTabs,
    renderMeta,
    saveActiveTab,
    restoreTabs,
    updateSaveButton,
  };
}
