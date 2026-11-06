/**
 * editor.js - ProseMirror WYSIWYG editor for KoreDoc.
 *
 * Stores the document as ProseMirror state; serialises to/from Markdown.
 * Document metadata headers are stashed separately so ProseMirror only sees the body.
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
      document.dispatchEvent(new CustomEvent('kd:editor-state-change', {
        detail: { state: next },
      }));
      if (tr.docChanged) _onUpdate?.(getValue());
    },
  });
}

/** Return the full document as Markdown (frontmatter + body). */
export function getValue() {
  if (!_view) return _fm;
  const body = getBodyValue();
  return _fm ? `${_fm}\n\n${body}` : body;
}

/** Return only the Markdown body visible in the rendered editor. */
export function getBodyValue() {
  return _view ? serializeMarkdown(_view.state.doc) : '';
}

export function serializeMarkdown(doc) {
  const blocks = [];
  doc.forEach(node => {
    const blockDoc = schema.topNodeType.create(null, node);
    let markdown = defaultMarkdownSerializer.serialize(blockDoc);
    if (node.type === schema.nodes.paragraph && node.attrs.indent > 0) {
      markdown = `<!-- koredocs-indent:${node.attrs.indent} -->\n\n${markdown}`;
    }
    blocks.push(markdown);
  });
  return blocks.join('\n\n').trimEnd();
}

export function parseMarkdown(markdown) {
  const indentMarkers = [];
  const markerPattern = /^<!-- koredocs-indent:(\d+) -->[ \t]*\r?\n/gm;
  let body = '';
  let cursor = 0;
  let match;
  while ((match = markerPattern.exec(markdown)) !== null) {
    body += markdown.slice(cursor, match.index);
    const preceding = body.trim();
    const precedingNodeCount = preceding ? _parser.parse(preceding).childCount : 0;
    indentMarkers.push({
      nodeIndex: precedingNodeCount,
      indent: Math.max(0, Math.min(8, Number(match[1]) || 0)),
    });
    cursor = markerPattern.lastIndex;
  }
  body += markdown.slice(cursor);

  const parsed = body.trim() ? _parser.parse(body) : schema.topNodeType.createAndFill();
  if (!indentMarkers.length) return parsed;

  const indentByNode = new Map(indentMarkers.map(marker => [marker.nodeIndex, marker.indent]));
  const nodes = [];
  parsed.forEach((node, _offset, index) => {
    const indent = indentByNode.get(index);
    nodes.push(
      indent !== undefined && node.type === schema.nodes.paragraph
        ? node.type.create({ ...node.attrs, indent }, node.content, node.marks)
        : node,
    );
  });
  return schema.topNodeType.create(null, nodes);
}

/**
 * Replace editor content from a Markdown string.
 * Strips and stashes YAML or KoreDocs JSON metadata; resets undo history.
 */
export function setValue(markdown) {
  if (!_view) return;
  const { fmText, bodyStart } = _splitFm(markdown);
  _fm = fmText;
  const body = markdown.slice(bodyStart).trim();
  const doc = parseMarkdown(body);
  _view.updateState(EditorState.create({ schema, doc, plugins: _view.state.plugins }));
  document.dispatchEvent(new CustomEvent('kd:editor-state-change', {
    detail: { state: _view.state },
  }));
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
  const area   = _view.dom.closest('#editor-area');
  if (!area) return;
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
  state.doc.forEach((node, pos) => {
    if (node.type !== para) return;
    const contentStart = pos + 1;
    const contentEnd = pos + node.nodeSize - 1;
    const selected = $from.pos === $to.pos
      ? $from.pos >= contentStart && $from.pos <= contentEnd
      : $from.pos < contentEnd && $to.pos > contentStart;
    if (!selected) return;
    const next = Math.max(0, Math.min(8, (node.attrs.indent || 0) + delta));
    if (next === (node.attrs.indent || 0)) return;
    tr = tr.setNodeMarkup(pos, null, { ...node.attrs, indent: next });
    changed = true;
  });
  if (!changed) return false;
  if (dispatch) dispatch(tr);
  return true;
}

// ── Internal ───────────────────────────────────────────────────────────────

function _splitFm(text) {
  const jsonHeader = text.match(/^---koredocs-json\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/i);
  if (jsonHeader) {
    try {
      const record = JSON.parse(jsonHeader[1]);
      if (record && typeof record === 'object' && typeof record.metadata === 'object') {
        return { fmText: jsonHeader[0].trimEnd(), bodyStart: jsonHeader[0].length };
      }
    } catch {
      // Leave malformed headers visible so they can be corrected in the text editor.
    }
  }

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
