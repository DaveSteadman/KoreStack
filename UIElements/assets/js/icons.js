/**
 * icons.js — shared icon registry for UIElements shell primitives.
 * All SVG markup is sourced from svg_icons.js.
 */

import {
  koreStackIcon, koreAgentIcon, koreDataIcon, koreDocsIcon, koreCodeIcon,
  koreCommsIcon, koreChatIcon,
  koreDocFileIcon, koreSheetFileIcon, koreDiagFileIcon,
  pyFileIcon, jsFileIcon, jsonFileIcon, htmlFileIcon, cssFileIcon, genericFileIcon,
} from './svg_icons.js?v=20260501a';

export { fileIconForPath };

/**
 * Returns an SVG icon for a given file path based on its extension.
 * Falls back to a generic file icon for unknown types.
 */
function fileIconForPath(path, size = 12) {
  if (!path) { return genericFileIcon(size); }
  const lower = path.toLowerCase();
  if (lower.endsWith('.py') || lower.endsWith('.pyi')) { return pyFileIcon(size); }
  if (lower.endsWith('.js') || lower.endsWith('.mjs') || lower.endsWith('.cjs') || lower.endsWith('.ts')) { return jsFileIcon(size); }
  if (lower.endsWith('.json')) { return jsonFileIcon(size); }
  if (lower.endsWith('.md') || lower.endsWith('.markdown')) { return koreDocFileIcon(size); }
  if (lower.endsWith('.html') || lower.endsWith('.htm')) { return htmlFileIcon(size); }
  if (lower.endsWith('.css')) { return cssFileIcon(size); }
  if (lower.endsWith('.csv') || lower.endsWith('.tsv')) { return koreSheetFileIcon(size); }
  return genericFileIcon(size);
}

export const SUITE_ICONS = {
  korestack: koreStackIcon,
  koreagent: koreAgentIcon,
  koredata: koreDataIcon,
  koredocs: koreDocsIcon,
  korecode: koreCodeIcon,
  korecomms: koreCommsIcon,
  korechat: koreChatIcon,
  koredoc: koreDocFileIcon,
  koresheet: koreSheetFileIcon,
  kodiag: koreDiagFileIcon,
  korefile: koreDocsIcon,
};

export function resolveIcon(icons, key, size) {
  const icon = icons[key];
  if (typeof icon === 'function') return icon(size);
  if (typeof icon === 'string') return icon;
  return '';
}

