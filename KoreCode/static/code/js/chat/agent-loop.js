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
