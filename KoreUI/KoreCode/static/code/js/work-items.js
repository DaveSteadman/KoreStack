import { api } from './state.js';

const _ACTIVE_WORK_ITEM_KEY = 'korecode:active-work-item';

export function initWorkItems() {
  const select        = document.getElementById('work-item-select');
  const status        = document.getElementById('work-item-status');
  const newButton     = document.getElementById('btn-new-work-item');
  const refreshButton = document.getElementById('btn-refresh-work-items');
  const dialog         = document.getElementById('work-item-dialog');
  const form           = document.getElementById('work-item-form');
  const titleInput     = document.getElementById('work-item-title-input');
  const descriptionInput = document.getElementById('work-item-description-input');
  const cancelButton  = document.getElementById('btn-cancel-work-item');
  let items           = [];
  let activeItemId    = _loadActiveId();

  function _showError(error) {
    const message = error instanceof Error ? error.message : String(error || 'Unknown error');
    if (typeof window.kcuiAlert === 'function') {
      void window.kcuiAlert('Work Item Error', message);
      return;
    }
    console.error('Work item error:', message);
  }

  function _loadActiveId() {
    try {
      return localStorage.getItem(_ACTIVE_WORK_ITEM_KEY) || null;
    } catch (_) {
      return null;
    }
  }

  function _saveActiveId() {
    try {
      if (activeItemId) localStorage.setItem(_ACTIVE_WORK_ITEM_KEY, activeItemId);
      else localStorage.removeItem(_ACTIVE_WORK_ITEM_KEY);
    } catch (_) {}
  }

  function _activeItem() {
    return items.find((item) => item.work_item_id === activeItemId) || null;
  }

  function _render() {
    const previous = activeItemId;
    select.replaceChildren();
    const none = document.createElement('option');
    none.value = '';
    none.textContent = 'No active work item';
    select.append(none);

    for (const item of items) {
      const option = document.createElement('option');
      option.value = item.work_item_id;
      option.textContent = `${item.status.replaceAll('_', ' ')}: ${item.title}`;
      select.append(option);
    }

    if (!items.some((item) => item.work_item_id === previous)) activeItemId = null;
    select.value = activeItemId || '';
    const active = _activeItem();
    status.value = active?.status || 'scoping';
    status.disabled = !active;
    _saveActiveId();
  }

  async function refresh() {
    const payload = await api('/api/work-items');
    items = Array.isArray(payload.work_items) ? payload.work_items : [];
    _render();
  }

  async function create(title, description) {
    const item = await api('/api/work-items', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ title, description }),
    });
    activeItemId = item.work_item_id;
    await refresh();
  }

  select.addEventListener('change', () => {
    activeItemId = select.value || null;
    _render();
  });

  status.addEventListener('change', async () => {
    if (!activeItemId) return;
    try {
      const item = await api(`/api/work-items/${encodeURIComponent(activeItemId)}`, {
        method:  'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ status: status.value }),
      });
      items = items.map((current) => current.work_item_id === item.work_item_id ? item : current);
      _render();
    } catch (error) {
      _showError(error);
      _render();
    }
  });

  newButton.addEventListener('click', () => {
    form.reset();
    dialog.showModal();
    titleInput.focus();
  });
  cancelButton.addEventListener('click', () => dialog.close());
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const title = titleInput.value.trim();
    if (!title) return;
    void create(title, descriptionInput.value)
      .then(() => dialog.close())
      .catch(_showError);
  });
  refreshButton.addEventListener('click', () => { void refresh().catch(_showError); });

  return {
    refresh,
    getActiveWorkItemId: () => activeItemId,
  };
}
