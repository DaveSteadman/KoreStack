/**
 * suiteMeta.js — suite-wide version constant.
 *
 * Single source of truth for the current build identifier shown in the topbar
 * version chip.  Bump SUITE_VERSION here when cutting a new build.
 *
 * Key exports:
 *   SUITE_VERSION   — version string, e.g. 'Build:0040 / 0.6+dev'
 *
 * Related modules:
 *   - topbar.js   — imports SUITE_VERSION to render the trailing version chip
 */

export const SUITE_VERSION = 'Build:0040 / 0.6+dev';