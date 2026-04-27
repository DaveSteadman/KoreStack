/**
 * toolbar.js — ProseMirror toolbar commands for KoreDoc.
 */

import { toggleMark, setBlockType }         from 'https://esm.sh/prosemirror-commands@1';
import { wrapInList, liftListItem }         from 'https://esm.sh/prosemirror-schema-list@1';
import { schema, getView, indentBlock, outdentBlock } from './editor.js';

export function init() {
  document.getElementById('toolbar').addEventListener('mousedown', e => {
    const btn = e.target.closest('[data-insert]');
    if (!btn) return;
    e.preventDefault();
    insert(btn.dataset.insert);
  });
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
