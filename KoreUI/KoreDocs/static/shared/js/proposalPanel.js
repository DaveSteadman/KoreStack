import { fetchWithAuth } from '/static/shared/js/auth.js';

async function _request(url, options = {}) {
  const response = await fetchWithAuth(url, {
    headers: { Accept: 'application/json', ...options.headers },
    ...options,
  });
  const raw = await response.text();
  let payload = {};
  try {
    payload = raw ? JSON.parse(raw) : {};
  } catch {
    payload = { detail: raw };
  }
  if (!response.ok) {
    throw new Error(payload.detail || `Request failed with status ${response.status}`);
  }
  return payload;
}

export function initProposalPanel({ getFileId, isDirty, reloadCurrent }) {
  const root = document.getElementById('agent-proposals');
  if (!root) return;

  const status = document.createElement('p');
  status.className = 'agent-proposal-status hint';
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  const list = document.createElement('div');
  list.className = 'agent-proposal-list';
  const detail = document.createElement('div');
  detail.className = 'agent-proposal-detail';
  root.replaceChildren(status, list, detail);

  let selectedId = null;

  function showError(action, error) {
    status.textContent = `${action}: ${error?.message || 'Unknown error'}`;
  }

  function renderDetail(proposal) {
    detail.replaceChildren();
    const heading = document.createElement('strong');
    heading.textContent = proposal.summary;
    const revision = document.createElement('p');
    revision.className = 'hint';
    revision.textContent = `Based on revision ${proposal.base_revision}`;
    const diff = document.createElement('pre');
    diff.className = 'agent-proposal-diff';
    diff.textContent = proposal.diff || 'No differences.';

    const actions = document.createElement('div');
    actions.className = 'agent-proposal-actions';
    const accept = document.createElement('button');
    accept.type = 'button';
    accept.textContent = 'Apply proposal';
    accept.disabled = isDirty();
    accept.title = accept.disabled ? 'Save or discard local changes before applying a proposal.' : '';
    accept.addEventListener('click', async () => {
      if (isDirty()) {
        status.textContent = 'Save or discard local changes before applying a proposal.';
        return;
      }
      accept.disabled = true;
      try {
        await _request(`/api/proposals/${encodeURIComponent(proposal.id)}/accept`, { method: 'POST' });
        try {
          await reloadCurrent();
        } catch (error) {
          showError('Proposal applied, but the file could not be refreshed', error);
          return;
        }
        selectedId = null;
        detail.replaceChildren();
        status.textContent = 'Proposal applied.';
        await refresh();
      } catch (error) {
        showError('Could not apply proposal', error);
        accept.disabled = isDirty();
      }
    });

    const reject = document.createElement('button');
    reject.type = 'button';
    reject.textContent = 'Reject';
    reject.addEventListener('click', async () => {
      reject.disabled = true;
      try {
        await _request(`/api/proposals/${encodeURIComponent(proposal.id)}/reject`, { method: 'POST' });
        selectedId = null;
        detail.replaceChildren();
        status.textContent = 'Proposal rejected.';
        await refresh();
      } catch (error) {
        showError('Could not reject proposal', error);
        reject.disabled = false;
      }
    });

    actions.append(accept, reject);
    detail.append(heading, revision, diff, actions);
  }

  async function loadProposal(proposalId) {
    selectedId = proposalId;
    status.textContent = 'Loading proposal…';
    try {
      const proposal = await _request(`/api/proposals/${encodeURIComponent(proposalId)}`);
      renderDetail(proposal);
      status.textContent = '';
    } catch (error) {
      showError('Could not load proposal', error);
    }
  }

  async function refresh() {
    const fileId = getFileId();
    if (fileId == null) {
      status.textContent = 'Open a document or sheet to review proposals.';
      list.replaceChildren();
      detail.replaceChildren();
      return;
    }
    try {
      const proposals = await _request(`/api/proposals?file_id=${encodeURIComponent(fileId)}`);
      list.replaceChildren();
      if (!proposals.length) {
        status.textContent = 'No pending Agent proposals.';
        detail.replaceChildren();
        selectedId = null;
        return;
      }
      status.textContent = '';
      for (const proposal of proposals) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'agent-proposal-item';
        button.textContent = proposal.summary;
        button.setAttribute('aria-pressed', String(proposal.id === selectedId));
        button.addEventListener('click', () => loadProposal(proposal.id));
        list.appendChild(button);
      }
      if (selectedId && !proposals.some(proposal => proposal.id === selectedId)) {
        selectedId = null;
        detail.replaceChildren();
      }
    } catch (error) {
      showError('Could not load proposals', error);
    }
  }

  void refresh();
  window.setInterval(() => {
    if (!document.hidden) void refresh();
  }, 5000);
}
