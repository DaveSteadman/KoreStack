import { initTopbar, initAppBar, kcuiForm, kcuiConfirm } from '/ui-elements/assets/js/chrome.js';
import { api, allRecords, allPages, node, projectTabs, getLoadedProject, setLoadedProject } from './client.js';

const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const isDesign = location.pathname === '/ui/design';
const kind = isDesign ? 'design' : 'requirement';
const plural = isDesign ? 'design statements' : 'requirements';
const label = isDesign ? 'Design' : 'Requirement';
const linkedKind = isDesign ? 'requirement' : 'design';
const linkedLabel = isDesign ? 'Requirement' : 'Design';
const pagePath = isDesign ? '/ui/design' : '/ui/requirements';
const state = {
  projectId: getLoadedProject(params), project: null, sections: [], statements: [], links: [],
  selected: params.get('section') || 'all', expanded: new Set(), drafts: new Map(), busy: false,
};
const editable = ['title', 'body'];
const labels = { title: 'Title', body: label };
const mutable = () => Boolean(state.project && !state.project.archived && !state.project.deletion_pending);
const section = id => state.sections.find(item => item.id === id);
const findStatement = id => state.statements.find(item => item.id === id && item.kind === kind);
const pageStatements = () => state.statements.filter(item => item.kind === kind);

document.title = `${isDesign ? 'Design' : 'Requirements'} - KoreProject`;
$('new-requirement').textContent = `New ${kind}`;
$('statement-heading').textContent = label;
$('links-heading').textContent = `${linkedLabel} links`;
$('search').placeholder = `ID, title, ${kind} or linked ${linkedKind}`;
document.querySelector('aside').setAttribute('aria-label', `${label} sections`);
$('section-tree').setAttribute('aria-label', `${label} sections`);
document.querySelector('.kr-sheet-scroll').setAttribute('aria-label', `Editable ${plural} table`);

initTopbar({ currentService: 'koreproject' });
initAppBar({ brandLabel: 'KoreProject', brandIcon: 'koreproject', currentService: 'koreproject',
  tabs: projectTabs(state.projectId, isDesign ? 'design' : 'requirements') });

function notice(text, error = false) {
  $('notice').textContent = text;
  $('notice').className = error ? 'kcui-field__error' : 'kcui-field__hint';
  $('notice').setAttribute('role', error ? 'alert' : 'status');
}

async function run(action) {
  if (state.busy) return;
  state.busy = true;
  document.querySelector('main').inert = true;
  updateActions();
  try { await action(); }
  catch (error) { notice(error.message, true); }
  finally {
    state.busy = false;
    document.querySelector('main').inert = false;
    updateActions();
  }
}

function updateActions() {
  const disabled = state.busy || !mutable();
  $('new-section').disabled = disabled;
  $('new-requirement').disabled = disabled;
  $('edit-section').disabled = disabled || !section(state.selected);
  $('delete-section').disabled = disabled || !section(state.selected);
  $('save-all').disabled = disabled || !state.drafts.size;
  $('save-all').title = `Save ${state.drafts.size} new or edited ${plural}`;
  $('row-count').textContent = `${$('requirements').querySelectorAll('tr[id]').length} ${plural} / ${state.drafts.size} unsaved`;
  $('load-project').disabled = state.busy || !$('projects').value || $('projects').value === state.projectId;
}

async function load() {
  const projects = (await allRecords('project')).filter(item => !item.deletion_pending);
  const selector = $('projects'), placeholder = node('option', 'Select a project');
  placeholder.value = '';
  selector.replaceChildren(placeholder);
  for (const project of projects) {
    const option = node('option', `${project.key}: ${project.title}`);
    option.value = project.id;
    selector.append(option);
  }
  state.project = projects.find(item => item.id === state.projectId) || null;
  selector.value = state.project?.id || '';
  const unavailable = state.projectId && !state.project;
  if (unavailable) {
    state.projectId = '';
    state.selected = 'all';
    state.expanded.clear();
    state.drafts.clear();
  }
  setLoadedProject(state.projectId);
  initAppBar({ brandLabel: 'KoreProject', brandIcon: 'koreproject', currentService: 'koreproject',
    tabs: projectTabs(state.projectId, isDesign ? 'design' : 'requirements') });
  if (state.project) {
    [state.sections, state.statements, state.links] = await Promise.all([
      allRecords('section', state.projectId), allRecords('statement', state.projectId),
      allPages(`/api/statement-links?project_id=${encodeURIComponent(state.projectId)}`),
    ]);
    state.sections = state.sections.filter(item => item.kind === kind);
    for (const draft of state.drafts.values()) {
      if (draft.record.isNew) state.statements.push(draft.record);
    }
  } else {
    state.sections = []; state.statements = []; state.links = [];
  }
  $('project-name').textContent = state.project ? `${state.project.key}${state.project.archived ? ' (archived, read-only)' : ''}` : 'No project loaded';
  if (!['all', 'unfiled'].includes(state.selected) && (!section(state.selected) || section(state.selected).archived)) state.selected = 'all';
  let parent = section(state.selected)?.parent_id;
  while (parent) { state.expanded.add(parent); parent = section(parent)?.parent_id; }
  renderTree();
  renderTable();
  if (unavailable) throw new Error('Project is unavailable or pending deletion. Choose another project.');
}

function sectionPath(id) {
  const path = [], seen = new Set();
  while (id && !seen.has(id)) {
    seen.add(id);
    const item = section(id);
    if (!item) { path.unshift('Unavailable section'); break; }
    path.unshift(item.title);
    id = item.parent_id;
  }
  return path.join(' / ');
}

function sectionOptions(exclude = '') {
  return state.sections.filter(item => {
    if (item.archived) return false;
    let id = item.id;
    while (id) {
      if (id === exclude) return false;
      id = section(id)?.parent_id;
    }
    return true;
  }).map(item => ({ value: item.id, label: sectionPath(item.id) }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

function selectSection(id) {
  state.selected = id;
  const url = new URL(location.href);
  url.searchParams.set('section', id);
  url.searchParams.delete(kind);
  history.replaceState(null, '', url);
  renderTree();
  renderTable();
  updateActions();
}

function renderTree() {
  const host = $('section-tree');
  host.replaceChildren();
  if (!state.project) return;
  const counts = new Map();
  for (const item of pageStatements()) {
    const id = item.section_id || 'unfiled';
    counts.set(id, (counts.get(id) || 0) + 1);
    counts.set('all', (counts.get('all') || 0) + 1);
  }
  function branch(id, title, parent, depth, children = []) {
    const wrapper = node('div');
    wrapper.setAttribute('role', 'none');
    const row = node('button', '', `tree-row is-dir${state.selected === id ? ' is-active' : ''}`);
    row.type = 'button';
    row.dataset.section = id;
    row.dataset.parent = parent || '';
    row.style.setProperty('--depth', depth);
    row.setAttribute('role', 'treeitem');
    row.setAttribute('aria-level', String(depth + 1));
    row.setAttribute('aria-selected', String(state.selected === id));
    row.tabIndex = state.selected === id ? 0 : -1;
    const caret = node('span', children.length ? (state.expanded.has(id) ? '-' : '+') : '', 'tree-caret');
    caret.setAttribute('aria-hidden', 'true');
    const label = node('span', `${title} (${counts.get(id) || 0})`, 'tree-label');
    row.title = title;
    row.append(caret, label);
    row.addEventListener('click', event => {
      if (event.target === caret && children.length) {
        if (state.expanded.has(id)) state.expanded.delete(id);
        else state.expanded.add(id);
        renderTree();
      } else {
        if (children.length) state.expanded.add(id);
        selectSection(id);
      }
      $('section-tree').querySelector(`[data-section="${id}"]`)?.focus();
    });
    wrapper.append(row);
    if (children.length) {
      row.setAttribute('aria-expanded', String(state.expanded.has(id)));
      if (state.expanded.has(id)) {
        const group = node('div');
        group.setAttribute('role', 'group');
        for (const child of children) group.append(build(child, depth + 1));
        wrapper.append(group);
      }
    }
    return wrapper;
  }
  const childrenOf = id => state.sections.filter(item => !item.archived && item.parent_id === id)
    .sort((a, b) => a.position - b.position || a.title.localeCompare(b.title));
  const build = (item, depth) => branch(item.id, item.title, item.parent_id, depth, childrenOf(item.id));
  host.append(branch('all', `All ${plural}`, null, 0), branch('unfiled', 'Unfiled', null, 0));
  for (const item of childrenOf(null)) host.append(build(item, 0));
}

$('section-tree').addEventListener('keydown', event => {
  const row = event.target.closest('[role=treeitem]');
  if (!row) return;
  const rows = [...$('section-tree').querySelectorAll('[role=treeitem]')];
  const index = rows.indexOf(row), id = row.dataset.section;
  let target;
  if (event.key === 'ArrowDown') target = rows[index + 1];
  else if (event.key === 'ArrowUp') target = rows[index - 1];
  else if (event.key === 'Home') target = rows[0];
  else if (event.key === 'End') target = rows.at(-1);
  else if (event.key === 'ArrowRight' && row.hasAttribute('aria-expanded')) {
    state.expanded.add(id); renderTree();
    target = $('section-tree').querySelector(`[data-parent="${id}"]`);
  } else if (event.key === 'ArrowLeft') {
    if (state.expanded.has(id)) { state.expanded.delete(id); renderTree(); target = $('section-tree').querySelector(`[data-section="${id}"]`); }
    else target = $('section-tree').querySelector(`[data-section="${row.dataset.parent}"]`);
  } else return;
  event.preventDefault();
  target?.focus();
});

function linksFor(id) { return state.links.filter(link => link.source_id === id || link.target_id === id); }

function designLinkButton(symbol, label, action, disabled) {
  const control = node('button', symbol, 'kcui-btn-icon kcui-btn-icon--compact');
  control.type = 'button';
  control.title = label;
  control.setAttribute('aria-label', label);
  control.disabled = disabled;
  control.addEventListener('click', () => run(action));
  return control;
}

async function addStatementLink(record) {
  const linkedIds = new Set(linksFor(record.id).flatMap(link => [link.source_id, link.target_id]));
  const targets = state.statements.filter(item => item.kind === linkedKind && !item.archived && !linkedIds.has(item.id));
  if (!targets.length) throw new Error(`No unlinked ${linkedKind} statements are available in this project.`);
  const values = await kcuiForm(`Link ${linkedKind} to ${record.ref}`, {
    fields: [{
      name: 'target_id', label: linkedLabel, type: 'select', required: true,
      options: [{ value: '', label: `Choose a ${linkedKind}` }, ...targets.map(item => ({
        value: item.id, label: `${item.ref}: ${item.title}`,
      }))],
    }],
    confirmLabel: 'Add link',
  });
  if (!values) return;
  const saved = await api('/api/links', 'POST', {
    source_kind: 'statement', source_id: isDesign ? record.id : values.target_id,
    target_kind: 'statement', target_id: isDesign ? values.target_id : record.id, relation: 'satisfies',
  });
  state.links.push(saved);
  renderTable();
  notice(`${linkedLabel} linked to ${record.ref}.`);
}

function designLinkCell(record) {
  const cell = node('td', '', 'kr-links');
  const list = node('div', '', 'kcui-stack kcui-stack--xs');
  const disabled = !mutable() || Boolean(record.isNew);
  for (const link of linksFor(record.id)) {
    const outgoing = link.source_id === record.id;
    const otherId = outgoing ? link.target_id : link.source_id;
    const other = state.statements.find(item => item.id === otherId);
    if (other?.kind !== linkedKind) continue;
    const entry = node('div', '', 'kcui-toolbar kr-design-link');
    const anchor = node('a', other.ref);
    anchor.href = `/ui/${isDesign ? 'requirements' : 'design'}?project=${state.projectId}&${linkedKind}=${other.id}`;
    const remove = designLinkButton('-', `Remove ${other.ref} from ${record.ref}`, async () => {
      if (!await kcuiConfirm(`Remove ${linkedKind} link`, `Remove ${other.ref} from ${record.ref}?`)) return;
      await api(`/api/links/statement/${link.id}`, 'DELETE');
      state.links = state.links.filter(item => item.id !== link.id);
      renderTable();
      notice(`Removed ${other.ref} from ${record.ref}.`);
    }, disabled);
    entry.append(anchor, remove);
    list.append(entry);
  }
  list.append(designLinkButton('+', record.isNew ? `Save this ${kind} before linking statements` :
    `Add ${linkedKind} link to ${record.ref}`, () => addStatementLink(record), disabled));
  cell.append(list);
  return cell;
}

let rowSizingFrame = 0;
function sizeRows() {
  cancelAnimationFrame(rowSizingFrame);
  rowSizingFrame = requestAnimationFrame(() => {
    for (const row of $('requirements').querySelectorAll('tr[id]')) {
      const controls = [...row.querySelectorAll('textarea')];
      for (const control of controls) control.style.height = '0px';
      const height = Math.ceil(Math.max(
        row.querySelector('.kr-links > div').getBoundingClientRect().height,
        ...controls.map(control => control.scrollHeight + control.offsetHeight - control.clientHeight),
      ));
      for (const control of controls) control.style.height = `${height}px`;
    }
  });
}

let sheetWidth = 0;
new ResizeObserver(([entry]) => {
  if (entry.contentRect.width === sheetWidth) return;
  sheetWidth = entry.contentRect.width;
  sizeRows();
}).observe(document.querySelector('.kr-sheet'));
document.fonts.ready.then(sizeRows);

function renderTable() {
  const host = $('requirements');
  host.replaceChildren();
  $('table-title').textContent = state.selected === 'all' ? `All ${plural}` :
    state.selected === 'unfiled' ? `Unfiled ${plural}` : sectionPath(state.selected);
  const query = $('search').value.toLowerCase();
  const rows = pageStatements().filter(item => {
    if (state.selected !== 'all' && (item.section_id || 'unfiled') !== state.selected) return false;
    const linked = linksFor(item.id).map(link => state.statements.find(other =>
      other.id === (link.source_id === item.id ? link.target_id : link.source_id)))
      .filter(other => other?.kind === linkedKind);
    return [item.ref, item.title, item.body, ...linked.flatMap(other => [other?.ref, other?.title])]
      .some(value => String(value || '').toLowerCase().includes(query));
  });
  $('row-count').textContent = `${rows.length} ${plural} / ${state.drafts.size} unsaved`;
  for (const record of rows) {
    const draft = state.drafts.get(record.id);
    const data = draft?.data || record;
    const row = node('tr');
    row.id = `${kind}-${record.id}`;
    row.classList.toggle('is-dirty', Boolean(draft));
    row.append(node('td', record.isNew ? '' : record.ref, 'kr-record-id'));
    const controls = {};
    for (const key of ['title', 'body']) {
      const cell = node('td');
      const control = node('textarea');
      control.setAttribute('aria-label', `${labels[key]} ${record.ref}`);
      control.name = key;
      control.rows = 1;
      control.value = data[key] ?? '';
      if (key === 'title') { control.required = true; control.maxLength = 300; }
      if (key === 'body') control.maxLength = 200000;
      control.disabled = !mutable();
      controls[key] = control;
      control.addEventListener('input', () => {
        const values = Object.fromEntries(editable.map(field => [field, controls[field].value]));
        state.drafts.set(record.id, { record: draft?.record || record, data: values });
        row.classList.add('is-dirty');
        updateActions();
        sizeRows();
      });
      cell.append(control);
      row.append(cell);
    }
    row.append(designLinkCell(record));
    host.append(row);
  }
  if (!rows.length) {
    const row = node('tr', '', 'kcui-table__empty'), cell = node('td',
      state.project ? `No ${plural} in this table. Add a ${kind} or choose another section.` : `Load a project to view ${plural}.`);
    cell.colSpan = 4; row.append(cell); host.append(row);
  }
  sizeRows();
}

async function saveRow(id) {
  const draft = state.drafts.get(id);
  if (!draft) return;
  if (!draft.data.title.trim()) throw new Error(`${draft.record.ref}: Title is required.`);
  const isNew = Boolean(draft.record.isNew);
  const saved = await api(`/api/records/statement${isNew ? '' : `/${id}`}`, isNew ? 'POST' : 'PUT', {
    data: isNew ? { ...draft.data, kind, section_id: draft.record.section_id } : draft.data,
    project_id: state.projectId,
    expected_version: isNew ? null : draft.record.version, request_id: isNew ? id : '',
  });
  state.statements = state.statements.map(item => item.id === id ? saved : item);
  state.drafts.delete(id);
  renderTree(); renderTable();
  notice(`Saved ${saved.ref}.`);
}

async function editSection(existing = null) {
  const values = await kcuiForm(existing ? 'Edit section' : 'New section', {
    fields: [
      { name: 'title', label: 'Section name', value: existing?.title || '', required: true, maxLength: 300 },
      { name: 'parent_id', label: 'Parent section', type: 'select',
        value: existing ? existing.parent_id || '' : section(state.selected)?.id || '',
        options: [{ value: '', label: 'Top level' }, ...sectionOptions(existing?.id)] },
      { name: 'position', label: 'Order (lowest first)', type: 'number', value: existing?.position || 0, required: true },
    ],
    validate: values => Number.isInteger(Number(values.position)) && Number(values.position) >= 0 ? null : 'Order must be a non-negative integer.',
    confirmLabel: 'Save section',
  });
  if (!values) return;
  const saved = await api(`/api/records/section${existing ? `/${existing.id}` : ''}`, existing ? 'PUT' : 'POST', {
    project_id: state.projectId, data: { title: values.title, kind, parent_id: values.parent_id || null, position: Number(values.position) },
    expected_version: existing?.version ?? null, request_id: crypto.randomUUID(),
  });
  state.selected = saved.id;
  if (saved.parent_id) state.expanded.add(saved.parent_id);
  await load();
  notice(`Saved section ${saved.title}.`);
}

async function addStatement() {
  const values = await kcuiForm(`New ${kind}`, {
    message: `Table: ${section(state.selected) ? sectionPath(state.selected) : 'Unfiled'}`,
    fields: [{ name: 'title', label: 'Title', required: true, maxLength: 300 },
      { name: 'body', label, type: 'textarea' }],
    confirmLabel: 'Add to table',
  });
  if (!values) return;
  const record = {
    id: `new-${crypto.randomUUID()}`, ref: `New ${kind}`, isNew: true,
    ...values, kind, project_id: state.projectId,
    section_id: section(state.selected)?.id || null,
  };
  state.statements.push(record);
  state.drafts.set(record.id, { record, data: Object.fromEntries(editable.map(key => [key, record[key]])) });
  reveal(record.id);
  notice(`${label} added to the table. Use Save above to assign its ID and store it.`);
}

function reveal(id) {
  const item = findStatement(id);
  if (!item) throw new Error(`Linked ${kind} is unavailable.`);
  let parent = item.section_id;
  while (parent) { state.expanded.add(parent); parent = section(parent)?.parent_id; }
  state.selected = item.section_id || 'unfiled';
  $('search').value = '';
  renderTree(); renderTable(); updateActions();
  const row = document.getElementById(`${kind}-${id}`);
  row?.scrollIntoView({ block: 'nearest', inline: 'start' });
  row?.querySelector('[name=title]')?.focus();
  const url = new URL(location.href);
  if (item.isNew) url.searchParams.delete(kind);
  else url.searchParams.set(kind, id);
  url.searchParams.set('section', state.selected);
  history.replaceState(null, '', url);
}

async function discardDrafts() {
  if (state.drafts.size && !await kcuiConfirm(`Unsaved ${plural}`, 'Discard all unsaved table edits?')) return false;
  state.drafts.clear();
  return true;
}

$('projects').addEventListener('change', updateActions);
$('load-project').addEventListener('click', () => run(async () => {
  if (await discardDrafts()) location.assign(`${pagePath}?project=${encodeURIComponent($('projects').value)}`);
}));
$('refresh').addEventListener('click', () => run(async () => {
  if (await discardDrafts()) { await load(); notice('Refreshed.'); }
}));
$('new-section').addEventListener('click', () => run(() => editSection()));
$('edit-section').addEventListener('click', () => run(() => editSection(section(state.selected))));
$('delete-section').addEventListener('click', () => run(async () => {
  const item = section(state.selected);
  if (!item || !await kcuiConfirm('Delete section',
    `Permanently delete ${sectionPath(item.id)}, all nested sections and their ${plural}? ` +
    'Comments, history, links and unsaved edits inside these sections will also be removed. This cannot be undone.')) return;
  const deletedSections = new Set([item.id]);
  for (const id of deletedSections) {
    for (const child of state.sections.filter(candidate => candidate.parent_id === id)) deletedSections.add(child.id);
  }
  await api(`/api/records/section/${item.id}`, 'DELETE', { expected_version: item.version });
  for (const [id, draft] of state.drafts) {
    if (deletedSections.has(draft.record.section_id)) state.drafts.delete(id);
  }
  for (const id of deletedSections) state.expanded.delete(id);
  selectSection('all');
  await load();
  notice('Section and its contents permanently deleted.');
}));
$('new-requirement').addEventListener('click', () => run(addStatement));
$('save-all').addEventListener('click', () => run(async () => {
  for (const id of [...state.drafts.keys()]) await saveRow(id);
  notice(`All ${kind} changes saved.`);
}));
$('search').addEventListener('input', renderTable);
window.addEventListener('beforeunload', event => {
  if (state.drafts.size) { event.preventDefault(); event.returnValue = ''; }
});
run(async () => {
  await load();
  if (state.project && params.get(kind)) reveal(params.get(kind));
});
