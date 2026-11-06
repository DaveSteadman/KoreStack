const _messages = {
  idle: 'Ready',
  loading: 'Loading…',
  dirty: 'Unsaved changes',
  saving: 'Saving…',
  saved: 'Saved',
  conflict: 'Resolving conflict…',
  error: 'Save failed',
};

export function initKorefileSaveStatus(retrySave) {
  if (document.getElementById('koredocs-save-status')) return;

  const status = document.createElement('div');
  status.id = 'koredocs-save-status';
  status.className = 'kd-save-status';
  status.dataset.state = 'idle';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');

  const label = document.createElement('span');
  const render = state => {
    status.dataset.state = state;
    label.textContent = state === 'error' && !navigator.onLine
      ? 'Offline — save failed'
      : _messages[state];
    retry.hidden = state !== 'error';
  };
  label.textContent = _messages.idle;
  status.appendChild(label);

  const retry = document.createElement('button');
  retry.type = 'button';
  retry.textContent = 'Retry';
  retry.hidden = true;
  retry.addEventListener('click', () => retrySave());
  status.appendChild(retry);

  document.body.appendChild(status);
  document.addEventListener('kd:korefile-save-state', event => {
    const state = _messages[event.detail?.state] ? event.detail.state : 'idle';
    render(state);
  });
  window.addEventListener('online', () => {
    if (status.dataset.state === 'error') render('error');
  });
  window.addEventListener('offline', () => {
    if (status.dataset.state === 'error') render('error');
  });
}
