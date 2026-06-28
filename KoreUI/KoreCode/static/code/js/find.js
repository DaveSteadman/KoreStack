import { SearchQuery, setSearchQuery, findNext, findPrevious, closeSearchPanel } from 'https://jspm.dev/@codemirror/search';

const findBar = document.getElementById('editor-findbar');
const findInput = document.getElementById('find-input');
const findPrevButton = document.getElementById('btn-find-prev');
const findNextButton = document.getElementById('btn-find-next');
const findCloseButton = document.getElementById('btn-find-close');
const findButton = document.getElementById('btn-find');

let currentFindQuery = '';
let _editorView = null;
let _getActiveTab = null;

export function initFind({ editorView, getActiveTab }) {
  _editorView = editorView;
  _getActiveTab = getActiveTab;

  findButton.addEventListener('click', () => {
    runFind();
  });
  findInput.addEventListener('input', () => {
    currentFindQuery = findInput.value;
    applyFindQuery(findInput.value);
  });
  findInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (event.shiftKey) {
        runFindPrevious();
        return;
      }
      runFindNext();
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      closeFindBar();
    }
  });
  findPrevButton.addEventListener('click', () => {
    runFindPrevious();
  });
  findNextButton.addEventListener('click', () => {
    runFindNext();
  });
  findCloseButton.addEventListener('click', () => {
    closeFindBar();
  });
}

export function getCurrentFindQuery() {
  return currentFindQuery;
}

export function runFind() {
  if (!_getActiveTab()) {
    return false;
  }
  if (!findBar.hidden) {
    closeFindBar();
    return true;
  }
  findBar.hidden = false;
  findInput.value = currentFindQuery;
  findInput.focus();
  findInput.select();
  return true;
}

export function closeFindBar() {
  closeSearchPanel(_editorView);
  findBar.hidden = true;
  _editorView.focus();
}

export function applyFindQuery(value) {
  const query = new SearchQuery({
    search: value,
    caseSensitive: false,
    literal: true,
    regexp: false,
    wholeWord: false,
  });
  _editorView.dispatch({
    effects: setSearchQuery.of(query),
  });
}

export function runFindNext() {
  const query = findInput.value;
  if (!query) {
    closeSearchPanel(_editorView);
    findInput.focus();
    return;
  }
  applyFindQuery(query);
  findNext(_editorView);
  closeSearchPanel(_editorView);
}

export function runFindPrevious() {
  const query = findInput.value;
  if (!query) {
    closeSearchPanel(_editorView);
    findInput.focus();
    return;
  }
  applyFindQuery(query);
  findPrevious(_editorView);
  closeSearchPanel(_editorView);
}
