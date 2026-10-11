/**
 * toolbar.js — ProseMirror toolbar commands for KoreDoc.
 */

import { toggleMark, setBlockType }         from 'https://esm.sh/prosemirror-commands@1';
import { wrapInList, liftListItem }         from 'https://esm.sh/prosemirror-schema-list@1';
import { schema, getView, indentBlock, outdentBlock } from './editor.js?v=20261011b';

export function init() {
  document.getElementById('toolbar').addEventListener('mousedown', e => {
    const btn = e.target.closest('[data-insert]');
    if (!btn) return;
    e.preventDefault();
    insert(btn.dataset.insert);
  });
  document.addEventListener('kd:editor-state-change', e => _syncActiveControls(e.detail.state));
}

export function insert(type) {
  const view = getView();
  if (!view) return;
  const run = cmd => cmd(view.state, view.dispatch, view);
  view.focus();

  const { heading, paragraph, code_block,
          bullet_list, ordered_list, list_item,
          horizontal_rule } = schema.nodes;
  const { strong, em, code } = schema.marks;
  const { $from } = view.state.selection;

  switch (type) {
    case 'h1':
    case 'h2':
    case 'h3': {
      const lvl     = parseInt(type[1]);
      const already = $from.parent.type === heading && $from.parent.attrs.level === lvl;
      run(setBlockType(already ? paragraph : heading, already ? {} : { level: lvl }));
      break;
    }
    case 'bold':   run(toggleMark(strong)); break;
    case 'italic': run(toggleMark(em));     break;
    case 'code':   run(toggleMark(code));   break;
    case 'code-block': {
      const already = $from.parent.type === code_block;
      run(setBlockType(already ? paragraph : code_block));
      break;
    }
    case 'ul': {
      const inUl = _inList(view.state, bullet_list);
      run(inUl ? liftListItem(list_item) : wrapInList(bullet_list));
      break;
    }
    case 'ol': {
      const inOl = _inList(view.state, ordered_list);
      run(inOl ? liftListItem(list_item) : wrapInList(ordered_list));
      break;
    }
    case 'hr': {
      view.dispatch(view.state.tr.replaceSelectionWith(horizontal_rule.create()));
      break;
    }
    case 'indent':  run(indentBlock);  break;
    case 'outdent': run(outdentBlock); break;
  }
}

function _inList(state, listType) {
  const { $from } = state.selection;
  for (let d = $from.depth; d > 0; d--) {
    if ($from.node(d).type === listType) return true;
  }
  return false;
}

function _syncActiveControls(state) {
  const { $from } = state.selection;
  const { heading, code_block, bullet_list, ordered_list } = schema.nodes;
  const marks = state.storedMarks || $from.marks();
  const active = {
    h1: $from.parent.type === heading && $from.parent.attrs.level === 1,
    h2: $from.parent.type === heading && $from.parent.attrs.level === 2,
    h3: $from.parent.type === heading && $from.parent.attrs.level === 3,
    bold: marks.some(mark => mark.type === schema.marks.strong),
    italic: marks.some(mark => mark.type === schema.marks.em),
    code: marks.some(mark => mark.type === schema.marks.code),
    'code-block': $from.parent.type === code_block,
    ul: _inList(state, bullet_list),
    ol: _inList(state, ordered_list),
  };

  document.querySelectorAll('[data-insert]').forEach(button => {
    const isActive = Boolean(active[button.dataset.insert]);
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-pressed', String(isActive));
  });
}
