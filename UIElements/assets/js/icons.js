/**
 * icons.js — shared icon registry for UIElements shell primitives.
 */

function koreDocsIcon(size = 12) {
  const s = `width="${size}" height="${size}"`;
  return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M6 2.8h5.7L15.2 6v10.2a1.8 1.8 0 0 1-1.8 1.8H6a1.8 1.8 0 0 1-1.8-1.8V4.6A1.8 1.8 0 0 1 6 2.8Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>
      <path d="M11.7 2.8V6h3.5" stroke="currentColor" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M7 9h6M7 11.9h6M7 14.8h3.9" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/>
    </svg>`;
}

export const SUITE_ICONS = {
  korestack(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M4 5.1 10 2.8l6 2.3L10 7.5 4 5.1Z" stroke="currentColor" stroke-width="1.45" stroke-linejoin="round"/>
      <path d="M4 9.3 10 7l6 2.3-6 2.4-6-2.4Z" stroke="currentColor" stroke-width="1.45" stroke-linejoin="round" opacity=".88"/>
      <path d="M4 13.5 10 11.2l6 2.3-6 2.4-6-2.4Z" stroke="currentColor" stroke-width="1.45" stroke-linejoin="round" opacity=".72"/>
    </svg>`;
  },
  koreagent(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="4.5" y="4.5" width="11" height="11" rx="3" stroke="currentColor" stroke-width="1.5"/>
      <path d="M10 2.8v1.7M3.2 8.5h1.3M15.5 8.5h1.3M3.2 11.5h1.3M15.5 11.5h1.3" stroke="currentColor" stroke-width="1.35" stroke-linecap="round"/>
      <path d="M10 7.1 11 9.05l2.2.32-1.6 1.55.38 2.18L10 12.05 8.02 13.1l.38-2.18-1.6-1.55L9 9.05 10 7.1Z" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round"/>
    </svg>`;
  },
  koredata(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <ellipse cx="10" cy="5" rx="5.5" ry="2.5" stroke="currentColor" stroke-width="1.4"/>
      <path d="M4.5 5v7c0 1.4 2.46 2.5 5.5 2.5s5.5-1.1 5.5-2.5V5" stroke="currentColor" stroke-width="1.4"/>
      <path d="M4.5 8.5c0 1.4 2.46 2.5 5.5 2.5s5.5-1.1 5.5-2.5" stroke="currentColor" stroke-width="1.4"/>
    </svg>`;
  },
  koredocs: koreDocsIcon,
  korecomms(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="2.8" y="4.8" width="14.4" height="10.4" rx="1.8" stroke="currentColor" stroke-width="1.45"/>
      <path d="M3.8 6 10 10.5 16.2 6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
  },
  koredoc: koreDocsIcon,
  koresheet: koreDocsIcon,
  kodiag: koreDocsIcon,
  korefile: koreDocsIcon,
  koreconversation(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M4.6 4.5h6.7A2.1 2.1 0 0 1 13.4 6.6v3.2a2.1 2.1 0 0 1-2.1 2.1H8.1l-2.7 2v-2H4.6a2.1 2.1 0 0 1-2.1-2.1V6.6a2.1 2.1 0 0 1 2.1-2.1Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
      <path d="M9.5 7.6h5.8A2.2 2.2 0 0 1 17.5 9.8v3a2.2 2.2 0 0 1-2.2 2.2h-.7v2l-2.7-2H9.5a2.2 2.2 0 0 1-2-1.2" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
    </svg>`;
  },
};

export function resolveIcon(icons, key, size) {
  const icon = icons[key];
  if (typeof icon === 'function') return icon(size);
  if (typeof icon === 'string') return icon;
  return '';
}