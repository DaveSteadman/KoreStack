/**
 * editor.js - ProseMirror WYSIWYG editor for KoreDoc.
 *
 * Stores the document as ProseMirror state; serialises to/from Markdown.
 * YAML frontmatter is stashed separately so ProseMirror only sees the body.
 */

import { Schema }        from 'https://esm.sh/prosemirror-model@1';
import { EditorState }   from 'https://esm.sh/prosemirror-state@1';
import { EditorView }    from 'https://esm.sh/prosemirror-view@1';
import { history, undo, redo } from 'https://esm.sh/prosemirror-history@1';
import { keymap }        from 'https://esm.sh/prosemirror-keymap@1';
import {
  baseKeymap, toggleMark, setBlockType, selectAll,
  chainCommands, newlineInCode, createParagraphNear,
  liftEmptyBlock, splitBlockKeepMarks,
} from 'https://esm.sh/prosemirror-commands@1';
import { wrapInList, splitListItem, liftListItem, sinkListItem }
  from 'https://esm.sh/prosemirror-schema-list@1';
import {
  MarkdownParser,
  schema as mdSchema,
  defaultMarkdownParser,
  defaultMarkdownSerializer,
} from 'https://esm.sh/prosemirror-markdown@1';

// ── Custom schema: extend paragraph with an indent attribute ───────────────
const _nodes = mdSchema.spec.nodes.update('paragraph', {
  attrs: { indent: { default: 0 } },
  content: 'inline*',
  group: 'block',
  parseDOM: [{
    tag: 'p',
    getAttrs: dom => ({ indent: parseInt(dom.dataset.indent || '0', 10) }),
  }],
  toDOM(node) {
    const { indent } = node.attrs;
    return ['p', indent ? { 'data-indent': String(indent) } : {}, 0];
  },
});
export const schema = new Schema({ nodes: _nodes, marks: mdSchema.spec.marks });

// Reuse the same markdown-it tokenizer and token map; swap in our schema.
const _parser = new MarkdownParser(
  schema,
  defaultMarkdownParser.tokenizer,
  defaultMarkdownParser.tokens,
);

let _view     = null;
let _onUpdate = null;
let _fm       = '';   // stashed YAML frontmatter (not part of PM doc)

// ── Public API ─────────────────────────────────────────────────────────────

export function getView() { return _view; }

/**
 * Initialise ProseMirror in hostEl.
 * @param {Element}  hostEl    Container element for the editor.
 * @param {Function} onUpdate  Called with the full markdown on every doc change.
 */
export function init(hostEl, onUpdate) {
  _onUpdate = onUpdate;
  _view = new EditorView(hostEl, {
    state: EditorState.create({ schema, plugins: _plugins() }),
    dispatchTransaction(tr) {
      const next = _view.state.apply(tr);
      _view.updateState(next);
      if (tr.docChanged) _onUpdate?.(getValue());
    },
  });
}

/** Return the full document as Markdown (frontmatter + body). */
export function getValue() {
  if (!_view) return _fm;
  const body = defaultMarkdownSerializer.serialize(_view.state.doc).trimEnd();
  return _fm ? `${_fm}\n\n${body}` : body;
}

/**
 * Replace editor content from a Markdown string.
 * Strips and stashes YAML frontmatter; resets undo history.
 */
export function setValue(markdown) {
  if (!_view) return;
  const { fmText, bodyStart } = _splitFm(markdown);
  _fm = fmText;
  const body = markdown.slice(bodyStart).trim();
  const doc  = body
    ? _parser.parse(body)
    : schema.topNodeType.createAndFill();
  _view.updateState(EditorState.create({ schema, doc, plugins: _view.state.plugins }));
}

export function doUndo()      { _view && undo(_view.state, _view.dispatch); }
export function doRedo()      { _view && redo(_view.state, _view.dispatch); }
export function doSelectAll() { _view && selectAll(_view.state, _view.dispatch); }

/** Scroll to the Nth heading (0-based) in the document. */
export function scrollToHeading(idx) {
  if (!_view) return;
  let n = 0, pos = null;
  _view.state.doc.forEach((node, offset) => {
    if (pos !== null) return;
    if (node.type === schema.nodes.heading && n++ === idx) pos = offset + 1;
  });
  if (pos == null) return;
  const area   = _view.dom.parentElement;
  const coords = _view.coordsAtPos(pos);
  area.scrollTop += coords.top - area.getBoundingClientRect().top - 80;
  _view.focus();
}

/**
 * Parse YAML frontmatter from a markdown string.
 * Returns { meta: Object, bodyStart: number }.
 * Used by properties.js.
 */
export function parseFrontmatter(text) {
  const m = text.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (!m) return { meta: {}, bodyStart: 0 };
  const meta = {};
  for (const line of m[1].split('\n')) {
    const kv = line.match(/^(\w[\w-]*):\s*(.+)/);
    if (kv) meta[kv[1]] = kv[2].trim();
  }
  return { meta, bodyStart: m[0].length };
}

// ── Indent commands ────────────────────────────────────────────────────────

export function indentBlock(state, dispatch) {
  return _shiftIndent(state, dispatch, +1);
}

export function outdentBlock(state, dispatch) {
  return _shiftIndent(state, dispatch, -1);
}

function _shiftIndent(state, dispatch, delta) {
  const { $from, $to } = state.selection;
  const para = schema.nodes.paragraph;
  let tr = state.tr;
  let changed = false;
  state.doc.nodesBetween($from.pos, $to.pos, (node, pos) => {
    if (node.type !== para) return true;
    const next = Math.max(0, Math.min(8, (node.attrs.indent || 0) + delta));
    if (next === (node.attrs.indent || 0)) return false;
    tr = tr.setNodeMarkup(pos, null, { ...node.attrs, indent: next });
    changed = true;
    return false;
  });
  if (!changed) return false;
  if (dispatch) dispatch(tr);
  return true;
}

// ── Internal ───────────────────────────────────────────────────────────────

function _splitFm(text) {
  const m = text.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  return m
    ? { fmText: m[0].trimEnd(), bodyStart: m[0].length }
    : { fmText: '', bodyStart: 0 };
}

function _plugins() {
  const { list_item } = schema.nodes;
  return [
    history(),
    keymap({
      'Mod-z':       (s, d) => undo(s, d),
      'Mod-y':       (s, d) => redo(s, d),
      'Mod-Shift-z': (s, d) => redo(s, d),
      'Mod-b':       toggleMark(schema.marks.strong),
      'Mod-i':       toggleMark(schema.marks.em),
      'Enter': chainCommands(
        splitListItem(list_item),
        newlineInCode,
        createParagraphNear,
        liftEmptyBlock,
        splitBlockKeepMarks,
      ),
      'Tab': chainCommands(
        (s, d) => sinkListItem(list_item)(s, d),
        indentBlock,
      ),
      'Shift-Tab': chainCommands(
        (s, d) => liftListItem(list_item)(s, d),
        outdentBlock,
      ),
    }),
    keymap(baseKeymap),
  ];
}

