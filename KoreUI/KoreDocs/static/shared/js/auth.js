const TOKEN_KEY = 'koredocs:api-token';

export function getAuthToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
      || localStorage.getItem(TOKEN_KEY)
      || '';
  } catch {
    return '';
  }
}

export function setAuthToken(token, { persist = false } = {}) {
  const value = (token || '').trim();
  try {
    sessionStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(TOKEN_KEY);
    if (!value) return;
    const store = persist ? localStorage : sessionStorage;
    store.setItem(TOKEN_KEY, value);
  } catch {
    // Ignore storage failures.
  }
}

export async function fetchWithAuth(url, opts = {}, { retryOnAuth = true } = {}) {
  const request = { ...opts, headers: new Headers(opts.headers || {}) };
  const token = getAuthToken();
  if (token && !request.headers.has('x-koredocs-token') && !request.headers.has('authorization')) {
    request.headers.set('x-koredocs-token', token);
  }

  let response = await fetch(url, request);
  if (response.status !== 401 || !retryOnAuth || typeof window === 'undefined' || typeof window.prompt !== 'function') {
    return response;
  }

  const entered = window.prompt('KoreDocs API token');
  if (!entered) return response;

  setAuthToken(entered);
  request.headers.set('x-koredocs-token', entered.trim());
  response = await fetch(url, request);
  return response;
}