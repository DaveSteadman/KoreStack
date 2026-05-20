/**
 * element2.js — behaviours for UIElements2 interactive components.
 *
 * Provides JavaScript behaviour for components that require it.
 * CSS-only components (e2-btn, e2-input, e2-slider, etc.) need no JS here.
 *
 * Exports:
 *   initSegControl(el)       — bind click-toggle behaviour to a single .e2-seg element
 *   initAllSegControls(root) — bind all .e2-seg[data-e2-auto] elements under root
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

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => initAllSegControls());
}
