import { initTopbar, initAppBar, kcuiForm, kcuiConfirm } from '/ui-elements/assets/js/chrome.js';
import { api, allRecords as all, allPages, node, projectTabs, getLoadedProject, setLoadedProject } from './client.js';

initTopbar({ currentService: 'koreproject' });

const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const initialProject = getLoadedProject(params);
const page = location.pathname === '/ui/tickets' ? 'tickets' :
  location.pathname === '/ui/boards' ? 'boards' :
  location.pathname === '/ui/settings' ? 'settings' : 'workspace';
document.title = `${page === 'boards' ? 'Boards' : page === 'tickets' ? 'Tickets' : page === 'settings' ? 'Data settings' : 'Project workspace'} - KoreProject`;
const state = { projects: [], statements: [], sections: [], links: [], tickets: [], boards: [], lanes: [],
  project: initialProject, selectedProject: initialProject,
  view: page,
  board: params.get('board') || '', generation: '', dirty: false, load: 0 };
const names = { project: 'Project', statement: 'Statement', ticket: 'Ticket', board: 'Board', lane: 'Lane' };
const listKey = kind => kind === 'project' ? 'projects' : `${kind}s`;
let busy = false;
let draggedTicket = null;
if (page === 'boards') {
  document.body.classList.add('kp-board-page');
  $('board-editor-dialog').append($('editor-panel'));
  document.querySelector('.kp-workspace').before($('notice'), $('storage-warning'));
  $('editor-panel').querySelector('.kcui-panel-header').append(button('Close', () => {
    if (discard()) clearEditor();
  }));
}

function notice(text, error = false) {
  for (const host of [$('notice'), $('board-editor-dialog').open ? $('editor-notice') : null].filter(Boolean)) {
    host.textContent = text;
    host.className = error ? 'kcui-field__error' : 'kcui-field__hint';
    host.setAttribute('role', error ? 'alert' : 'status');
  }
}

async function run(action) {
  if (busy) return;
  busy = true;
  updateProjectActions();
  document.querySelector('main').inert = true;
  $('editor-panel').inert = true;
  try { await action(); }
  catch (error) { notice(error.message, true); }
  finally {
    busy = false;
    document.querySelector('main').inert = false;
    $('editor-panel').inert = false;
    updateProjectActions();
  }
}

function button(text, action) {
  const result = node('button', text);
  result.type = 'button';
  result.addEventListener('click', () => run(action));
  return result;
}

function field(form, label, name, value = '', options = null, type = 'text') {
  const wrapper = node('label', label, type === 'checkbox' ? 'kcui-checkbox-label' : 'kcui-field');
  const control = node(options ? 'select' : type === 'textarea' ? 'textarea' : 'input');
  control.name = name;
  if (options) {
    for (const [id, title] of options) {
      const option = node('option', title);
      option.value = id;
      control.append(option);
    }
    if (value && !options.some(([id]) => id === value)) {
      const option = node('option', 'Unavailable or archived reference');
      option.value = value;
      control.append(option);
    }
  } else if (type !== 'textarea') control.type = type;
  if (type === 'checkbox') control.checked = Boolean(value);
  else control.value = value ?? '';
  if (type === 'number') { control.min = '0'; control.step = '1'; }
  if (name === 'title' || name === 'key') control.required = true;
  if (name === 'title') control.maxLength = 300;
  if (type === 'checkbox') wrapper.prepend(control);
  else wrapper.append(control);
  form.append(wrapper);
  return control;
}

const choices = values => values.map(value => [value, value.replaceAll('_', ' ')]);
const displayRef = (kind, record) => kind === 'project' ? record.key :
  kind === 'ticket' || kind === 'statement' ? record.ref : record.title;
const refs = (kind, records, selected = '') => records.filter(record => !record.archived || record.id === selected)
  .map(record => [record.id, `${displayRef(kind, record)}${kind === 'ticket' ? `: ${record.title}` : ''}${record.archived ? ' (archived)' : ''}`]);

function historySnapshot(entry, kind, record) {
  const snapshot = JSON.parse(entry.snapshot);
  const references = { project_id: 'project', parent_id: 'ticket', lane_id: 'lane', board_id: 'board',
    section_id: 'section', record_id: kind, source_id: snapshot.source_kind, target_id: snapshot.target_kind };
  const result = {};
  for (const [key, value] of Object.entries(snapshot)) {
    if (['id', 'previous_ref', 'generation', 'previous_generation'].includes(key)) continue;
    if (Object.hasOwn(references, key)) {
      const relatedKind = references[key];
      const related = state[listKey(relatedKind)]?.find(item => item.id === value);
      result[key.replace(/_id$/, '')] = value ? related ? displayRef(relatedKind, related) : 'Unavailable reference' : null;
    } else {
      result[key] = key === 'ref' ? displayRef(kind, record) : value;
    }
  }
  return result;
}
function discard() { return !state.dirty || window.confirm('Discard unsaved edits?'); }
function clearEditor() {
  state.dirty = false;
  $('board-editor-dialog').close();
  $('editor-notice').textContent = '';
  $('editor-panel').hidden = true;
  document.querySelector('.kp-workspace').classList.remove('has-editor');
  $('editor-title').textContent = 'Details';
  $('editor').replaceChildren(node('p', 'Select a record to edit or create a new record.'));
}

function updateProjectActions() {
  const selected = state.projects.find(project => project.id === state.selectedProject);
  $('new-project').disabled = busy;
  $('load-project').disabled = busy || !selected || selected.deletion_pending || selected.id === state.project;
  $('delete-project').disabled = busy || !selected || !state.generation;
}

async function reload() {
  const sequence = ++state.load;
  const projects = await all('project');
  if (sequence !== state.load) return;
  state.projects = projects;
  const unavailable = state.project && !projects.some(project => project.id === state.project && !project.deletion_pending);
  if (unavailable) {
    if (state.selectedProject === state.project) state.selectedProject = '';
    state.project = '';
    state.board = '';
    clearEditor();
  }
  setLoadedProject(state.project);
  if (!projects.some(project => project.id === state.selectedProject)) state.selectedProject = '';
  const selector = $('project-select');
  const placeholder = node('option', 'Select a project');
  placeholder.value = '';
  selector.replaceChildren(placeholder);
  for (const project of projects) {
    const option = node('option', `${project.key}: ${project.title}${project.deletion_pending ? ' (deletion pending)' : project.archived ? ' (archived)' : ''}`);
    option.value = project.id;
    selector.append(option);
  }
  selector.value = state.selectedProject;
  $('storage-warning').textContent = '';
  [state.statements, state.sections, state.links] = state.project
    ? await Promise.all([
      all('statement', state.project),
      page === 'workspace' ? all('section', state.project) : [],
      page === 'workspace' ? allPages(`/api/statement-links?project_id=${encodeURIComponent(state.project)}`) : [],
    ]) : [[], [], []];
  try {
    state.generation = (await api('/api/tickets/generation')).generation;
    [state.tickets, state.boards, state.lanes] = state.project
      ? await Promise.all(['ticket', 'board', 'lane'].map(kind => all(kind, state.project)))
      : [[], [], []];
  } catch (error) {
    state.generation = '';
    state.tickets = []; state.boards = []; state.lanes = [];
    $('storage-warning').textContent = `Ticket storage unavailable (not an empty backlog): ${error.message}`;
  }
  $('counts').textContent = `${state.statements.length} statements / ${state.generation ? state.tickets.length : '?'} tickets`;
  updateProjectActions();
  render();
  if (unavailable) notice('The loaded project is unavailable or pending deletion. Choose another project.', true);
}

function visible(record) {
  const query = $('search').value.toLowerCase();
  return ($('show-archived').checked || !record.archived) &&
    [record.ref, record.title, record.body, record.owner, record.labels].some(value => String(value || '').toLowerCase().includes(query));
}

function recordCard(kind, record) {
  const card = node('article', '', 'kcui-card kcui-stack kcui-stack--sm');
  card.dataset.recordRef = record.ref;
  const heading = node('div', '', 'kcui-toolbar');
  heading.append(button(displayRef(kind, record), () => open(kind, record.id)), node('strong', record.title, 'kp-record-title'));
  if (record.archived) heading.append(node('span', 'Archived', 'kcui-tag'));
  card.append(heading);
  if (kind === 'statement') {
    card.append(node('div', `${record.kind} / ${record.state} / v${record.version}`, 'kcui-field__hint'));
  } else if (kind === 'ticket') {
    const lane = state.lanes.find(item => item.id === record.lane_id);
    const parent = state.tickets.find(item => item.id === record.parent_id);
    card.append(node('div', `${record.priority} / ${record.owner || 'Unassigned owner'} / ${lane?.title || (record.lane_id ? 'Unavailable lane' : 'Backlog')}${parent ? ` / Parent: ${parent.ref}` : ''}`, 'kcui-field__hint'));
  }
  if (record.body) card.append(node('p', record.body.slice(0, 200), 'kp-record-copy'));
  if (page === 'boards' && kind === 'ticket') {
    const actions = node('div', '', 'kcui-toolbar');
    actions.append(button('Comment', async () => {
      await open('ticket', record.id);
      requestAnimationFrame(() => {
        const comment = $('editor').querySelector('[name="comment"]');
        comment.focus();
        comment.scrollIntoView({ block: 'center' });
      });
    }));
    if (!record.archived && boardEditable()) {
      actions.append(button('Move', async () => {
        const answer = await kcuiForm(`Move ${record.ref}`, {
          fields: [{ name: 'lane', label: 'Destination lane', type: 'select', value: record.lane_id || '',
            options: [{ value: '', label: 'Backlog' }, ...state.lanes
              .filter(lane => lane.board_id === state.board && !lane.archived).sort(order)
              .map(lane => ({ value: lane.id, label: lane.title }))] }],
          confirmLabel: 'Move ticket',
        });
        if (answer) {
          const lane = answer.lane ? state.lanes.find(item => item.id === answer.lane) : null;
          if (answer.lane && !lane) throw new Error('Destination lane is unavailable. Refresh the board.');
          await moveTicket(record, lane);
        }
      }));
      card.draggable = true;
      card.classList.add('kp-draggable');
      card.addEventListener('dragstart', event => {
        if (busy) { event.preventDefault(); return; }
        draggedTicket = record;
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', record.ref);
        card.classList.add('kp-dragging');
      });
      card.addEventListener('dragend', () => {
        draggedTicket = null;
        card.classList.remove('kp-dragging');
        document.querySelectorAll('.kp-drop-target').forEach(panel => panel.classList.remove('kp-drop-target'));
      });
    }
    card.append(actions);
  }
  return card;
}

function boardEditable() {
  return state.boards.some(board => board.id === state.board && !board.archived) &&
    state.projects.some(project => project.id === state.project && !project.archived);
}

async function moveTicket(ticket, lane) {
  if (ticket.lane_id === (lane?.id || null)) return;
  let confirmChildren = false;
  if (lane && ['done', 'cancelled'].includes(lane.category)) {
    const unfinished = state.tickets.some(child => child.parent_id === ticket.id && !child.archived &&
      !state.lanes.some(item => item.id === child.lane_id && ['done', 'cancelled'].includes(item.category)));
    if (unfinished) {
      confirmChildren = await kcuiConfirm('Unfinished children',
        `${ticket.ref} has unfinished children. Move this parent to ${lane.title} anyway?`, { confirmLabel: 'Move ticket' });
      if (!confirmChildren) return;
    }
  }
  await api(`/api/records/ticket/${ticket.id}`, 'PUT', {
    data: { lane_id: lane?.id || null }, expected_version: ticket.version,
    generation: state.generation, confirm_children: confirmChildren,
  });
  await reload();
  notice(`Moved ${ticket.ref} to ${lane?.title || 'Backlog'}.`);
}

function render() {
  initAppBar({ brandLabel: 'KoreProject', brandIcon: 'koreproject', currentService: 'koreproject',
    tabs: projectTabs(state.project, page) });
  const url = new URL(location.href);
  if (state.project) url.searchParams.set('project', state.project);
  else url.searchParams.delete('project');
  url.searchParams.delete('view');
  history.replaceState(null, '', url);
  const actions = $('view-actions'), records = $('records');
  actions.replaceChildren(); records.replaceChildren();
  records.classList.toggle('kp-dashboard', page === 'workspace');
  records.classList.toggle('kp-boards', state.view === 'boards');
  $('search-controls').hidden = !['tickets', 'boards'].includes(page);
  const loaded = state.projects.find(project => project.id === state.project);
  const heading = page === 'workspace' ? 'Project workspace' : page === 'settings' ? 'Data settings' :
    page === 'boards' ? 'Boards' : 'Tickets';
  $('workspace-title').textContent = loaded ? `${loaded.key}: ${loaded.title} / ${heading}` : heading;
  actions.append(button('Refresh', async () => { if (discard()) { clearEditor(); await reload(); notice('Refreshed.'); } }));
  if (loaded && page !== 'boards') actions.append(button('Edit project', () => open('project', loaded.id)));
  if (state.view === 'settings') { renderSettings(records); return; }
  if (!state.project) { records.append(node('p', 'Create a new project or select a project and choose Load.')); return; }
  if (page === 'workspace') { renderDashboard(records); return; }
  if (!state.generation) { records.append(node('p', 'Restore ticket storage to use this view. Project definitions remain available.')); return; }
  actions.append(button('New ticket', () => edit('ticket', {})));
  if (state.view === 'tickets') {
    const visited = new Set();
    function walk(parent, depth) {
      for (const ticket of state.tickets.filter(item => item.parent_id === parent).sort(order)) {
        if (visited.has(ticket.id)) continue;
        visited.add(ticket.id);
        if (visible(ticket)) {
          const card = recordCard('ticket', ticket);
          card.classList.add('kp-ticket-row');
          card.style.setProperty('--kp-depth', Math.min(depth, 6));
          records.append(card);
        }
        walk(ticket.id, depth + 1);
      }
    }
    walk(null, 0);
    for (const ticket of state.tickets.filter(item => !visited.has(item.id))) {
      if (visible(ticket)) {
        const card = recordCard('ticket', ticket);
        card.append(node('p', 'Parent is unavailable. Restore its backup or reparent this ticket.', 'kcui-field__error'));
        records.append(card);
      }
    }
    if (!records.children.length) records.append(node('p', 'No tickets match this view.'));
    return;
  }
  actions.append(button('New board', () => edit('board', {})));
  const boards = state.boards.filter(record => $('show-archived').checked || !record.archived);
  if (!boards.some(board => board.id === state.board)) state.board = boards[0]?.id || '';
  if (state.board) url.searchParams.set('board', state.board);
  else url.searchParams.delete('board');
  history.replaceState(null, '', url);
  const select = field(actions, 'Board', 'board', state.board, boards.map(board => [board.id, board.title]));
  select.addEventListener('change', () => { state.board = select.value; render(); });
  if (state.board) {
    actions.append(button('Edit board', () => open('board', state.board)),
      button('New lane', () => edit('lane', { board_id: state.board })));
  }
  const lanes = state.lanes.filter(lane => lane.board_id === state.board && ($('show-archived').checked || !lane.archived)).sort(order);
  for (const lane of [null, ...lanes]) {
    const panel = node('section', '', 'kcui-panel');
    panel.setAttribute('aria-label', `${lane?.title || 'Backlog'} lane`);
    if (boardEditable() && !lane?.archived) {
      panel.addEventListener('dragover', event => {
        if (!draggedTicket || busy || draggedTicket.lane_id === (lane?.id || null)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        panel.classList.add('kp-drop-target');
      });
      panel.addEventListener('dragleave', event => {
        if (!panel.contains(event.relatedTarget)) panel.classList.remove('kp-drop-target');
      });
      panel.addEventListener('drop', event => {
        event.preventDefault();
        panel.classList.remove('kp-drop-target');
        const ticket = draggedTicket;
        draggedTicket = null;
        if (ticket && !busy) run(() => moveTicket(ticket, lane));
      });
    }
    const header = node('div', '', 'kcui-panel-header');
    header.append(node('h3', lane?.title || 'Backlog', 'kcui-panel-title'));
    if (lane) header.append(button('Edit lane', () => open('lane', lane.id)));
    const body = node('div', '', 'kcui-panel-body kp-lane-tickets');
    const tickets = state.tickets.filter(ticket => ticket.lane_id === (lane?.id || null) && visible(ticket)).sort(order);
    body.append(...tickets.map(ticket => recordCard('ticket', ticket)));
    if (!tickets.length) body.append(node('span', 'No tickets', 'kcui-field__hint'));
    panel.append(header, body); records.append(panel);
  }
}

function renderDashboard(host) {
  const requirements = state.statements.filter(item => item.kind === 'requirement');
  const designs = state.statements.filter(item => item.kind === 'design');
  const requirementIds = new Set(requirements.map(item => item.id));
  const designIds = new Set(designs.map(item => item.id));
  const satisfaction = state.links.filter(link => link.relation === 'satisfies' &&
    requirementIds.has(link.target_id) && designIds.has(link.source_id));
  const covered = new Set(satisfaction.map(link => link.target_id)).size;
  const linkedDesigns = new Set(satisfaction.map(link => link.source_id)).size;
  function card(key, title, metrics, links, message = '') {
    const panel = node('section', '', 'kcui-card kcui-stack kcui-stack--sm');
    panel.dataset.subsection = key;
    panel.append(node('h2', title));
    if (message) panel.append(node('p', message, 'kcui-field__error'));
    const list = node('dl', '', 'kp-metrics');
    for (const [label, value] of metrics) list.append(node('dt', label), node('dd', String(value)));
    panel.append(list);
    const actions = node('div', '', 'kcui-toolbar');
    for (const [label, path] of links) {
      const link = node('a', label, 'kcui-btn');
      link.href = `${path}${path.includes('?') ? '&' : '?'}project=${encodeURIComponent(state.project)}`;
      actions.append(link);
    }
    panel.append(actions);
    host.append(panel);
  }
  card('requirements', 'Requirements', [
    ['Requirements', requirements.length],
    ['Sections', state.sections.filter(item => item.kind === 'requirement' && !item.archived).length],
    ['Linked to design', covered], ['Without design links', requirements.length - covered],
  ], [['Open requirements', '/ui/requirements']]);
  card('design', 'Design', [
    ['Design statements', designs.length],
    ['Sections', state.sections.filter(item => item.kind === 'design' && !item.archived).length],
    ['Linked to requirements', linkedDesigns], ['Without requirement links', designs.length - linkedDesigns],
  ], [['Open design', '/ui/design']]);
  const activeTickets = state.tickets.filter(item => !item.archived);
  const lanes = new Map(state.lanes.map(item => [item.id, item.category]));
  const category = ticket => ticket.lane_id ? lanes.get(ticket.lane_id) || 'unavailable' : 'backlog';
  const count = status => activeTickets.filter(ticket => category(ticket) === status).length;
  const ticketMetrics = state.generation ? [
    ['Tickets', state.tickets.length], ['Backlog', count('backlog')],
    ['Open', count('open')], ['In progress', count('active')],
    ['Done', count('done')], ['Cancelled', count('cancelled')],
    ['Boards', state.boards.filter(item => !item.archived).length],
    ['Archived tickets', state.tickets.length - activeTickets.length],
  ] : [];
  if (state.generation && count('unavailable')) ticketMetrics.push(['Unavailable lane', count('unavailable')]);
  card('tickets', 'Tickets and boards', ticketMetrics,
    [['Open tickets', '/ui/tickets'], ['Open boards', '/ui/boards']],
    state.generation ? '' : 'Ticket storage unavailable. Counts cannot be determined.');
}

function order(a, b) { return a.position - b.position || a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id); }

async function open(kind, id) {
  if (!discard()) return;
  const record = await api(`/api/records/${kind}/${id}`);
  if (kind === 'statement') {
    state.dirty = false;
    const path = record.kind === 'design' ? 'design' : 'requirements';
    location.assign(`/ui/${path}?project=${encodeURIComponent(record.project_id)}&${record.kind}=${encodeURIComponent(id)}`);
    return;
  }
  edit(kind, record, true);
}

function edit(kind, record, confirmed = false) {
  if (!confirmed && !discard()) return;
  clearEditor();
  $('editor-panel').hidden = false;
  if (page === 'boards') $('board-editor-dialog').showModal();
  else document.querySelector('.kp-workspace').classList.add('has-editor');
  $('editor-title').textContent = record.id ? `${names[kind]} ${displayRef(kind, record)}` : `New ${names[kind].toLowerCase()}`;
  const editor = $('editor'), form = node('form');
  editor.replaceChildren(form);
  const title = field(form, 'Title', 'title', record.title);
  const controls = { title, body: field(form, 'Description / statement', 'body', record.body, null, 'textarea') };
  if (kind === 'project') {
    controls.key = field(form, 'Permanent project key (2-16 characters)', 'key', record.key);
    controls.key.readOnly = Boolean(record.id);
  }
  if (kind === 'statement') {
    controls.kind = field(form, 'Kind', 'kind', record.kind || 'requirement', choices(['requirement', 'design']));
    controls.kind.disabled = Boolean(record.id);
    controls.state = field(form, 'Review state', 'state', record.state || 'draft', choices(['draft', 'in_review', 'approved', 'retired']));
    controls.owner = field(form, 'Owner', 'owner', record.owner);
    form.append(node('p', 'Editing approved content creates a new draft. Previous versions remain in history.', 'kcui-field__hint'));
  }
  if (kind === 'ticket') {
    controls.parent_id = field(form, 'Parent ticket', 'parent_id', record.parent_id,
      [['', 'No parent'], ...refs('ticket', state.tickets.filter(item => item.id !== record.id), record.parent_id)]);
    controls.lane_id = field(form, 'Board / lifecycle lane', 'lane_id', record.lane_id,
      [['', 'Backlog'], ...state.lanes.filter(item => !item.archived && state.boards.some(board => board.id === item.board_id && !board.archived))
        .sort(order).map(lane => [lane.id, `${state.boards.find(board => board.id === lane.board_id)?.title || 'Unavailable board'} / ${lane.title}`])]);
    controls.priority = field(form, 'Priority', 'priority', record.priority || 'normal', choices(['low', 'normal', 'high', 'urgent']));
    controls.owner = field(form, 'Owner', 'owner', record.owner);
    controls.labels = field(form, 'Labels (comma separated)', 'labels', record.labels);
  }
  if (kind === 'lane') {
    controls.board_id = field(form, 'Board', 'board_id', record.board_id || state.board, refs('board', state.boards));
    controls.board_id.disabled = Boolean(record.id);
    controls.category = field(form, 'Lifecycle category', 'category', record.category || 'open', choices(['open', 'active', 'done', 'cancelled']));
  }
  if (kind === 'ticket' || kind === 'lane') controls.position = field(form, 'Order (lowest first)', 'position', record.position || 0, null, 'number');
  if (record.id) controls.archived = field(form, 'Archived', 'archived', record.archived, null, 'checkbox');
  const confirmChildren = kind === 'ticket' ? field(form, 'Allow closing this parent with unfinished children', 'confirm_children', false, null, 'checkbox') : null;
  const toolbar = node('div', '', 'kcui-toolbar');
  const save = node('button', 'Save'); save.type = 'submit';
  toolbar.append(save, button('Cancel', () => { if (discard()) clearEditor(); }));
  form.append(toolbar);
  form.addEventListener('input', () => { state.dirty = true; });
  const requestId = crypto.randomUUID();
  form.addEventListener('submit', event => {
    event.preventDefault();
    run(async () => {
      const data = Object.fromEntries(Object.entries(controls).map(([key, control]) =>
        [key, control.type === 'checkbox' ? control.checked : control.type === 'number' ? Number(control.value) : control.value]));
      const saved = await api(`/api/records/${kind}${record.id ? `/${record.id}` : ''}`, record.id ? 'PUT' : 'POST',
        { data, project_id: state.project, expected_version: record.id ? record.version : null,
          request_id: requestId, generation: state.generation, confirm_children: Boolean(confirmChildren?.checked) });
      state.dirty = false;
      if (kind === 'project') {
        state.project = saved.id;
        state.selectedProject = saved.id;
      }
      await reload();
      edit(kind, await api(`/api/records/${kind}/${saved.id}`), true);
      notice(`Saved ${displayRef(kind, saved)} (version ${saved.version}).`);
    });
  });
  if (record.id) renderDetails(editor, kind, record);
  requestAnimationFrame(() => title.focus());
}

function renderDetails(editor, kind, record) {
  if (record.ticket_store_error) editor.append(node('p', `Ticket links unavailable: ${record.ticket_store_error}`, 'kcui-field__error'));
  if (kind === 'statement' || kind === 'ticket') {
    editor.append(node('h3', 'Linked context'));
    for (const link of record.links || []) {
      const outgoing = link.source_id === record.id;
      const otherKind = outgoing ? link.target_kind : link.source_kind;
      const otherId = outgoing ? link.target_id : link.source_id;
      const other = state[listKey(otherKind)].find(item => item.id === otherId);
      const row = node('div', '', 'kcui-toolbar');
      row.append(node('span', `${outgoing ? 'Outgoing' : 'Incoming'}: ${link.relation.replaceAll('_', ' ')}`));
      row.append(button(other ? displayRef(otherKind, other) : 'Unavailable reference', () => open(otherKind, otherId)));
      row.append(node('span', other ? other.title : 'Record is unavailable', 'kp-record-title'));
      row.append(button('Remove link', async () => {
        if (!window.confirm('Remove this link?')) return;
        const store = link.source_kind === 'statement' && link.target_kind === 'statement' ? 'statement' : 'ticket';
        await api(`/api/links/${store}/${link.id}?generation=${encodeURIComponent(state.generation)}`, 'DELETE');
        await open(kind, record.id);
      }));
      editor.append(row);
      if (link.note) editor.append(node('p', link.note, 'kp-record-copy'));
    }
    const linkForm = node('form');
    const relationship = field(linkForm, 'New outgoing relationship', 'relation', 'relates_to',
      choices(['relates_to', ...(kind === 'ticket' ? ['addresses', 'depends_on'] : ['satisfies', 'refines'])]));
    const targets = [...state.statements.map(item => ['statement', item]), ...state.tickets.map(item => ['ticket', item])]
      .filter(([, item]) => item.id !== record.id && !item.archived);
    const target = field(linkForm, 'Target record', 'target', '', [['', 'Choose target'],
      ...targets.map(([type, item]) => [`${type}:${item.id}`, `${item.ref}: ${item.title}`])]);
    target.required = true;
    const note = field(linkForm, 'Link note / evidence reference', 'note');
    const add = node('button', 'Add link'); add.type = 'submit'; linkForm.append(add);
    linkForm.addEventListener('submit', event => {
      event.preventDefault();
      run(async () => {
        if (!discard()) return;
        const [targetKind, targetId] = target.value.split(':');
        await api('/api/links', 'POST', { source_kind: kind, source_id: record.id,
          target_kind: targetKind, target_id: targetId, relation: relationship.value, note: note.value,
          generation: state.generation });
        state.dirty = false;
        await open(kind, record.id);
        notice('Link added.');
      });
    });
    editor.append(linkForm);
  }
  editor.append(node('h3', 'Comments'));
  for (const comment of record.comments || []) {
    editor.append(node('div', `${comment.actor} / ${comment.created_at}`, 'kcui-field__hint'),
      node('p', comment.body, 'kp-record-copy'));
  }
  const commentForm = node('form');
  const comment = field(commentForm, 'New comment / evidence reference', 'comment', '', null, 'textarea');
  comment.required = true; comment.maxLength = 20000;
  const addComment = node('button', 'Add comment'); addComment.type = 'submit'; commentForm.append(addComment);
  commentForm.addEventListener('submit', event => {
    event.preventDefault();
    run(async () => {
      if (!discard()) return;
      await api(`/api/records/${kind}/${record.id}/comments`, 'POST', { body: comment.value, generation: state.generation });
      state.dirty = false; await open(kind, record.id); notice('Comment added.');
    });
  });
  editor.append(commentForm, node('h3', 'Activity and revisions (latest 100)'));
  const history = node('div', '', 'kp-history kcui-stack kcui-stack--sm');
  for (const entry of record.history || []) {
    const details = node('details');
    details.append(node('summary', `${entry.created_at} / ${entry.actor} / ${entry.action}`));
    details.append(node('pre', JSON.stringify(historySnapshot(entry, kind, record), null, 2), 'kp-record-copy'));
    history.append(details);
  }
  editor.append(history);
}

function renderSettings(records) {
  records.append(node('h2', 'Separate data lifecycles'),
    node('p', 'projects.db retains projects, statements, revisions, statement links and statement comments.'),
    node('p', 'tickets.db holds tickets, hierarchy, boards, lanes, work links, comments and activity. Reset applies to ALL projects, not just the selected project.'),
    node('p', 'Back up tickets.db before resetting if this work may be needed again. The reset does not delete project definitions.'),
    node('p', 'For a consistent backup, stop KoreProject before copying both databases and any associated WAL files.'));
  if (!state.generation) {
    records.append(node('p', 'Ticket storage is unavailable. Stop the service and restore a valid ticket database before resetting.', 'kcui-field__error'));
    return;
  }
  const form = node('form');
  const confirmation = field(form, 'Type RESET ALL TICKETS to confirm', 'confirmation');
  const reset = node('button', 'Reset all ticket data'); reset.type = 'submit'; form.append(reset);
  form.addEventListener('submit', event => {
    event.preventDefault();
    run(async () => {
      if (!discard()) return;
      await api('/api/tickets/reset', 'POST', { confirmation: confirmation.value, generation: state.generation });
      clearEditor(); await reload(); notice('All ticket data reset. Project definitions are unchanged.');
    });
  });
  records.append(form);
}

$('new-project').addEventListener('click', () => edit('project', {}));
$('load-project').addEventListener('click', () => run(async () => {
  if (!discard()) return;
  const selected = await api(`/api/records/project/${state.selectedProject}`);
  if (selected.deletion_pending) throw new Error('Project deletion is pending. Retry Delete to finish it.');
  state.project = selected.id;
  state.board = '';
  clearEditor();
  await reload();
  notice(`Loaded ${selected.key}: ${selected.title}.`);
}));
$('delete-project').addEventListener('click', () => run(async () => {
  const selected = state.projects.find(project => project.id === state.selectedProject);
  if (!selected || (selected.id === state.project && !discard())) return;
  const answer = await kcuiForm('Delete project', {
    message: `Permanently delete ${selected.key}: ${selected.title}?\n\nThis removes all its statements, revisions, tickets, boards, lanes, links, comments and history. Other projects are not affected. This cannot be undone.`,
    fields: [{ name: 'confirmation', label: `Type DELETE ${selected.key} to confirm`, required: true }],
    confirmLabel: 'Delete permanently',
    danger: true,
    validate: values => values.confirmation === `DELETE ${selected.key}` ? null : `Type DELETE ${selected.key} to confirm.`,
  });
  if (!answer) return;
  try {
    await api(`/api/projects/${selected.id}`, 'DELETE', { expected_version: selected.version, confirmation: answer.confirmation });
  } catch (error) {
    await reload();
    throw error;
  }
  if (state.project === selected.id) { state.project = ''; clearEditor(); }
  state.selectedProject = '';
  await reload();
  notice(`Permanently deleted ${selected.key}: ${selected.title}.`);
}));
$('project-select').addEventListener('change', () => {
  state.selectedProject = $('project-select').value;
  updateProjectActions();
});
$('board-editor-dialog').addEventListener('cancel', event => {
  event.preventDefault();
  if (!busy && discard()) clearEditor();
});
$('search').addEventListener('input', render);
$('show-archived').addEventListener('change', render);
window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
run(async () => {
  await reload();
  if (params.get('statement') && state.project) await open('statement', params.get('statement'));
  if (params.get('ticket') && ['tickets', 'boards'].includes(page) && state.project) await open('ticket', params.get('ticket'));
});
