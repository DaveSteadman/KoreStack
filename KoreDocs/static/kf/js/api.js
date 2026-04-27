/**
 * api.js — Thin wrappers around the /api/kf/* REST endpoints.
 */

import { fetchWithAuth } from '/static/shared/js/auth.js';

async function _req(method, path, body) {
  const opts = { method, headers: {} };
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

// ── Folders ────────────────────────────────────────────────────

export const listFolders    = ()                => _req('GET',    '/folders');
export const createFolder   = (name, pid)       => _req('POST',   '/folders',       { name, parent_id: pid });
export const deleteFolder   = (id, expectedRevision = null) => {
  const qs = expectedRevision == null ? '' : `?expected_revision=${encodeURIComponent(expectedRevision)}`;
  return _req('DELETE', `/folders/${id}${qs}`);
};
export const patchFolder    = (id, updates)     => _req('PATCH',  `/folders/${id}`, updates);

// ── Files ──────────────────────────────────────────────────────

export function listFiles({ folderId, folderPath } = {}) {
  const q = new URLSearchParams();
  if (folderId    != null) q.set('folder_id',   folderId);
  if (folderPath  != null) q.set('folder_path', folderPath);
  const qs = q.toString();
  return _req('GET', '/files' + (qs ? '?' + qs : ''));
}

export const getFile   = (id)           => _req('GET',    `/files/${id}`);
export const createFile = (folderId, name, content, metadata) =>
  _req('POST', '/files', { folder_id: folderId, name, content, metadata });
export const updateFile = (id, content, metadata) =>
  _req('PUT', `/files/${id}`, { content, metadata });
export const deleteFile = (id, expectedRevision = null) => {
  const qs = expectedRevision == null ? '' : `?expected_revision=${encodeURIComponent(expectedRevision)}`;
  return _req('DELETE', `/files/${id}${qs}`);
};
export const patchFile = (id, updates)   => _req('PATCH', `/files/${id}`, updates);

// ── Search ─────────────────────────────────────────────────────

export function search(q, { type, folderPath, limit = 40 } = {}) {
  const params = new URLSearchParams({ q, limit });
  if (type)       params.set('type',        type);
  if (folderPath) params.set('folder_path', folderPath);
  return _req('GET', '/search?' + params.toString());
}

// ── Import ─────────────────────────────────────────────────────

export const importFs = () => _req('POST', '/import-fs');
