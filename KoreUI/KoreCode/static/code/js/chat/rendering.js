export function esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export function renderAssistantText(text) {
  const structured = extractStructuredEdits(text);
  if (structured) {
    const summary = String(structured.summary || 'Edit proposal ready.').trim();
    const files = [...new Set(structured.edits.map((edit) => String(edit?.file || '').trim()).filter(Boolean))];
    const fileLabel = files.length ? `KoreCode validates and applies direct coding changes to ${files.join(', ')}.` : 'KoreCode validates and applies direct coding changes.';
    return `<p>${esc(summary)}</p><p>${esc(fileLabel)}</p>`;
  }
  return text
    .split(/(```[\s\S]*?```)/g)
    .map((part) => {
      if (part.startsWith('```')) {
        const inner = part.replace(/^```[^\n]*\n?/, '').replace(/```$/, '');
        return `<pre>${esc(inner)}</pre>`;
      }
      return part
        .split(/\n{2,}/)
        .map((para) => para.trim())
        .filter(Boolean)
        .map((para) => `<p>${esc(para)}</p>`)
        .join('');
    })
    .join('');
}

export function extractCodeForActions(text) {
  const blocks = [];
  const pattern = /```[^\n]*\n?([\s\S]*?)```/g;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    const snippet = (match[1] ?? '').trim();
    if (snippet) blocks.push(snippet);
  }
  if (!blocks.length) return null;
  return blocks.join('\n\n');
}

export function extractStructuredEdits(text) {
  if (!text || typeof text !== 'string') return null;
  const candidates = [];
  const pattern = /```(?:json)?\s*([\s\S]*?)```/gi;
  let match;
  while ((match = pattern.exec(text)) !== null) {
    const candidate = (match[1] || '').trim();
    if (candidate) candidates.push(candidate);
  }
  candidates.push(text.trim());

  for (const raw of candidates) {
    try {
      const parsed = JSON.parse(raw);
      if (parsed && Array.isArray(parsed.edits)) {
        return parsed;
      }
    } catch {
      // Ignore non-JSON candidates.
    }
  }
  return null;
}
