import {
  esc,
  renderAssistantText,
  extractCodeForActions,
  extractStructuredEdits,
} from './rendering.js';

export function createThreadUI({ thread, insertFromChat = null, createEditProposal = null, applyEditProposal = null, reloadTabs = null, saveTabs = null }) {
  function scrollBottom() {
    thread.scrollTop = thread.scrollHeight;
  }

  async function copyText(text, btn) {
    const prev = btn.textContent;
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = 'copied';
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', 'readonly');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
        btn.textContent = 'copied';
      } finally {
        document.body.removeChild(ta);
      }
    }
    setTimeout(() => {
      btn.textContent = prev;
    }, 1000);
  }

  function buildAssistantActions(codeText, structured = null) {
    const row = document.createElement('div');
    row.className = 'chat-msg-actions';

    if (structured && Array.isArray(structured.edits)) {
      const files = [...new Set(structured.edits.map((e) => String(e?.file || '').trim()).filter(Boolean))];
      const summary = document.createElement('span');
      summary.className = 'kcui-tag kcui-tag--muted';
      summary.textContent = `${structured.edits.length} edit${structured.edits.length === 1 ? '' : 's'}${files.length ? ` · ${files.slice(0, 2).join(', ')}${files.length > 2 ? '…' : ''}` : ''}`;
      row.appendChild(summary);
    }

    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'kcui-tag kcui-tag--info';
    copyBtn.textContent = 'copy';
    copyBtn.addEventListener('click', () => {
      void copyText(codeText, copyBtn);
    });

    const insertBtn = document.createElement('button');
    insertBtn.type = 'button';
    insertBtn.className = 'kcui-tag kcui-tag--accent';
    insertBtn.textContent = 'insert';
    insertBtn.disabled = typeof insertFromChat !== 'function';
    insertBtn.addEventListener('click', () => {
      if (typeof insertFromChat !== 'function') return;
      const inserted = insertFromChat(codeText);
      const prev = insertBtn.textContent;
      insertBtn.textContent = inserted ? 'inserted' : 'no file';
      setTimeout(() => {
        insertBtn.textContent = prev;
      }, 1000);
    });

    row.appendChild(copyBtn);
    row.appendChild(insertBtn);

    if (structured && Array.isArray(structured.edits) && typeof createEditProposal === 'function' && typeof applyEditProposal === 'function') {
      const applyBtn = document.createElement('button');
      applyBtn.type = 'button';
      applyBtn.className = 'kcui-tag kcui-tag--warning';
      applyBtn.textContent = 'apply proposal';
      applyBtn.addEventListener('click', async () => {
        const prev = applyBtn.textContent;
        const fileList = [...new Set(structured.edits.map((e) => String(e?.file || '').trim()).filter(Boolean))];
        const prompt = `Create and apply proposal for ${structured.edits.length} edit(s) across ${fileList.length || 1} file(s)?`;
        const confirmed = await window.kcuiConfirm('Apply Proposal', prompt, { confirmLabel: 'Apply' });
        if (!confirmed) return;
        applyBtn.disabled = true;
        try {
          const proposal = await createEditProposal(structured.edits);
          if (!proposal?.validation_ok) {
            const invalid = (proposal?.edits || []).find((edit) => !edit?.validation?.ok);
            applyBtn.textContent = 'proposal invalid';
            await window.kcuiAlert('Proposal Invalid', invalid?.validation?.errors?.[0] || 'proposal validation failed');
            return;
          }
          const result = await applyEditProposal(proposal.proposal_id);
          if (result?.apply_result?.ok) {
            await reloadTabs?.(fileList);
            applyBtn.textContent = `applied (${result.apply_result?.applied || 0})`;
          } else {
            const firstError = Array.isArray(result?.apply_result?.errors) && result.apply_result.errors.length ? result.apply_result.errors[0] : 'apply failed';
            applyBtn.textContent = 'apply failed';
            await window.kcuiAlert('Apply Failed', firstError);
          }
        } catch {
          applyBtn.textContent = 'apply failed';
        } finally {
          setTimeout(() => {
            applyBtn.disabled = false;
            applyBtn.textContent = prev;
          }, 1200);
        }
      });
      row.appendChild(applyBtn);

      if (typeof saveTabs === 'function') {
        const applySaveBtn = document.createElement('button');
        applySaveBtn.type = 'button';
        applySaveBtn.className = 'kcui-tag kcui-tag--accent';
        applySaveBtn.textContent = 'apply proposal';
        applySaveBtn.addEventListener('click', async () => {
          const prev     = applySaveBtn.textContent;
          const fileList = [...new Set(structured.edits.map((e) => String(e?.file || '').trim()).filter(Boolean))];
          const prompt   = `Create, apply, and reload proposal for ${structured.edits.length} edit(s) across ${fileList.length || 1} file(s)?`;
          const confirmed = await window.kcuiConfirm('Apply Proposal', prompt, { confirmLabel: 'Apply' });
          if (!confirmed) return;
          applySaveBtn.disabled = true;
          try {
            const proposal = await createEditProposal(structured.edits);
            if (!proposal?.validation_ok) {
              const invalid = (proposal?.edits || []).find((edit) => !edit?.validation?.ok);
              applySaveBtn.textContent = 'proposal invalid';
              await window.kcuiAlert('Proposal Invalid', invalid?.validation?.errors?.[0] || 'proposal validation failed');
              return;
            }
            const applyResult = await applyEditProposal(proposal.proposal_id);
            if (!applyResult?.apply_result?.ok) {
              const firstApplyError = Array.isArray(applyResult?.apply_result?.errors) && applyResult.apply_result.errors.length ? applyResult.apply_result.errors[0] : 'apply failed';
              applySaveBtn.textContent = 'apply failed';
              await window.kcuiAlert('Apply Failed', firstApplyError);
              return;
            }
            const reloadResult = await reloadTabs?.(fileList);
            if (!reloadResult || reloadResult?.ok) {
              applySaveBtn.textContent = `applied (${applyResult.apply_result?.applied || 0})`;
            } else {
              const firstSaveError = Array.isArray(reloadResult?.errors) && reloadResult.errors.length ? reloadResult.errors[0] : 'reload failed';
              applySaveBtn.textContent = 'reload failed';
              await window.kcuiAlert('Reload Failed', firstSaveError);
            }
          } catch {
            applySaveBtn.textContent = 'apply/save failed';
          } finally {
            setTimeout(() => {
              applySaveBtn.disabled = false;
              applySaveBtn.textContent = prev;
            }, 1400);
          }
        });
        row.appendChild(applySaveBtn);
      }
    }

    return row;
  }

  function buildMsgEl(msg) {
    const el = document.createElement('div');

    if (msg.role === 'user') {
      el.className = 'chat-msg chat-msg--user';
      const bubble = document.createElement('div');
      bubble.className = 'bubble';
      bubble.textContent = msg.text;
      el.appendChild(bubble);
    } else {
      el.className = 'chat-msg chat-msg--assistant';
      const avatar = document.createElement('div');
      avatar.className = 'avatar';
      avatar.textContent = 'Kore';
      const body = document.createElement('div');
      body.className = 'body';
      body.innerHTML = renderAssistantText(msg.text);
      const codeText = extractCodeForActions(msg.text);
      const structured = extractStructuredEdits(msg.text);
      el.appendChild(avatar);
      el.appendChild(body);
      if (codeText || structured) {
        el.appendChild(buildAssistantActions(codeText || JSON.stringify(structured, null, 2), structured));
      }
    }

    return el;
  }

  function renderThread(path, msgs) {
    thread.innerHTML = '';
    if (!path) return;

    if (!msgs.length) {
      const empty = document.createElement('div');
      empty.id = 'chat-empty';
      empty.textContent = 'Ask about this file…';
      thread.appendChild(empty);
      return;
    }

    for (let i = 0; i < msgs.length; i += 1) {
      const msg = msgs[i];
      thread.appendChild(buildMsgEl(msg));
      if (msg.role === 'assistant' && i < msgs.length - 1) {
        const div = document.createElement('div');
        div.className = 'chat-divider';
        thread.appendChild(div);
      }
    }

    scrollBottom();
  }

  function appendThinking(label = 'Kore is thinking') {
    const el = document.createElement('div');
    el.className = 'chat-thinking';
    el.innerHTML = `${esc(label)} <span class="chat-thinking-dots"><span>•</span><span>•</span><span>•</span></span>`;
    thread.appendChild(el);
    scrollBottom();
    return el;
  }

  function appendLivePre() {
    const el = document.createElement('div');
    el.className = 'chat-msg chat-msg--assistant';
    const avatar = document.createElement('div');
    avatar.className = 'avatar';
    avatar.textContent = 'Kore';
    const body = document.createElement('div');
    body.className = 'body';
    const pre = document.createElement('pre');
    pre.style.cssText = 'margin:0;padding:0;background:none;border:none;white-space:pre-wrap;word-break:break-word;';
    body.appendChild(pre);
    el.appendChild(avatar);
    el.appendChild(body);
    thread.appendChild(el);
    scrollBottom();
    return pre;
  }

  return {
    renderThread,
    appendThinking,
    appendLivePre,
    scrollBottom,
  };
}
