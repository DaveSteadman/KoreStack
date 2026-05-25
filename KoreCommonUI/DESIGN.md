# KoreCommonUI

## Purpose

KoreCommonUI is the shared UI framework that supplies CSS/JS page formatting, fonts and icons to provide all KoreStack pages with a common look and feel.

---

## Design Principles

### One source, no copies

KoreCommonUI defines constants for the size and layout of any component. No consuming page is allowed to define any size or padding as it would override a KoreCommonUI value. If we find a new value, then we'll define it here and propagate it out.

- rootsize.css - defines constants around size and spaceing. Nothing else can define a number
- rootfont.css - defines all font styles, nothing else can deine a font
- rootcolor.css - defines all colors

### No new constants without explicit instruction

**Before reaching for a new token, stop.**

Every spacing, sizing, or colour value in any CSS or JS file must already exist in `tokens.css` or `colors.js`.  If the value you need is not there, the correct response is to use the closest existing token — not to invent a new one.

A new constant may only be created when:
1. The user explicitly states that a new constant is needed, and
2. The user states its name and value, or explicitly delegates that decision.

Creating a constant because it "seems useful", because an existing token "feels too small", or because a layout "needs more space" is not permitted.  These are design decisions that belong to the user, not to the implementer.

**The most common violation** is using a spacing token (`--gap-sm`, `--gap-md`, `--gap-lg`, etc.) as a padding value because it produces a visually pleasing result.  This is wrong.  `--pad-h` is the padding token.  It applies to every element, in every context, without exception.  The fact that it produces tight spacing is intentional — the design is governed by the constants, not the other way around.

### Single-source font policy

**Fonts are governed by one file only: `assets/css/fonts.css`.**

Allowed font family:
- `Roboto Mono` from `assets/fonts/` (Regular, Medium, SemiBold, Bold)

Hard rules:
- No file other than `assets/css/fonts.css` may contain `@font-face`.
- No file other than `assets/css/fonts.css` may set `font-family`, `font-size`, `font-weight`, or `line-height`.
- `tokens.css` may not define font tokens; font tokens live in `fonts.css`.
- Consuming pages must load `tokens.css`, then `fonts.css`, then component CSS.

Code review must reject any change that defines font styles outside `assets/css/fonts.css`.

### Foundational height constants

**This is the one rule that may not be broken.**

Two numbers govern every structural height in the system.  Nothing else does.

| Token | Value | Meaning |
|---|---|---|
| `--el-h` | `1.75rem` | The height of every element: buttons, inputs, labels, icon slots, and all interactive controls |
| `--pad-h` | `0.3125rem` | The padding placed **above, below, and to the left** of every element — no more, no less |

Every row height — a topbar, a panel header row, a toolbar — is exactly:

```
--row-h = --el-h + 2 × --pad-h
```

This is the only equation that may produce a structural height.  A panel header row, a control and its vertical alignment, a topbar: all of them are `--row-h`.  There is no other permitted source of height.

**Consequences:**

- No file may contain a hardcoded pixel or rem value for any height or vertical padding.
- No consuming page may define or override `--el-h`, `--pad-h`, or `--row-h`.
- If a new height relationship is needed, it must be expressed as a token derived from `--el-h` and `--pad-h`, defined here in `tokens.css`, and documented in this file.
- Code review must reject any PR that introduces a dimension outside this system.

---

## Controls

### Text Line Edit

This control defines a height, given the font within it. We use this height to then drive the element height.


