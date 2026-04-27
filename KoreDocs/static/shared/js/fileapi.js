/**
 * fileapi.js — Shared HTTP client for the KoreDocs /api/files endpoints.
 *
 * All functions are async and throw an Error on non-2xx responses.
 *
 * Usage:
 *   import * as api from '/static/shared/js/fileapi.js';
 *   const files = await api.listFiles('koredoc');
 */

const BASE = '/api/files';
const _etagCache = new Map();

import { fetchWithAuth } from '/static/shared/js/auth.js';

function _rememberEtag(name, response) {
  const etag = response.headers.get('etag');
  if (etag) _etagCache.set(name, etag);
}

/**
 * List files in the data directory.
 * @param {string|null} type  Optional extension filter without dot, e.g. 'koredoc'.
 * @returns {Promise<Array<{name:string, type:string, size:number, modified:number}>>}
 */
export async function listFiles(type = null) {
  const url = type ? `${BASE}?type=${encodeURIComponent(type)}` : BASE;
  const res = await fetchWithAuth(url);
  if (!res.ok) throw new Error(`listFiles: ${res.status} ${res.statusText}`);
  return res.json();
}

/**
 * Read the raw text content of a file.
 * @param {string} name
 * @returns {Promise<string>}
 */
export async function readFile(name) {
  const res = await fetchWithAuth(`${BASE}/${encodeURIComponent(name)}`);
  if (!res.ok) throw new Error(`readFile: ${res.status} ${res.statusText}`);
  _rememberEtag(name, res);
  return res.text();
}

/**
 * Overwrite (or create) a file with the given content.
 * @param {string} name
 * @param {string} content
 */
export async function writeFile(name, content) {
  const headers = { 'Content-Type': 'application/json' };
  const etag = _etagCache.get(name);
  if (etag) headers['If-Match'] = etag;
  const res = await fetchWithAuth(`${BASE}/${encodeURIComponent(name)}`, {
    method:  'PUT',
    headers,
    body:    JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error(`writeFile: ${res.status} ${res.statusText}`);
  _rememberEtag(name, res);
  return res.json();
}

/**
 * Create a new file. Throws (HTTP 409) if it already exists.
 * @param {string} name
 * @param {string} content
 */
export async function createFile(name, content) {
  const res = await fetchWithAuth(BASE, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ name, content }),
  });
  if (!res.ok) throw new Error(`createFile: ${res.status} ${res.statusText}`);
  _rememberEtag(name, res);
  return res.json();
}

/**
 * Delete a file.
 * @param {string} name
 */
export async function deleteFile(name) {
  const headers = {};
  const etag = _etagCache.get(name);
  if (etag) headers['If-Match'] = etag;
  const res = await fetchWithAuth(`${BASE}/${encodeURIComponent(name)}`, {
    method: 'DELETE',
    headers,
  });
  if (!res.ok) throw new Error(`deleteFile: ${res.status} ${res.statusText}`);
  _etagCache.delete(name);
  return res.json();
}
