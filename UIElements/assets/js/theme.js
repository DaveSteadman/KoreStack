const SERVICE_THEMES = {
  korestack: { accent: '#6eb5ff', accent2: '#9fd0ff' },
  koreagent: { accent: '#66f0c9', accent2: '#9af7de' },
  koreconversation: { accent: '#59d7ff', accent2: '#93e7ff' },
  koredata: { accent: '#a78bfa', accent2: '#c8b4ff' },
  koredocs: { accent: '#ffd166', accent2: '#ffe3a3' },
  korecomms: { accent: '#ff8fab', accent2: '#ffb4c8' },
};

const APP_THEME_KEYS = {
  koredoc: 'koredocs',
  koresheet: 'koredocs',
  kodiag: 'koredocs',
  korefile: 'koredocs',
};

export function resolveThemeKey(key) {
  if (!key) return null;
  const normalized = String(key).trim().toLowerCase();
  return APP_THEME_KEYS[normalized] || normalized;
}

export function themeFor(key) {
  return SERVICE_THEMES[resolveThemeKey(key)] || null;
}

export function applyTheme(target, key) {
  const theme = themeFor(key);
  const el = target || document?.documentElement || null;
  if (!theme || !el) return theme;

  el.style.setProperty('--app-accent', theme.accent);
  el.style.setProperty('--app-accent-2', theme.accent2);
  el.style.setProperty('--accent', theme.accent);
  el.style.setProperty('--accent-2', theme.accent2);

  if (typeof document !== 'undefined' && el === document.documentElement && document.body) {
    document.body.dataset.koreTheme = resolveThemeKey(key) || '';
  }
  return theme;
}

export function serviceThemes() {
  return Object.fromEntries(
    Object.entries(SERVICE_THEMES).map(([key, value]) => [key, { ...value }]),
  );
}