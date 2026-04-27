import * as api from '/static/shared/js/korefileapi.js';
import * as draft from '/static/shared/js/draft.js';

export function createKfSyncController({
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

  function applyRemoteFile(file) {
    const replacedLocalChanges = dirty || pendingText != null || Boolean(hasExternalUnsavedChanges?.());
    const content = (file.content || '').trim()
      ? file.content
      : buildBlankContent(file.name.replace(/\.[^.]+$/, ''));

    currentId = file.id;
    currentName = file.name;
    currentRevision = file.revision ?? null;
    resetPendingState();
    applyLoadedContent(content, file, loader);
    draft.clear();
    markSaved();
    onAfterApplyRemote?.({ file, replacedLocalChanges });

    if (replacedLocalChanges) {
      alert(`${alertLabel} changed in the background. The latest server version has been loaded.`);
    }
  }

  async function reloadLatest() {
    if (currentId == null) return;
    try {
      const latest = await api.getFile(currentId);
      applyRemoteFile(latest);
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
      try {
        const latest = await api.getFile(currentId, { includeContent: false });
        if (currentRevision != null && latest.revision !== currentRevision) {
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
      applyRemoteFile(file);
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
      currentRevision = updated.revision ?? currentRevision;
      savedSeq = Math.max(savedSeq, seq);
      if (savedSeq === changeSeq && pendingText == null) {
        draft.clear();
        markSaved();
        onSaveSuccess?.();
      }
    } catch (err) {
      if (String(err?.message || err).includes('changed in the background')) {
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
    isDirty: () => dirty,
    markDirty,
    markSaved,
    guardUnsaved() {
      if (!dirty) return true;
      return confirm('You have unsaved changes. Continue and discard them?');
    },
    autoOpenFromUrl,
    queueAutosave,
    flushAutosave,
  };
}