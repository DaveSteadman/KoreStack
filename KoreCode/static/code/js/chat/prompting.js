const DEFAULT_MAX_MENTION_FILE_CHARS = 7000;
const DEFAULT_MAX_MENTION_COUNT = 4;

const AGENT_OUTPUT_SCHEMA = {
  kind: 'analysis|plan|tool_requests|edits|final',
  summary: 'short summary string',
  findings: [],
  tool_requests: [
    {
      tool: 'read_file|read_context|list_tree|search_in_file',
      args: {},
      reason: 'why this tool is needed',
    },
  ],
  edits: [
    {
      file: 'path/to/file',
      from: 1,
      to: 1,
      replacement: 'new text',
      explanation: 'why this change is needed',
    },
  ],
  next: 'continue|done',
};

const AGENT_TOOL_GUIDE = {
  read_file: { args: { path: 'workspace-relative path' } },
  read_context: {
    args: {
      path: 'workspace-relative path',
      start_line: 'optional positive integer',
      end_line: 'optional positive integer',
      include_workspace: 'optional boolean',
    },
  },
  list_tree: { args: { path: 'optional workspace-relative directory path, default root' } },
  search_in_file: {
    args: {
      path: 'workspace-relative path',
      query: 'plain text to match',
      max_results: 'optional positive integer',
    },
  },
};

function extractFileMentions(text, maxMentionCount = DEFAULT_MAX_MENTION_COUNT) {
  const tokens = String(text || '').replaceAll('\n', ' ').replaceAll('\t', ' ').split(' ').filter(Boolean);
  const out = [];
  const seen = new Set();
  for (const token of tokens) {
    if (!token.startsWith('@') || token.length < 3) continue;
    let raw = token.slice(1).replaceAll('\\', '/').trim();
    while (raw.length > 1 && '),.;:\'"}]>'.includes(raw[raw.length - 1])) {
      raw = raw.slice(0, -1);
    }
    if (!raw.includes('.') || raw.endsWith('.')) continue;
    if (!raw || raw.startsWith('/') || raw.includes('..') || seen.has(raw)) continue;
    seen.add(raw);
    out.push(raw);
    if (out.length >= maxMentionCount) break;
  }
  return out;
}

async function fetchMentionContext(paths, maxMentionFileChars = DEFAULT_MAX_MENTION_FILE_CHARS) {
  if (!Array.isArray(paths) || !paths.length) return [];
  const blocks = [];
  for (const path of paths) {
    try {
      const resp = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
      if (!resp.ok) continue;
      const payload = await resp.json();
      const content = String(payload?.content || '').slice(0, maxMentionFileChars);
      const lines = content.split('\n');
      const head = lines.slice(0, 160).join('\n');
      blocks.push({
        path,
        content: head,
        truncated: content.length >= maxMentionFileChars || lines.length > 160,
      });
    } catch {
      // Ignore unreadable mention targets.
    }
  }
  return blocks;
}

function buildMentionContextBlock(mentionBlocks) {
  if (!Array.isArray(mentionBlocks) || !mentionBlocks.length) return '';
  const blocks = mentionBlocks
    .map((item) => `FILE: ${item.path}${item.truncated ? ' (truncated)' : ''}\n\`\`\`\n${item.content}\n\`\`\``)
    .join('\n\n');
  return `\n\n[MENTIONED_FILES]\n${blocks}\n[/MENTIONED_FILES]`;
}

function instructionByMode(mode) {
  const contracts = {
    chat: 'Solve the user request directly. Use tool_requests if you need extra codebase evidence before proposing edits.',
    explain: 'Explain behavior, edge cases, and side effects. Use tool_requests first if context is insufficient.',
    bughunt: 'Find bugs and risks with concise severity and evidence. Request tools first when uncertain.',
    refactor: 'Refactor without changing behavior. Prefer minimal edits and preserve style.',
    tests: 'Create focused tests for behavior and edge cases. Keep edits scoped to test files unless requested.',
  };
  return contracts[mode] || contracts.chat;
}

function buildAgentContract(mode) {
  const lines = [
    'You are KoreCode Agent, a coding agent that can request tools before proposing changes.',
    instructionByMode(mode),
    'Always output EXACTLY one valid JSON object and nothing else.',
    'Do not wrap output in markdown fences.',
    'If additional information is required, set kind="tool_requests" and next="continue".',
    'When finished, set next="done" and use kind="analysis", "edits", or "final" as appropriate.',
    'When emitting edits, include line-based ranges with from/to inclusive.',
    'If creating a new file, use from=1 and to=1 and put full file content in replacement.',
  ];
  return lines.join(' ');
}

export function buildAgentToolFollowupPrompt({
  mode,
  path,
  userText,
  previousResponse,
  toolResults,
}) {
  return [
    buildAgentContract(mode),
    '',
    '[ACTIVE_FILE]',
    path,
    '[/ACTIVE_FILE]',
    '',
    '[ORIGINAL_USER_REQUEST]',
    userText,
    '[/ORIGINAL_USER_REQUEST]',
    '',
    '[PREVIOUS_AGENT_RESPONSE_JSON]',
    previousResponse,
    '[/PREVIOUS_AGENT_RESPONSE_JSON]',
    '',
    '[TOOL_RESULTS]',
    JSON.stringify(toolResults, null, 2),
    '[/TOOL_RESULTS]',
    '',
    '[OUTPUT_SCHEMA]',
    JSON.stringify(AGENT_OUTPUT_SCHEMA, null, 2),
    '[/OUTPUT_SCHEMA]',
    '',
    'Return one JSON object now, based on tool results.',
  ].join('\n');
}

export async function buildPromptByMode({
  mode,
  userText,
  path,
  selection,
  cursor,
  workspaceContextEnabled,
  maxMentionCount = DEFAULT_MAX_MENTION_COUNT,
  maxMentionFileChars = DEFAULT_MAX_MENTION_FILE_CHARS,
}) {
  const hasActiveFile = typeof path === 'string' && path.trim() && path !== '.';
  const base = selection
    ? `The following code is selected in the editor:\n\`\`\`\n${selection}\n\`\`\`\n\n${userText}`
    : userText;

  const mentionPaths = extractFileMentions(userText, maxMentionCount);
  const mentionBlocks = await fetchMentionContext(mentionPaths, maxMentionFileChars);
  const mentionContextBlock = buildMentionContextBlock(mentionBlocks);

  let contextPack = null;
  if (hasActiveFile) {
    try {
      const qs = new URLSearchParams({ path });
      const querySeed = [userText, selection || ''].filter(Boolean).join('\n').slice(0, 1200);
      if (cursor?.line) {
        qs.set('start_line', String(cursor.line));
        qs.set('end_line', String(cursor.line));
      }
      if (workspaceContextEnabled && querySeed) {
        qs.set('query', querySeed);
      }
      qs.set('include_workspace', workspaceContextEnabled ? 'true' : 'false');
      const resp = await fetch(`/api/context?${qs.toString()}`);
      if (resp.ok) {
        contextPack = await resp.json();
      }
    } catch {
      contextPack = null;
    }
  }

  const contextBlock = contextPack
    ? `\n\n[CONTEXT_PACK]\n${JSON.stringify(contextPack, null, 2)}\n[/CONTEXT_PACK]`
    : '';
  const agentContract = buildAgentContract(mode);
  return [
    agentContract,
    '',
    '[ACTIVE_FILE]',
    path,
    '[/ACTIVE_FILE]',
    '',
    '[AVAILABLE_TOOLS]',
    JSON.stringify(AGENT_TOOL_GUIDE, null, 2),
    '[/AVAILABLE_TOOLS]',
    '',
    '[OUTPUT_SCHEMA]',
    JSON.stringify(AGENT_OUTPUT_SCHEMA, null, 2),
    '[/OUTPUT_SCHEMA]',
    '',
    '[USER_TASK]',
    base,
    '[/USER_TASK]',
    mentionContextBlock,
    contextBlock,
  ].join('\n');
}
