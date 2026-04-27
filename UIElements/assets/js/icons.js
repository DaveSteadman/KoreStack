/**
 * icons.js — shared icon registry for UIElements shell primitives.
 */

export const SUITE_ICONS = {
  korestack(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M3 6.5h14M3 10h14M3 13.5h14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      <rect x="4" y="3" width="4" height="4" rx="1" fill="currentColor" opacity=".95"/>
      <rect x="12" y="8" width="4" height="4" rx="1" fill="currentColor" opacity=".75"/>
      <rect x="7" y="13" width="4" height="4" rx="1" fill="currentColor" opacity=".55"/>
    </svg>`;
  },
  koreagent(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="5" y="5" width="10" height="10" rx="2" stroke="currentColor" stroke-width="1.6"/>
      <path d="M8 2.8 10 5l2-2.2" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="8" cy="10" r="1" fill="currentColor"/>
      <circle cx="12" cy="10" r="1" fill="currentColor"/>
      <path d="M8 12.8h4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
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
  koredocs(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M5 3.5h8l2 2v10.5a1.5 1.5 0 0 1-1.5 1.5h-8A1.5 1.5 0 0 1 4 16V5a1.5 1.5 0 0 1 1-1.5Z" stroke="currentColor" stroke-width="1.5"/>
      <path d="M13 3.5V6h2.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
      <path d="M7 9h6M7 12h6M7 15h4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
    </svg>`;
  },
  korecomms(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <path d="M4 6.5h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      <path d="M4 10h7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      <path d="M4 13.5h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
      <path d="M13.5 8.5 16.5 10l-3 1.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
  },
  koredoc(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="3" y="2" width="14" height="16" rx="2" stroke="currentColor" stroke-width="1.8"/>
      <line x1="6" y1="7" x2="14" y2="7" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
      <line x1="6" y1="10" x2="14" y2="10" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
      <line x1="6" y1="13" x2="11" y2="13" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>
    </svg>`;
  },
  koresheet(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="2" y="2" width="16" height="16" rx="2" stroke="currentColor" stroke-width="1.8"/>
      <line x1="2" y1="8" x2="18" y2="8" stroke="currentColor" stroke-width="1.2"/>
      <line x1="2" y1="13" x2="18" y2="13" stroke="currentColor" stroke-width="1.2"/>
      <line x1="8" y1="2" x2="8" y2="18" stroke="currentColor" stroke-width="1.2"/>
      <line x1="13" y1="2" x2="13" y2="18" stroke="currentColor" stroke-width="1.2"/>
    </svg>`;
  },
  kodiag(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <circle cx="4" cy="10" r="2.5" stroke="currentColor" stroke-width="1.5"/>
      <circle cx="16" cy="4" r="2.5" stroke="currentColor" stroke-width="1.5"/>
      <circle cx="16" cy="16" r="2.5" stroke="currentColor" stroke-width="1.5"/>
      <line x1="6.2" y1="9" x2="13.8" y2="5" stroke="currentColor" stroke-width="1.3"/>
      <line x1="6.2" y1="11" x2="13.8" y2="15" stroke="currentColor" stroke-width="1.3"/>
    </svg>`;
  },
  koreconversation(size = 12) {
    const s = `width="${size}" height="${size}"`;
    return `<svg ${s} viewBox="0 0 20 20" fill="none" aria-hidden="true">
      <rect x="2.5" y="3" width="15" height="11" rx="2" stroke="currentColor" stroke-width="1.6"/>
      <path d="M6 14v3l3.3-3" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
      <circle cx="7" cy="8.5" r=".9" fill="currentColor"/>
      <circle cx="10" cy="8.5" r=".9" fill="currentColor"/>
      <circle cx="13" cy="8.5" r=".9" fill="currentColor"/>
    </svg>`;
  },
};

export function resolveIcon(icons, key, size) {
  const icon = icons[key];
  if (typeof icon === 'function') return icon(size);
  if (typeof icon === 'string') return icon;
  return '';
}