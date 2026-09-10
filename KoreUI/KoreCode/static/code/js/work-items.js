import { api } from './state.js';
import { kcuiForm } from '/ui-elements/assets/js/dialogs.js';

const _ACTIVE_WORK_ITEM_KEY = 'korecode:active-work-item';

export function initWorkItems() {
  const select        = document.getElementById('work-item-select');
  const status        = document.getElementById('work-item-status');
  const newButton     = document.getElementById('btn-new-work-item');
  const refreshButton = document.getElementById('btn-refresh-work-items');
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

  newButton.addEventListener('click', async () => {
    const values = await kcuiForm('New work item', {
      message: 'Describe the outcome and the context needed to complete it.',
      confirmLabel: 'Create work item',
      fields: [
        {
          name:      'title',
          label:     'Outcome',
          required:  true,
          maxLength: 160,
          placeholder: 'Diagnose and fix the failing test',
        },
        {
          name:        'description',
          label:       'Context',
          type:        'textarea',
          rows:        4,
          placeholder: 'What should be true when this work is complete?',
        },
      ],
    });
    if (!values) return;
    try {
      await create(values.title, values.description);
    } catch (error) {
      _showError(error);
    }
  });
  refreshButton.addEventListener('click', () => { void refresh().catch(_showError); });

  return {
    refresh,
    getActiveWorkItemId: () => activeItemId,
  };
}
