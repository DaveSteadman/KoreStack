/**
 * korefileapi.js — Shared HTTP client for the KoreFile /api/kf/* endpoints.
 */

import { fetchWithAuth } from '/static/shared/js/auth.js';

async function _req(method, path, body, { keepalive = false } = {}) {
  const opts = { method, headers: {}, keepalive };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetchWithAuth('/api/kf' + path, opts);
  if (!res.ok) {
    const raw = await res.text().catch(() => res.statusText);
    let detail = raw;
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && typeof parsed.detail === 'string') {
        detail = parsed.detail;
      }
    } catch {
      // Non-JSON error bodies fall back to raw text.
    }
    throw new Error(detail || `HTTP ${res.status}`);
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.text();
}

export function getFile(id, { includeContent = true } = {}) {
  const params = new URLSearchParams();
  if (!includeContent) params.set('include_content', 'false');
  const qs = params.toString();
  return _req('GET', `/files/${id}` + (qs ? `?${qs}` : ''));
}

export function listFiles({ type, folderId, folderPath, name, limit } = {}) {
  const params = new URLSearchParams();
  if (type) params.set('type', type);
  if (folderId != null) params.set('folder_id', folderId);
  if (folderPath) params.set('folder_path', folderPath);
  if (name) params.set('name', name);
  if (limit != null) params.set('limit', limit);
  const qs = params.toString();
  return _req('GET', '/files' + (qs ? `?${qs}` : ''));
}

export async function resolveLegacyFile(type, name) {
  const files = await listFiles({ type, name, limit: 1 });
  return files[0] ?? null;
}

export function updateFile(id, content, metadata, options = {}) {
  const { expectedRevision, ...requestOptions } = options;
  return _req('PUT', `/files/${id}`, {
    content,
    metadata,
    expected_revision: expectedRevision,
  }, requestOptions);
}
