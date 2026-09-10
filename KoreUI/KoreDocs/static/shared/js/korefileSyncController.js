import * as api from '/static/shared/js/korefileapi.js';
import * as draft from '/static/shared/js/draft.js';
import { kcuiChoice, kcuiConfirm } from '/ui-elements/assets/js/dialogs.js';

export function createKorefileSyncController({
  logLabel,
  alertLabel,
  legacyType,
  buildBlankContent,
  applyLoadedContent,
  onMarkDirty,
  onMarkSaved,
  hasExternalUnsavedChanges,
  onSaveSuccess,
  onAfterApplyRemote,
  autosaveMs = 1000,
  remoteSyncMs = 2000,
}) {
  let currentId = null;
  let currentName = null;
  let currentRevision = null;
  let dirty = false;
  let onChanged = null;
  let saveTimer = null;
  let pendingText = null;
  let saving = false;
  let changeSeq = 0;
  let savedSeq = 0;
  let syncTimer = null;
  let loader = null;
  let currentMetadata = {};

  function normaliseRevision(value) {
    if (value === null || value === undefined) return null;
    return String(value);
  }

  function notify() {
    onChanged?.(currentName, dirty);
  }

  function markDirty() {
    dirty = true;
    onMarkDirty?.();
    notify();
  }

  function markSaved() {
    dirty = false;
    onMarkSaved?.();
    notify();
  }

  function resetPendingState() {
    pendingText = null;
    changeSeq = 0;
    savedSeq = 0;
    if (saveTimer) {
      clearTimeout(saveTimer);
      saveTimer = null;
    }
  }

  async function chooseRemoteChange(file, localContent) {
    return kcuiChoice(
      `${alertLabel} changed in the background`,
      'Choose which version to keep. Comparing does not discard either version.',
      {
        cancelValue: 'load',
        choices: [
          { value: 'load', label: 'Load remote', quiet: true },
          { value: 'keep', label: 'Keep mine' },
        ],
        details: [
          { label: 'Your local changes', content: localContent },
          { label: 'Latest server version', content: file.content || '' },
        ],
      },
    );
  }

  async function preserveLocalChanges(file) {
    const replacedLocalChanges = dirty || pendingText != null || Boolean(hasExternalUnsavedChanges?.());
    if (!replacedLocalChanges || currentId == null) return false;

    const localContent = pendingText ?? draft.load();
    const choice = await chooseRemoteChange(file, localContent ?? '');
    if (choice !== 'keep') return false;

    if (localContent != null) {
      pendingText = localContent;
      draft.save(localContent);
    }
    currentName     = file.name;
    currentRevision = normaliseRevision(file.revision);
    currentMetadata = file.metadata && typeof file.metadata === 'object' ? file.metadata : {};
    markDirty();
    armAutosave();
    return true;
  }

  async function applyRemoteFile(file) {
    if (await preserveLocalChanges(file)) return;

    const replacedLocalChanges = dirty || pendingText != null || Boolean(hasExternalUnsavedChanges?.());
    const content = (file.content || '').trim()
      ? file.content
      : buildBlankContent(file.name.replace(/\.[^.]+$/, ''));

    currentId = file.id;
    currentName = file.name;
    currentRevision = normaliseRevision(file.revision);
    currentMetadata = file.metadata && typeof file.metadata === 'object' ? file.metadata : {};
    resetPendingState();
    applyLoadedContent(content, file, loader);
    draft.clear();
    markSaved();
    onAfterApplyRemote?.({ file, replacedLocalChanges });
  }

  async function reloadLatest() {
    if (currentId == null) return;
    try {
      const latest = await api.getFile(currentId);
      await applyRemoteFile(latest);
    } catch (err) {
      console.warn(`[${logLabel}] failed to reload latest version for`, currentName, err);
    }
  }

  function armAutosave() {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveTimer = null;
      flushAutosave();
    }, autosaveMs);
  }

  function startRemoteSync() {
    if (syncTimer || currentId == null) return;
    syncTimer = setInterval(async () => {
      if (currentId == null) return;
      // Local edits are either queued or being written; optimistic concurrency
      // on save handles conflicts, so skip polling to avoid false self-conflicts.
      if (saving || pendingText != null) return;
      try {
        const latest = await api.getFile(currentId, { includeContent: false });
        const latestRevision = normaliseRevision(latest.revision);
        if (currentRevision != null && latestRevision !== currentRevision) {
          await reloadLatest();
        }
      } catch (err) {
        console.warn(`[${logLabel}] remote sync check failed for`, currentName, err);
      }
    }, remoteSyncMs);
  }

  async function findLegacyFile(name) {
    try {
      return await api.resolveLegacyFile(legacyType, name);
    } catch (err) {
      console.warn(`[${logLabel}] failed to resolve legacy file link for`, name, err);
      return null;
    }
  }

  async function autoOpenFromUrl(nextLoader) {
    const params = new URLSearchParams(location.search);
    let id = params.get('id');
    if (!id) {
      const legacyName = params.get('file');
      if (!legacyName) return false;
      const legacyFile = await findLegacyFile(legacyName);
      if (!legacyFile) return false;
      id = String(legacyFile.id);
      params.set('id', id);
      params.set('file', legacyFile.name);
      history.replaceState(null, '', `${location.pathname}?${params.toString()}`);
    }
    try {
      loader = nextLoader;
      const file = await api.getFile(parseInt(id, 10));
      await applyRemoteFile(file);
      startRemoteSync();
      return true;
    } catch (err) {
      console.warn(`[${logLabel}] auto-open failed for id`, id, err);
      return false;
    }
  }

  function queueAutosave(text) {
    if (currentId == null) return;
    pendingText = text;
    changeSeq += 1;
    armAutosave();
  }

  async function flushAutosave(options = {}) {
    if (currentId == null || saving || pendingText == null) return;
    const text = pendingText;
    const seq = changeSeq;
    pendingText = null;
    if (saveTimer) {
      clearTimeout(saveTimer);
      saveTimer = null;
    }
    saving = true;
    try {
      const updated = await api.updateFile(currentId, text, undefined, {
        ...options,
        expectedRevision: currentRevision,
      });
      currentRevision = normaliseRevision(updated.revision) ?? currentRevision;
      savedSeq = Math.max(savedSeq, seq);
      if (savedSeq === changeSeq && pendingText == null) {
        draft.clear();
        markSaved();
        onSaveSuccess?.();
      }
    } catch (err) {
      if (String(err?.message || err).includes('changed in the background')) {
        pendingText = text;
        markDirty();
        await reloadLatest();
        return;
      }
      console.warn(`[${logLabel}] autosave failed for`, currentName, err);
      pendingText = text;
      markDirty();
    } finally {
      saving = false;
      if (pendingText != null && !options.keepalive) {
        armAutosave();
      }
    }
  }

  return {
    init(handler) {
      onChanged = handler;
    },
    currentId: () => currentId,
    currentName: () => currentName,
    currentRevision: () => currentRevision,
    currentMetadata: () => ({ ...currentMetadata }),
    getHistory: () => currentId == null ? Promise.resolve([]) : api.getFileHistory(currentId),
    isDirty: () => dirty,
    markDirty,
    markSaved,
    guardUnsaved() {
      if (!dirty) return true;
      return kcuiConfirm(
        'Discard unsaved changes',
        'You have unsaved changes. Continue and discard them?',
        { confirmLabel: 'Discard' },
      );
    },
    autoOpenFromUrl,
    queueAutosave,
    flushAutosave,
  };
}
