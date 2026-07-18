import { EditorState, Compartment, StateEffect, StateField, RangeSet } from 'https://jspm.dev/@codemirror/state';
import {
  drawSelection,
  dropCursor,
  EditorView,
  Decoration,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightWhitespace,
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
import { state, api, STORAGE_TABS, STORAGE_ACTIVE, STORAGE_DRAFTS, workspaceStorageKey } from './state.js';
import { fileIconForPath } from '/ui-elements/assets/js/icons.js';

const STORAGE_LINE_WRAP = 'korecode:line-wrap';

function _loadDrafts() {
  try {
    const key = workspaceStorageKey(STORAGE_DRAFTS);
    const raw = JSON.parse(localStorage.getItem(key) || localStorage.getItem(STORAGE_DRAFTS) || '{}');
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      return {};
    }
    return raw;
  } catch {
    return {};
  }
}

function _saveDrafts(drafts) {
  try {
    localStorage.setItem(workspaceStorageKey(STORAGE_DRAFTS), JSON.stringify(drafts));
  } catch {
    // Ignore localStorage quota or availability errors.
  }
}

// ── Continuation ghost-text decoration ───────────────────────────────────────
const addContinuationMark    = StateEffect.define();
const clearContinuationMark  = StateEffect.define();

const continuationField = StateField.define({
  create: () => RangeSet.empty,
  update(marks, tr) {
    let next = marks.map(tr.changes);
    for (const effect of tr.effects) {
      if (effect.is(addContinuationMark))   next = effect.value;
      if (effect.is(clearContinuationMark)) next = RangeSet.empty;
    }
    return next;
  },
  provide: (field) => EditorView.decorations.from(field),
});

const continuationDeco = Decoration.mark({ class: 'cm-continuation' });

let _continuationView = null;   // set once the editorView is created

function _applyContinuationMark(from, to) {
  if (!_continuationView || from >= to) return;
  _continuationView.dispatch({
    effects: addContinuationMark.of(RangeSet.of([continuationDeco.range(from, to)])),
  });
}

function _removeContinuationMark() {
  if (!_continuationView) return;
  _continuationView.dispatch({ effects: clearContinuationMark.of(null) });
}

let tabsHost = null;
const breadcrumb = document.getElementById('file-breadcrumb');
const fileState = document.getElementById('file-state');
const editorEmpty = document.getElementById('editor-empty');
const editorHost = document.getElementById('editor-host');
const findButton = document.getElementById('btn-find');
const saveButton = document.getElementById('btn-save');
const wrapButton = document.getElementById('btn-wrap');

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
      userSelect: 'none',
      cursor: 'default',
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

export function createEditor({ runFind, runFindNext, runFindPrevious, closeFindBar, applyFindQuery, getCurrentFindQuery, renderTree, expandAncestors, onTabChange, onSelectionChange = null }) {
  const languageCompartment = new Compartment();
  const editableCompartment = new Compartment();
  const readonlyCompartment = new Compartment();
  const wrapCompartment = new Compartment();
  let suppressEditorSync = false;
  let drafts = {};
  let lineWrapEnabled = localStorage.getItem(STORAGE_LINE_WRAP) === '1';

  function updateWrapButton() {
    if (!wrapButton) return;
    wrapButton.classList.toggle('is-on', lineWrapEnabled);
    wrapButton.classList.toggle('kcui-tag--warning', lineWrapEnabled);
    wrapButton.classList.toggle('kcui-tag--dim', !lineWrapEnabled);
    wrapButton.setAttribute('aria-pressed', lineWrapEnabled ? 'true' : 'false');
    wrapButton.title = lineWrapEnabled ? 'Disable line wrap' : 'Enable line wrap';
  }

  function applyLineWrap(enabled) {
    lineWrapEnabled = Boolean(enabled);
    editorView.dispatch({
      effects: wrapCompartment.reconfigure(lineWrapEnabled ? EditorView.lineWrapping : []),
    });
    try {
      localStorage.setItem(STORAGE_LINE_WRAP, lineWrapEnabled ? '1' : '0');
    } catch {
      // Ignore localStorage availability errors.
    }
    updateWrapButton();
  }

  function setDraft(path, content) {
    if (!path) return;
    drafts[path] = content;
    _saveDrafts(drafts);
  }

  function clearDraft(path) {
    if (!path || !(path in drafts)) return;
    delete drafts[path];
    _saveDrafts(drafts);
  }

  function getActiveTab() {
    return state.openTabs.find((tab) => tab.path === state.activePath) ?? null;
  }

  function getCursorInfo() {
    const sel = editorView.state.selection.main;
    const head = sel.head;
    const line = editorView.state.doc.lineAt(head);
    return {
      line: line.number,
      column: (head - line.from) + 1,
      offset: head,
    };
  }

  function updateSaveButton() {
    const active = getActiveTab();
    const dirty = Boolean(active?.dirty);
    findButton.disabled = !active;
    saveButton.disabled = !active || !dirty;
    saveButton.classList.toggle('kcui-tag--info', dirty);
    saveButton.classList.toggle('kcui-tag--dim', !dirty);
  }

  function persistTabs() {
    const payload = state.openTabs.map((tab) => ({ path: tab.path }));
    localStorage.setItem(workspaceStorageKey(STORAGE_TABS), JSON.stringify(payload));
    if (state.activePath) {
      localStorage.setItem(workspaceStorageKey(STORAGE_ACTIVE), state.activePath);
    } else {
      localStorage.removeItem(workspaceStorageKey(STORAGE_ACTIVE));
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
        void closeTab(tab.path);
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
      fileState.classList.remove('kcui-tag--warning');
      fileState.classList.add('kcui-tag--dim');
      editorEmpty.classList.remove('is-hidden');
      editorHost.classList.remove('is-ready');
      updateSaveButton();
      return;
    }
    breadcrumb.textContent = active.path;
    fileState.textContent = active.dirty ? 'Unsaved changes' : 'Saved';
    fileState.classList.toggle('kcui-tag--warning', Boolean(active.dirty));
    fileState.classList.toggle('kcui-tag--dim', !active.dirty);
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

    if (active?.editorState) {
      // Restore previously saved state — preserves cursor, selection, scroll.
      editorView.setState(active.editorState);
      editorView.dispatch({
        effects: wrapCompartment.reconfigure(lineWrapEnabled ? EditorView.lineWrapping : []),
      });
      suppressEditorSync = false;
      const savedScrollTop = active.scrollTop ?? 0;
      requestAnimationFrame(() => {
        editorView.scrollDOM.scrollTop = savedScrollTop;
      });
    } else {
      editorView.dispatch({
        changes: { from: 0, to: editorView.state.doc.length, insert: active?.content ?? '' },
        effects: [
          languageCompartment.reconfigure(languageForPath(active?.path)),
          editableCompartment.reconfigure(EditorView.editable.of(Boolean(active))),
          readonlyCompartment.reconfigure(EditorState.readOnly.of(!active)),
        ],
      });
      suppressEditorSync = false;
    }

    applyFindQuery(getCurrentFindQuery());
    if (active) {
      editorView.focus();
    }
  }

  function setActiveTab(path) {
    // Save editor state of the outgoing tab so it can be fully restored later.
    const leaving = getActiveTab();
    if (leaving) {
      leaving.editorState = editorView.state;
      leaving.scrollTop = editorView.scrollDOM.scrollTop;
    }
    state.activePath = path;
    applyActiveTabToEditor();
    persistTabs();
    renderTabs();
    renderMeta();
    renderTree();
    onTabChange?.(path);
  }

  async function closeTab(path) {
    const tab = state.openTabs.find((item) => item.path === path);
    if (!tab) {
      return;
    }
    if (tab.dirty) {
      const confirmed = await window.kcuiConfirm(
        'Discard Changes',
        `Discard unsaved changes in ${tab.name}?`,
        { confirmLabel: 'Discard' },
      );
      if (!confirmed) {
        return;
      }
    }
    clearDraft(path);
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

  function resetWorkspaceContext({ persist = true } = {}) {
    state.openTabs = [];
    state.activePath = null;
    if (persist) persistTabs();
    applyActiveTabToEditor();
    renderTabs();
    renderMeta();
    onTabChange?.(null);
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
    const savePayload = {
      content: active.content,
      expected_modified_at: active.modifiedAt ?? null,
      expected_modified_at_ns: active.modifiedAtNs ?? null,
      expected_hash: active.contentHash ?? null,
    };

    let payload;
    try {
      payload = await api(`/api/file?path=${encodeURIComponent(active.path)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(savePayload),
      });
    } catch (err) {
      const message = String(err?.message || err || '').toLowerCase();
      if (!message.includes('file not found')) {
        throw err;
      }

      // If backend root drifted, resync it to the currently displayed explorer root and retry once.
      const rootPathText = document.getElementById('root-select')?.dataset?.currentRoot?.trim()
                        || document.getElementById('root-select')?.value?.trim()
                        || '';
      if (!rootPathText) {
        throw err;
      }
      await api('/api/root', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ root: rootPathText }),
      });
      payload = await api(`/api/file?path=${encodeURIComponent(active.path)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(savePayload),
      });
    }
    active.savedContent = active.content;
    active.modifiedAt = payload.modified_at;
    active.modifiedAtNs = payload.modified_at_ns ?? null;
    active.contentHash = payload.content_hash ?? null;
    active.dirty = false;
    clearDraft(active.path);
    persistTabs();
    renderTabs();
    renderMeta();
    return true;
  }

  async function openFile(path, opts = {}) {
    const activate = opts.activate !== false;
    const existing = state.openTabs.find((tab) => tab.path === path);
    if (existing) {
      if (activate) {
        setActiveTab(path);
      }
      return;
    }
    const payload = await api(`/api/file?path=${encodeURIComponent(path)}`);
    const draftContent = typeof drafts[payload.path] === 'string' ? drafts[payload.path] : null;
    const initialContent = draftContent ?? payload.content;
    const tab = {
      path: payload.path,
      name: payload.name,
      content: initialContent,
      savedContent: payload.content,
      modifiedAt: payload.modified_at,
      modifiedAtNs: payload.modified_at_ns ?? null,
      contentHash: payload.content_hash ?? null,
      dirty: initialContent !== payload.content,
    };
    state.openTabs.push(tab);
    await expandAncestors(path);
    if (activate) {
      setActiveTab(path);
    } else {
      persistTabs();
      renderTabs();
      renderMeta();
      renderTree();
    }
  }

  async function restoreTabs() {
    let savedTabs = [];
    try {
      const key = workspaceStorageKey(STORAGE_TABS);
      savedTabs = JSON.parse(localStorage.getItem(key) || localStorage.getItem(STORAGE_TABS) || '[]');
    } catch {
      savedTabs = [];
    }
    drafts = _loadDrafts();
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
    const desiredActive = localStorage.getItem(workspaceStorageKey(STORAGE_ACTIVE)) || localStorage.getItem(STORAGE_ACTIVE);
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
        continuationField,
        lineNumbers({
          domEventHandlers: {
            mousedown(view, line, event) {
              if (event.button !== 0) return false;
              const clickedLine = view.state.doc.lineAt(line.from);

              if (event.shiftKey) {
                // Extend selection from existing anchor.
                const curAnchor = view.state.selection.main.anchor;
                const anchorLine = view.state.doc.lineAt(curAnchor);
                const anchor = anchorLine.from;
                const head = clickedLine.number >= anchorLine.number
                  ? clickedLine.to
                  : clickedLine.from;
                view.dispatch({ selection: { anchor, head }, scrollIntoView: false });
                view.focus();
                event.preventDefault();
                return true;
              }

              const anchor = clickedLine.from;
              view.dispatch({ selection: { anchor, head: clickedLine.to }, scrollIntoView: false });
              view.focus();

              // Drag across line numbers selects whole lines.
              const onMove = (e) => {
                const rect = view.scrollDOM.getBoundingClientRect();
                const docY = e.clientY - rect.top + view.scrollDOM.scrollTop;
                const block = view.lineBlockAtHeight(Math.max(0, docY));
                const moveLine = view.state.doc.lineAt(block.from);
                const head = moveLine.number >= clickedLine.number
                  ? moveLine.to
                  : moveLine.from;
                view.dispatch({ selection: { anchor, head }, scrollIntoView: false });
              };
              const onUp = () => {
                window.removeEventListener('mousemove', onMove);
                window.removeEventListener('mouseup', onUp);
              };
              window.addEventListener('mousemove', onMove);
              window.addEventListener('mouseup', onUp);

              event.preventDefault();
              return true;
            },
          },
        }),
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
        highlightWhitespace(),
        syntaxHighlighting(codeHighlightStyle, { fallback: true }),
        languageCompartment.of([]),
        editableCompartment.of(EditorView.editable.of(false)),
        readonlyCompartment.of(EditorState.readOnly.of(true)),
        wrapCompartment.of(lineWrapEnabled ? EditorView.lineWrapping : []),
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
          if (update.selectionSet) {
            const sel = update.state.selection.main;
            const selText = sel.empty ? null : update.state.doc.sliceString(sel.from, sel.to);
            const head = sel.head;
            const line = update.state.doc.lineAt(head);
            onSelectionChange?.({
              text: selText,
              line: line.number,
              column: (head - line.from) + 1,
              offset: head,
            });
          }
          if (!update.docChanged || suppressEditorSync) {
            return;
          }
          const activeTab = getActiveTab();
          if (!activeTab) {
            return;
          }
          activeTab.content = update.state.doc.toString();
          activeTab.dirty = activeTab.content !== activeTab.savedContent;
          if (activeTab.dirty) setDraft(activeTab.path, activeTab.content);
          else clearDraft(activeTab.path);
          persistTabs();
          renderTabs();
          renderMeta();
        }),
      ],
    }),
    parent: editorHost,
  });

  _continuationView = editorView;   // expose to module-level helpers

  saveButton.addEventListener('click', () => {
    void saveActiveTab().catch((err) => {
      fileState.textContent = `Save failed: ${err?.message || err}`;
      fileState.classList.add('kcui-tag--warning');
      fileState.classList.remove('kcui-tag--dim');
    });
  });

  wrapButton?.addEventListener('click', () => {
    applyLineWrap(!lineWrapEnabled);
  });
  updateWrapButton();

  function _replaceLines(content, fromLine, toLine, replacement) {
    const lines = content.split('\n');
    const from = Math.max(1, Number(fromLine || 1));
    const to = Math.max(from, Number(toLine || from));
    const before = lines.slice(0, from - 1);
    const after = lines.slice(to);
    const middle = String(replacement ?? '').split('\n');
    return [...before, ...middle, ...after].join('\n');
  }

  async function applyStructuredEdits(edits) {
    if (!Array.isArray(edits) || edits.length === 0) {
      return { ok: false, applied: 0, errors: ['No edits provided.'] };
    }
    const errors = [];
    let applied = 0;
    const touchedPaths = new Set();

    for (const edit of edits) {
      const path = String(edit?.file || '').trim();
      if (!path) {
        errors.push('Edit missing file path.');
        continue;
      }
      try {
        let tab = state.openTabs.find((item) => item.path === path);
        if (!tab) {
          try {
            await openFile(path, { activate: false });
          } catch {
            // If the target file does not exist yet, create it and reopen.
            await api(`/api/file?path=${encodeURIComponent(path)}`, { method: 'POST' });
            await openFile(path, { activate: false });
          }
          tab = state.openTabs.find((item) => item.path === path);
        }
        if (!tab) {
          errors.push(`Could not open ${path}.`);
          continue;
        }
        const from = Number(edit.from || 1);
        const to = Number(edit.to || from);
        const replacement = String(edit.replacement ?? '');
        tab.content = _replaceLines(tab.content, from, to, replacement);
        tab.dirty = tab.content !== tab.savedContent;
        if (tab.dirty) setDraft(tab.path, tab.content);
        else clearDraft(tab.path);
        touchedPaths.add(tab.path);
        applied += 1;
      } catch (err) {
        errors.push(`Failed to apply edit for ${path}: ${err?.message || err}`);
      }
    }

    const active = getActiveTab();
    if (active && touchedPaths.has(active.path)) {
      suppressEditorSync = true;
      editorView.dispatch({
        changes: { from: 0, to: editorView.state.doc.length, insert: active.content },
      });
      suppressEditorSync = false;
      editorView.focus();
    }

    persistTabs();
    renderTabs();
    renderMeta();
    state.tree.clear();
    renderTree();

    return { ok: errors.length === 0, applied, errors };
  }

  async function saveTabs(paths = null) {
    const targets = Array.isArray(paths) && paths.length
      ? state.openTabs.filter((tab) => paths.includes(tab.path))
      : state.openTabs.filter((tab) => tab.dirty);
    const previousActivePath = state.activePath;
    const errors = [];
    let saved = 0;

    for (const tab of targets) {
      try {
        if (state.activePath !== tab.path) {
          setActiveTab(tab.path);
        }
        const ok = await saveActiveTab();
        if (ok) saved += 1;
      } catch (err) {
        errors.push(`${tab.path}: ${err?.message || err}`);
      }
    }

    if (previousActivePath && state.activePath !== previousActivePath && state.openTabs.some((tab) => tab.path === previousActivePath)) {
      setActiveTab(previousActivePath);
    }

    return { ok: errors.length === 0, saved, errors };
  }

  async function reloadTabs(paths = []) {
    const targets = Array.isArray(paths) && paths.length
      ? state.openTabs.filter((tab) => paths.includes(tab.path))
      : state.openTabs.slice();
    const errors = [];
    const previousActivePath = state.activePath;

    for (const tab of targets) {
      try {
        const payload = await api(`/api/file?path=${encodeURIComponent(tab.path)}`);
        tab.content      = payload.content;
        tab.savedContent = payload.content;
        tab.modifiedAt   = payload.modified_at;
        tab.modifiedAtNs = payload.modified_at_ns ?? null;
        tab.contentHash  = payload.content_hash ?? null;
        tab.dirty        = false;
        clearDraft(tab.path);
      } catch (err) {
        errors.push(`${tab.path}: ${err?.message || err}`);
      }
    }

    if (previousActivePath && state.openTabs.some((tab) => tab.path === previousActivePath)) {
      state.activePath = previousActivePath;
      applyActiveTabToEditor();
    }

    persistTabs();
    renderTabs();
    renderMeta();
    renderTree();
    return { ok: errors.length === 0, reloaded: targets.length - errors.length, errors };
  }

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
    applyStructuredEdits,
    saveTabs,
    reloadTabs,
    resetWorkspaceContext,

    getContinueContext() {
      const tab = getActiveTab();
      if (!tab) return null;
      const doc    = editorView.state.doc;
      const cursor = editorView.state.selection.main.head;
      const line   = doc.lineAt(cursor);
      // Prefix: up to 120 lines ending at cursor.
      const fromLine = Math.max(1, line.number - 119);
      const from     = doc.line(fromLine).from;
      // Suffix: up to 40 lines starting after cursor.
      const toLine = Math.min(doc.lines, line.number + 40);
      const to     = doc.line(toLine).to;
      return {
        path:   tab.path,
        text:   doc.sliceString(from, cursor),
        suffix: doc.sliceString(cursor, to),
        offset: cursor,
      };
    },

    getEditorSelection() {
      const sel = editorView.state.selection.main;
      if (sel.empty) return null;
      return editorView.state.doc.sliceString(sel.from, sel.to);
    },

    getCursorInfo,

    insertTextAtSelection(text) {
      const activeTab = getActiveTab();
      if (!activeTab || typeof text !== 'string' || !text.length) {
        return false;
      }
      const sel = editorView.state.selection.main;
      const from = sel.from;
      editorView.dispatch({
        changes: { from: sel.from, to: sel.to, insert: text },
        selection: { anchor: from + text.length },
      });
      editorView.focus();
      return true;
    },

    /**
     * Inserts `text` at the current cursor position as a ghost/preview, styled
     * with the `cm-continuation` class. Returns a controller object:
     *   { accept(), cancel() }
     * accept() commits the text as a real edit; cancel() removes the ghost.
     * Any keydown other than Tab calls cancel() automatically.
     */
    insertContinuation(text, options = {}) {
      const targetPath = typeof options.path === 'string' ? options.path : null;
      if (targetPath && state.activePath !== targetPath) {
        const targetTab = state.openTabs.find((tab) => tab.path === targetPath);
        if (targetTab) {
          setActiveTab(targetPath);
        }
      }

      const offset = typeof options.offset === 'number'
        ? Math.max(0, Math.min(editorView.state.doc.length, options.offset))
        : editorView.state.selection.main.head;

      suppressEditorSync = true;
      editorView.dispatch({
        changes: { from: offset, insert: text },
        selection: { anchor: offset },    // keep cursor before inserted text
      });
      suppressEditorSync = false;

      const insertedFrom = offset;
      const insertedTo   = offset + text.length;

      let done = false;

      function accept() {
        if (done) return;
        done = true;
        cleanup();
        // Move cursor to end of inserted text.
        editorView.dispatch({ selection: { anchor: insertedTo } });
        // Commit into the active tab content.
        const activeTab = getActiveTab();
        if (activeTab) {
          activeTab.content = editorView.state.doc.toString();
          activeTab.dirty   = activeTab.content !== activeTab.savedContent;
          if (activeTab.dirty) setDraft(activeTab.path, activeTab.content);
          else clearDraft(activeTab.path);
          persistTabs();
          renderTabs();
          renderMeta();
        }
        editorView.focus();
      }

      function cancel() {
        if (done) return;
        done = true;
        cleanup();
        suppressEditorSync = true;
        editorView.dispatch({ changes: { from: insertedFrom, to: insertedTo, insert: '' } });
        suppressEditorSync = false;
        editorView.focus();
      }

      // Mutable controller — chat.js may replace .accept/.cancel after creation.
      const ctl = { accept, cancel };

      function onKey(e) {
        // Any regular keypress while ghost text is visible cancels the preview
        // so the user doesn't accidentally type alongside it.
        if (!e.ctrlKey && !e.altKey && !e.metaKey) {
          ctl.cancel();
        }
      }

      function onMouseDown() { ctl.cancel(); }

      function cleanup() {
        editorView.dom.removeEventListener('keydown', onKey, true);
        editorView.dom.removeEventListener('mousedown', onMouseDown, true);
        _removeContinuationMark(insertedFrom, insertedTo);
      }

      editorView.dom.addEventListener('keydown', onKey, true);
      editorView.dom.addEventListener('mousedown', onMouseDown, true);

      // Mark the ghost text visually.
      _applyContinuationMark(insertedFrom, insertedTo);

      return ctl;
    },
  };
}
