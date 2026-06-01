/**
 * element2.js — behaviours for UIElements2 interactive components.
 *
 * Provides JavaScript behaviour for components that require it.
 * CSS-only components (e2-btn, e2-input, e2-slider, etc.) need no JS here.
 *
 * Exports:
 *   initSegControl(el)       — bind click-toggle behaviour to a single .e2-seg element
 *   initAllSegControls(root) — bind all .e2-seg[data-e2-auto] elements under root
 *   showFileSelectDialog()   — themed file browser with in-dialog traversal
 *   showFolderSelectDialog() — themed folder browser with in-dialog traversal
 *   showConfirmDialog()      — themed confirmation popup
 */

/**
 * Bind mutually-exclusive toggle behaviour to a single .e2-seg element.
 * Clicks on any .e2-seg__btn make it active and deactivate all siblings.
 * Fires a bubbling 'e2:seg-change' CustomEvent with { value, btn } detail.
 *
 * @param {HTMLElement} el  The .e2-seg container element.
 */
export function initSegControl(el) {
    el.addEventListener('click', (e) => {
        const btn = e.target.closest('.e2-seg__btn');
        if (!btn || !el.contains(btn)) return;
        if (btn.disabled) return;
        for (const sibling of el.querySelectorAll('.e2-seg__btn')) {
            sibling.classList.toggle('is-active', sibling === btn);
        }
        el.dispatchEvent(new CustomEvent('e2:seg-change', {
            bubbles: true,
            detail: { value: btn.dataset.value ?? btn.textContent.trim(), btn },
        }));
    });
}

/**
 * Bind initSegControl to every .e2-seg[data-e2-auto] element under root.
 * Called automatically on DOMContentLoaded.
 *
 * @param {Document|HTMLElement} root  Search root (default: document).
 */
export function initAllSegControls(root = document) {
    for (const el of root.querySelectorAll('.e2-seg[data-e2-auto]')) {
        initSegControl(el);
    }
}

function _makeDialogButton(label, className, onClick) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = className;
    btn.textContent = label;
    btn.addEventListener('click', onClick);
    return btn;
}

function _buildDialogShell({ title, description }) {
    const dialog = document.createElement('dialog');
    dialog.className = 'e2-dialog';

    const surface = document.createElement('div');
    surface.className = 'e2-dialog__surface';

    const header = document.createElement('div');
    header.className = 'e2-dialog__header';

    const titleEl = document.createElement('div');
    titleEl.className = 'e2-dialog__title';
    titleEl.textContent = title;
    header.appendChild(titleEl);

    if (description) {
        const descEl = document.createElement('div');
        descEl.className = 'e2-dialog__desc';
        descEl.textContent = description;
        header.appendChild(descEl);
    }

    const body = document.createElement('div');
    body.className = 'e2-dialog__body';

    const footer = document.createElement('div');
    footer.className = 'e2-dialog__footer';

    surface.append(header, body, footer);
    dialog.appendChild(surface);
    document.body.appendChild(dialog);

    return { dialog, surface, body, footer };
}

async function _fetchDialogJson(path, params = {}) {
    const url = new URL(path, window.location.origin);
    for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) {
            url.searchParams.set(key, value);
        }
    }

    const response = await fetch(url.toString(), { cache: 'no-store' });
    if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`;
        try {
            const payload = await response.json();
            if (payload?.detail) {
                detail = payload.detail;
            }
        } catch {
            // keep the HTTP status text if the error payload is not JSON
        }
        throw new Error(detail);
    }

    return response.json();
}

function _formatFileSize(size) {
    if (size == null || Number.isNaN(size)) {
        return '';
    }

    if (size < 1024) {
        return `${size} B`;
    }

    if (size < 1024 * 1024) {
        return `${(size / 1024).toFixed(1)} KB`;
    }

    if (size < 1024 * 1024 * 1024) {
        return `${(size / (1024 * 1024)).toFixed(1)} MB`;
    }

    return `${(size / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function _describeBrowseSelection(state, mode) {
    if (!state.listing) {
        return mode === 'folder' ? 'No folder loaded.' : 'No file selected.';
    }

    if (mode === 'folder') {
        return `Selected folder: ${state.listing.root.label}${state.listing.display_path}`;
    }

    if (!state.selectedEntry) {
        return 'Select a file from the browser list.';
    }

    return `Selected file: ${state.selectedEntry.path}`;
}

function _makeBrowserResult(state, mode) {
    if (!state.listing) {
        return null;
    }

    if (mode === 'folder') {
        const folderName = state.listing.path
            ? state.listing.path.split('/').filter(Boolean).pop()
            : state.listing.root.label;
        return {
            kind: 'folder',
            root: state.listing.root,
            path: state.listing.path,
            displayPath: `${state.listing.root.label}${state.listing.display_path}`,
            name: folderName,
            absolutePath: state.listing.absolute_path,
        };
    }

    if (!state.selectedEntry) {
        return null;
    }

    return {
        kind: 'file',
        root: state.listing.root,
        path: state.selectedEntry.path,
        displayPath: `${state.listing.root.label}/${state.selectedEntry.path}`,
        name: state.selectedEntry.name,
        size: state.selectedEntry.size,
        absolutePath: state.selectedEntry.absolute_path,
    };
}

function _renderBrowserSummary(container, state, mode) {
    container.textContent = '';

    const summary = document.createElement('div');
    summary.className = 'e2-dialog__summary-title';
    summary.textContent = _describeBrowseSelection(state, mode);
    container.appendChild(summary);

    if (!state.listing) {
        return;
    }

    const note = document.createElement('div');
    note.className = 'e2-dialog__summary-note';
    note.textContent = mode === 'folder'
        ? 'Use Open on folders to traverse, then confirm the current folder.'
        : 'Click a file row to select it. Folder rows open deeper into the tree.';
    container.appendChild(note);
}

function _createEntryRow(entry, mode, state, openPath, rerender) {
    const row = document.createElement('div');
    row.className = 'e2-dialog__entry';
    if (state.selectedEntry?.path === entry.path) {
        row.classList.add('is-selected');
    }

    const main = document.createElement('button');
    main.type = 'button';
    main.className = 'e2-dialog__entry-main';

    const name = document.createElement('span');
    name.className = 'e2-dialog__entry-name';
    name.textContent = entry.name;

    const meta = document.createElement('span');
    meta.className = 'e2-dialog__entry-meta';
    meta.textContent = entry.type === 'dir' ? 'Folder' : _formatFileSize(entry.size);

    main.append(name, meta);
    row.appendChild(main);

    if (entry.type === 'dir') {
        const openBtn = _makeDialogButton('Open', 'e2-btn e2-btn--accent', () => {
            void openPath(entry.path);
        });
        openBtn.classList.add('e2-dialog__entry-action');
        row.appendChild(openBtn);

        main.addEventListener('click', () => {
            void openPath(entry.path);
        });
    } else {
        const isSelectable = mode === 'file';
        main.addEventListener('click', () => {
            if (!isSelectable) {
                return;
            }
            state.selectedEntry = entry;
            rerender();
        });

        if (isSelectable) {
            const chooseBtn = _makeDialogButton('Select', 'e2-btn e2-btn--info', () => {
                state.selectedEntry = entry;
                rerender();
            });
            chooseBtn.classList.add('e2-dialog__entry-action');
            row.appendChild(chooseBtn);
        }
    }

    return row;
}

function _showBrowserDialog({
    mode,
    title,
    description,
    confirmLabel,
    rootId = 'workspace',
    startPath = '',
}) {
    return new Promise((resolve) => {
        const { dialog, surface, body, footer } = _buildDialogShell({ title, description });
        surface.classList.add('e2-dialog__surface--browser');

        const toolbar = document.createElement('div');
        toolbar.className = 'e2-dialog__toolbar';

        const rootSelect = document.createElement('select');
        rootSelect.className = 'e2-select e2-dialog__root-select';
        toolbar.appendChild(rootSelect);

        const upBtn = _makeDialogButton('Up', 'e2-btn e2-btn--muted', () => {
            if (!state.listing) {
                return;
            }
            void loadPath(state.listing.parent_path || '');
        });
        toolbar.appendChild(upBtn);

        const refreshBtn = _makeDialogButton('Refresh', 'e2-btn e2-btn--info', () => {
            void loadPath(state.path || '');
        });
        toolbar.appendChild(refreshBtn);

        const pathBar = document.createElement('div');
        pathBar.className = 'e2-dialog__path';

        const statusBox = document.createElement('div');
        statusBox.className = 'e2-dialog__status';

        const browserList = document.createElement('div');
        browserList.className = 'e2-dialog__browser';

        const summaryBox = document.createElement('div');
        summaryBox.className = 'e2-dialog__summary';

        body.append(toolbar, pathBar, statusBox, browserList, summaryBox);

        const state = {
            roots: [],
            rootId,
            path: startPath,
            listing: null,
            selectedEntry: null,
            loading: false,
            error: '',
        };

        let finalised = false;

        const confirmTone = mode === 'folder' ? 'e2-btn e2-btn--accent' : 'e2-btn e2-btn--info';
        const confirmBtn = _makeDialogButton(confirmLabel, confirmTone, () => {
            finish(_makeBrowserResult(state, mode));
        });
        confirmBtn.disabled = true;

        const cancelBtn = _makeDialogButton('Cancel', 'e2-btn e2-btn--muted', () => {
            finish(null);
        });

        footer.append(cancelBtn, confirmBtn);

        const finish = (result) => {
            if (finalised) return;
            finalised = true;
            dialog.close();
            dialog.remove();
            resolve(result);
        };

        const render = () => {
            pathBar.textContent = state.listing
                ? `${state.listing.root.label}${state.listing.display_path}`
                : 'Loading...';

            statusBox.classList.toggle('is-error', Boolean(state.error));
            statusBox.textContent = state.error || (state.loading ? 'Loading directory…' : '');
            statusBox.hidden = !(state.error || state.loading);

            browserList.textContent = '';
            if (!state.listing) {
                const empty = document.createElement('div');
                empty.className = 'e2-dialog__empty';
                empty.textContent = state.loading ? 'Loading directory…' : 'No directory loaded.';
                browserList.appendChild(empty);
            } else if (!state.listing.entries.length) {
                const empty = document.createElement('div');
                empty.className = 'e2-dialog__empty';
                empty.textContent = 'This folder is empty.';
                browserList.appendChild(empty);
            } else {
                for (const entry of state.listing.entries) {
                    browserList.appendChild(_createEntryRow(entry, mode, state, loadPath, render));
                }
            }

            upBtn.disabled = state.loading || !state.listing || !state.listing.path;
            refreshBtn.disabled = state.loading;
            rootSelect.disabled = state.loading;
            confirmBtn.disabled = state.loading || (mode === 'file' && !state.selectedEntry);

            _renderBrowserSummary(summaryBox, state, mode);
        };

        const loadPath = async (nextPath) => {
            state.loading = true;
            state.error = '';
            state.path = nextPath || '';
            state.selectedEntry = null;
            render();

            try {
                state.listing = await _fetchDialogJson('/api/fs/list', {
                    root: state.rootId,
                    path: state.path,
                });
            } catch (error) {
                state.error = error instanceof Error ? error.message : 'Directory load failed.';
            } finally {
                state.loading = false;
                render();
            }
        };

        rootSelect.addEventListener('change', () => {
            state.rootId = rootSelect.value;
            void loadPath('');
        });

        dialog.addEventListener('cancel', (event) => {
            event.preventDefault();
            finish(null);
        });

        dialog.addEventListener('click', (event) => {
            if (event.target === dialog) {
                finish(null);
            }
        });

        dialog.showModal();

        void (async () => {
            state.loading = true;
            render();

            try {
                const payload = await _fetchDialogJson('/api/fs/roots');
                state.roots = payload.roots || [];
                if (!state.roots.length) {
                    throw new Error('No browse roots configured.');
                }

                if (!state.roots.some((rootEntry) => rootEntry.id === state.rootId)) {
                    state.rootId = state.roots[0].id;
                }

                rootSelect.textContent = '';
                for (const rootEntry of state.roots) {
                    const option = document.createElement('option');
                    option.value = rootEntry.id;
                    option.textContent = rootEntry.label;
                    option.selected = rootEntry.id === state.rootId;
                    rootSelect.appendChild(option);
                }

                await loadPath(state.path || '');
                return;
            } catch (error) {
                state.loading = false;
                state.error = error instanceof Error ? error.message : 'Browse roots could not be loaded.';
                render();
            }
        })();
    });
}

export function showFileSelectDialog(options = {}) {
    return _showBrowserDialog({
        mode: 'file',
        title: options.title || 'File Select',
        description: options.description || 'Browse the available roots and select a file directly inside the themed dialog.',
        confirmLabel: options.confirmLabel || 'Use file',
        rootId: options.rootId,
        startPath: options.startPath,
    });
}

export function showFolderSelectDialog(options = {}) {
    return _showBrowserDialog({
        mode: 'folder',
        title: options.title || 'Folder Select',
        description: options.description || 'Browse the available roots and confirm the current folder when you reach the target location.',
        confirmLabel: options.confirmLabel || 'Use this folder',
        rootId: options.rootId,
        startPath: options.startPath,
    });
}

export function showConfirmDialog(options = {}) {
    return new Promise((resolve) => {
        const { dialog, surface, body, footer } = _buildDialogShell({
            title: options.title || 'Confirm Action',
            description: options.description || 'Check the action before continuing.',
        });

        const message = document.createElement('div');
        message.className = 'e2-dialog__message';
        message.textContent = options.message || 'This action will continue immediately if confirmed.';
        body.appendChild(message);

        let finalised = false;
        const finish = (result) => {
            if (finalised) return;
            finalised = true;
            dialog.close();
            dialog.remove();
            resolve(result);
        };

        const cancelBtn = _makeDialogButton(options.cancelLabel || 'Cancel', 'e2-btn e2-btn--muted', () => finish(false));
        const toneClass = options.confirmTone === 'danger'
            ? 'e2-btn e2-btn--danger'
            : 'e2-btn e2-btn--accent';
        const confirmBtn = _makeDialogButton(options.confirmLabel || 'Confirm', toneClass, () => finish(true));

        footer.append(cancelBtn, confirmBtn);

        dialog.addEventListener('cancel', (event) => {
            event.preventDefault();
            finish(false);
        });

        dialog.addEventListener('click', (event) => {
            if (event.target === dialog) {
                finish(false);
            }
        });

        dialog.showModal();
    });
}

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => initAllSegControls());
}
