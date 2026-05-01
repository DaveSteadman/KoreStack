export const STORAGE_TABS = 'korecode:open-tabs';
export const STORAGE_ACTIVE = 'korecode:active-tab';

export const state = {
  root: '',
  tree: new Map(),
  expanded: new Set(['']),
  openTabs: [],
  activePath: null,
};

export async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      // Ignore invalid JSON error payloads.
    }
    throw new Error(detail);
  }
  return response.json();
}
