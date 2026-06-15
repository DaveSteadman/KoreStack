# Web Consistency Standards

This document defines non-negotiable consistency rules for all Kore web pages.

## 1. Shared Shell Entry

All pages must import shared shell APIs from:
- `/ui-elements/assets/js/chrome.js`

Do not import `topbar.js`, `appbar.js`, or `appMenu.js` directly in app pages.

## 2. One Canonical Path

Use one shared `chrome.js` path across all services.
Do not append ad hoc version query strings to shared UIElements assets.

## 3. Top Bar Commonality

Top bar is shared and common across all pages.
- Mount `#topbar`
- Call `initTopbar({ currentService, urls: window.__koreSuiteUrls || {} })`
- Suite version comes from `suiteMeta.js`

## 4. App Bar Commonality

App bar must be initialized through shared shell APIs.
- Use `initAppBar(...)`
- Use shared tabs (`initAppTabs`) when needed

## 5. Tag Canonical Rule

Use `kcui-tag` component only. No lookalike variants.
- Label tags: rounded ends
- Button tags: square ends

## 6. Typography Rule

Use semantic shared role classes from `UIElements/assets/css/typography.css` for shell chrome text.
Avoid per-page ad hoc shell font sizing unless strictly local to domain content.

## 7. suite-config Contract

Every web page should load:
- `<script src="/suite-config.js"></script>`

Then pass `window.__koreSuiteUrls` into `initTopbar`.

## 8. No Inline Event Handlers

Use `addEventListener` over inline HTML event attributes (`onclick`, `onchange`, etc.).
Inline handlers are allowed only as temporary legacy exceptions.

## 9. CSS Shell Baseline

Use shared shell CSS via `chrome.css` and optional `workspace.css`.
Local CSS should extend, not override shell primitives unless intentional.

Do not re-declare shared shell tokens (`--bg`, `--border`, `--text`, `--accent`, etc.) in service CSS.
If a local variant is required, alias from shared tokens with service-prefixed variables.

## 10. CI Guardrail

Run `python tools/check_web_consistency.py` in CI.
Fail CI on:
- Multiple `chrome.js` version stamps
- Direct UIElements imports (`topbar.js`, `appbar.js`, `appMenu.js`)

Use strict mode in CI once migration is complete:
- `python tools/check_web_consistency.py --strict`

Strict mode also fails CI on:
- Inline HTML event handlers
- Service CSS shell token overrides
