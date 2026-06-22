const MAX_AGENT_TOOL_REQUESTS = 8;

export function extractAgentEnvelope(text) {
  if (!text || typeof text !== 'string') return null;
  const candidates = [];
  const fenced = /```(?:json)?\s*([\s\S]*?)```/gi;
  let match;
  while ((match = fenced.exec(text)) !== null) {
    const candidate = String(match[1] || '').trim();
    if (candidate) candidates.push(candidate);
  }
  candidates.push(text.trim());

  for (const raw of candidates) {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && typeof parsed.kind === 'string') {
        return parsed;
      }
    } catch {
      // Ignore invalid JSON candidates.
    }
  }
  return null;
}

async function toolReadFile(args, activePath) {
  const path = String(args?.path || activePath || '').trim();
  if (!path) throw new Error('read_file requires path');
  const resp = await fetch(`/api/file?path=${encodeURIComponent(path)}`);
  if (!resp.ok) throw new Error(`read_file failed: ${resp.status}`);
  const payload = await resp.json();
  return {
    path,
    content: String(payload?.content || ''),
    modified_at: payload?.modified_at ?? null,
    content_hash: payload?.content_hash ?? null,
  };
}

async function toolReadContext(args, activePath, workspaceContextEnabled) {
  const path = String(args?.path || activePath || '').trim();
  if (!path) throw new Error('read_context requires path');
  const qs = new URLSearchParams({ path });
  const startLine = Number(args?.start_line);
  const endLine = Number(args?.end_line);
  if (Number.isFinite(startLine) && startLine > 0) qs.set('start_line', String(Math.floor(startLine)));
  if (Number.isFinite(endLine) && endLine > 0) qs.set('end_line', String(Math.floor(endLine)));
  qs.set('include_workspace', args?.include_workspace ? 'true' : (workspaceContextEnabled ? 'true' : 'false'));

  const resp = await fetch(`/api/context?${qs.toString()}`);
  if (!resp.ok) throw new Error(`read_context failed: ${resp.status}`);
  return await resp.json();
}

async function toolListTree(args) {
  const path = String(args?.path || '').trim();
  const query = path ? `?path=${encodeURIComponent(path)}` : '';
  const resp = await fetch(`/api/tree${query}`);
  if (!resp.ok) throw new Error(`list_tree failed: ${resp.status}`);
  return await resp.json();
}

async function toolSearchInFile(args, activePath) {
  const path = String(args?.path || activePath || '').trim();
  const query = String(args?.query || '').trim();
  if (!path) throw new Error('search_in_file requires path');
  if (!query) throw new Error('search_in_file requires query');
  const maxResults = Math.max(1, Math.min(50, Number(args?.max_results || 10)));

  const filePayload = await toolReadFile({ path }, activePath);
  const lines = String(filePayload.content || '').split('\n');
  const needle = query.toLowerCase();
  const matches = [];
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    if (line.toLowerCase().includes(needle)) {
      matches.push({ line: i + 1, preview: line.slice(0, 220) });
      if (matches.length >= maxResults) break;
    }
  }
  return { path, query, matches };
}

async function toolGetPythonFunction(args, activePath) {
  const path   = String(args?.path || activePath || '').trim();
  const symbol = String(args?.symbol || '').trim();
  if (!path)   throw new Error('get_python_function requires path');
  if (!symbol) throw new Error('get_python_function requires symbol');
  const qs = new URLSearchParams({ path, symbol });
  const resp = await fetch(`/api/python-function?${qs.toString()}`);
  if (!resp.ok) throw new Error(`get_python_function failed: ${resp.status}`);
  return await resp.json();
}

async function toolReplacePythonFunction(args, activePath) {
  const path        = String(args?.path || activePath || '').trim();
  const symbol      = String(args?.symbol || '').trim();
  const replacement = String(args?.replacement || '');
  const expectedHash = String(args?.expected_hash || '').trim();
  if (!path)   throw new Error('replace_python_function requires path');
  if (!symbol) throw new Error('replace_python_function requires symbol');
  if (!expectedHash) throw new Error('replace_python_function requires expected_hash');
  if (!replacement.trim()) throw new Error('replace_python_function requires replacement');
  const resp = await fetch('/api/python-function', {
    method:  'PUT',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ path, symbol, replacement, expected_hash: expectedHash }),
  });
  if (!resp.ok) throw new Error(`replace_python_function failed: ${resp.status}`);
  return await resp.json();
}

async function toolInsertPythonFunction(args, activePath) {
  const path        = String(args?.path || activePath || '').trim();
  const source      = String(args?.source || '');
  const expectedHash = String(args?.expected_hash || '').trim();
  const afterSymbol = args?.after_symbol == null ? null : String(args.after_symbol || '').trim() || null;
  const intoClass   = args?.into_class   == null ? null : String(args.into_class   || '').trim() || null;
  if (!path) throw new Error('insert_python_function requires path');
  if (!expectedHash) throw new Error('insert_python_function requires expected_hash');
  if (!source.trim()) throw new Error('insert_python_function requires source');
  const resp = await fetch('/api/python-function', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ path, source, expected_hash: expectedHash, after_symbol: afterSymbol, into_class: intoClass }),
  });
  if (!resp.ok) throw new Error(`insert_python_function failed: ${resp.status}`);
  return await resp.json();
}

export async function executeAgentToolRequests({
  toolRequests,
  activePath,
  workspaceContextEnabled,
  errorText,
}) {
  const requests = Array.isArray(toolRequests)
    ? toolRequests.slice(0, MAX_AGENT_TOOL_REQUESTS)
    : [];
  const out = [];

  for (let i = 0; i < requests.length; i += 1) {
    const request = requests[i] || {};
    const tool = String(request.tool || '').trim();
    const args = request.args && typeof request.args === 'object' ? request.args : {};
    try {
      let result;
      if (tool === 'read_file') {
        result = await toolReadFile(args, activePath);
      } else if (tool === 'read_context') {
        result = await toolReadContext(args, activePath, workspaceContextEnabled);
      } else if (tool === 'list_tree') {
        result = await toolListTree(args);
      } else if (tool === 'search_in_file') {
        result = await toolSearchInFile(args, activePath);
      } else if (tool === 'get_python_function') {
        result = await toolGetPythonFunction(args, activePath);
      } else if (tool === 'replace_python_function') {
        result = await toolReplacePythonFunction(args, activePath);
      } else if (tool === 'insert_python_function') {
        result = await toolInsertPythonFunction(args, activePath);
      } else {
        throw new Error(`Unknown tool: ${tool}`);
      }
      out.push({
        request_index: i,
        tool,
        ok: true,
        result,
      });
    } catch (err) {
      out.push({
        request_index: i,
        tool,
        ok: false,
        error: errorText(err),
      });
    }
  }
  return out;
}
