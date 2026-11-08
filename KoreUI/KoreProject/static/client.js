export function node(tag, text = '', className = '') {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

export async function api(path, method = 'GET', body) {
  const response = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body === undefined ? undefined : JSON.stringify(body) });
  const result = await response.json();
  if (!response.ok) {
    const detail = typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail);
    throw new Error(`${response.status}: ${detail || response.statusText}`);
  }
  return result;
}

export async function allPages(path) {
  const items = [];
  let page;
  const separator = path.includes('?') ? '&' : '?';
  do {
    page = await api(`${path}${separator}offset=${items.length}&limit=500`);
    items.push(...page.items);
  } while (items.length < page.total && page.items.length);
  return items;
}

export function allRecords(kind, project = '') {
  return allPages(`/api/records/${kind}?project_id=${encodeURIComponent(project)}`);
}

const loadedProjectKey = 'koreproject.loadedProject';

export function getLoadedProject(params) {
  if (params.has('project')) return params.get('project') || '';
  try {
    return localStorage.getItem(loadedProjectKey) || '';
  } catch (error) {
    if (!(error instanceof DOMException)) throw error;
    console.warn('KoreProject could not restore the loaded project from browser storage.', error);
    return '';
  }
}

export function setLoadedProject(projectId) {
  try {
    if (projectId) localStorage.setItem(loadedProjectKey, projectId);
    else localStorage.removeItem(loadedProjectKey);
  } catch (error) {
    if (!(error instanceof DOMException)) throw error;
    console.warn('KoreProject could not remember the loaded project in browser storage.', error);
  }
  const url = new URL(location.href);
  if (!projectId || (url.searchParams.has('project') && url.searchParams.get('project') !== projectId)) {
    for (const key of ['board', 'ticket', 'statement', 'section', 'requirement', 'design']) url.searchParams.delete(key);
  }
  if (projectId) url.searchParams.set('project', projectId);
  else url.searchParams.delete('project');
  history.replaceState(null, '', url);
}

export function projectTabs(project, current) {
  const query = `?project=${encodeURIComponent(project)}`;
  return [
    ['workspace', 'Project workspace', '/ui'],
    ['requirements', 'Requirements', '/ui/requirements'],
    ['design', 'Design', '/ui/design'],
    ['tickets', 'Tickets', '/ui/tickets'],
    ['boards', 'Boards', '/ui/boards'],
    ['settings', 'Data settings', '/ui/settings'],
  ].map(([page, label, path]) => ({ label, href: path + query, active: page === current }));
}
