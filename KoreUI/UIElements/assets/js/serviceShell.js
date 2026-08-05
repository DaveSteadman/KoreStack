import { initTopbar } from './topbar.js';
import { initAppBar } from './appbar.js';

function shellUrls(urls) {
  return urls || (typeof window !== 'undefined' ? (window.__koreSuiteUrls || {}) : {});
}

function baseUrl(value) {
  return String(value || '').replace(/\/+$/, '');
}

function currentPath(path) {
  if (typeof path === 'string' && path.length > 0) return path;
  if (typeof window !== 'undefined' && window.location?.pathname) return window.location.pathname;
  return '/';
}

function tabsWithActiveState(tabs, section) {
  return tabs.map((tab) => ({
    ...tab,
    active: tab.key === section || (Array.isArray(tab.activeSections) && tab.activeSections.includes(section)),
  }));
}

export function initServiceShell(options = {}) {
  const {
    currentService,
    urls,
    path,
    shellMeta = {},
    shellTabs = [],
    section,
    resolveSection,
    topbarOptions = {},
    appBarOptions = {},
  } = options;

  const suiteUrls       = shellUrls(urls);
  const resolvedPath    = currentPath(path);
  const resolvedSection = section || (typeof resolveSection === 'function' ? resolveSection(resolvedPath) : null);
  const sectionMeta     = shellMeta[resolvedSection] || {};

  initTopbar({
    currentService,
    urls: suiteUrls,
    ...topbarOptions,
  });

  initAppBar({
    currentService,
    ...sectionMeta,
    chips: [],
    tabs: tabsWithActiveState(shellTabs, resolvedSection),
    ...appBarOptions,
  });

  return {
    path:    resolvedPath,
    section: resolvedSection,
    tabs:    tabsWithActiveState(shellTabs, resolvedSection),
    urls:    suiteUrls,
  };
}

export function initKoreDataShell(options = {}) {
  const {
    urls,
    path,
    section,
    graphBaseOverride,
    topbarOptions,
    appBarOptions,
  } = options;

  const suiteUrls     = shellUrls(urls);
  const gatewayBase   = baseUrl(suiteUrls.koredatagateway || suiteUrls.koredata);
  const feedBase      = baseUrl(suiteUrls.korefeed        || gatewayBase);
  const libraryBase   = baseUrl(suiteUrls.korelibrary     || gatewayBase);
  const referenceBase = baseUrl(suiteUrls.korereference   || gatewayBase);
  const ragBase       = baseUrl(suiteUrls.korerag         || gatewayBase);
  const scrapeBase    = baseUrl(suiteUrls.korescrape      || gatewayBase);
  const graphBase     = baseUrl(suiteUrls.koregraph       || graphBaseOverride || gatewayBase);

  const shellMeta = {
    home:      { brandLabel: 'KoreData',      overline: 'Data Gateway',    brandIcon: 'koredata' },
    feeds:     { brandLabel: 'KoreFeed',      overline: 'News Feeds',      brandIcon: 'korefeed' },
    library:   { brandLabel: 'KoreLibrary',   overline: 'Books',           brandIcon: 'korelibrary' },
    reference: { brandLabel: 'KoreReference', overline: 'Reference',       brandIcon: 'korereference' },
    rag:       { brandLabel: 'KoreRAG',       overline: 'RAG Chunks',      brandIcon: 'korerag' },
    scrape:    { brandLabel: 'KoreScrape',    overline: 'Web Snapshots',   brandIcon: 'korescrape' },
    graph:     { brandLabel: 'KoreGraph',     overline: 'Knowledge Graph', brandIcon: 'koregraph' },
  };

  const shellTabs = [
    { key: 'home',      label: 'Home',      href: `${gatewayBase}/ui` },
    { key: 'feeds',     label: 'Feeds',     href: `${feedBase}/ui/feeds` },
    { key: 'library',   label: 'Library',   href: `${libraryBase}/ui/library` },
    { key: 'reference', label: 'Reference', href: `${referenceBase}/ui/reference` },
    { key: 'rag',       label: 'RAG',       href: `${ragBase}/ui/rag/databases` },
    { key: 'scrape',    label: 'Scrape',    href: `${scrapeBase}/ui/scrape` },
    { key: 'graph',     label: 'Graph',     href: `${graphBase}/ui/vocab` },
  ];

  return initServiceShell({
    currentService: 'koredata',
    urls:           suiteUrls,
    path,
    section,
    shellMeta,
    shellTabs,
    resolveSection(resolvedPath) {
      return resolvedPath.startsWith('/ui/feeds')     ? 'feeds'
           : resolvedPath.startsWith('/ui/library')   ? 'library'
           : resolvedPath.startsWith('/ui/reference') ? 'reference'
           : resolvedPath.startsWith('/ui/rag')       ? 'rag'
           : resolvedPath.startsWith('/ui/scrape')    ? 'scrape'
           : resolvedPath.startsWith('/graph')        ? 'graph'
           : 'home';
    },
    topbarOptions,
    appBarOptions,
  });
}

export function initKoreCommsShell(options = {}) {
  const {
    urls,
    path,
    section,
    topbarOptions,
    appBarOptions,
  } = options;

  return initServiceShell({
    currentService: 'korecomms',
    urls,
    path,
    section,
    shellMeta: {
      conversations: { brandLabel: 'KoreComms', overline: 'External Messaging', brandIcon: 'korecomms' },
      compose:       { brandLabel: 'KoreComms', overline: 'External Messaging', brandIcon: 'korecomms' },
      connections:   { brandLabel: 'KoreComms', overline: 'External Messaging', brandIcon: 'korecomms' },
      activity:      { brandLabel: 'KoreComms', overline: 'External Messaging', brandIcon: 'korecomms' },
    },
    shellTabs: [
      { key: 'conversations', label: 'Conversations', href: '/' },
      { key: 'compose',       label: 'Compose',       href: '/compose' },
      { key: 'connections',   label: 'Connections',   href: '/connections' },
      { key: 'activity',      label: 'Activity',      href: '/activity' },
    ],
    resolveSection(resolvedPath) {
      return resolvedPath === '/compose'                ? 'compose'
           : resolvedPath.startsWith('/connections')    ? 'connections'
           : resolvedPath === '/activity'               ? 'activity'
           : 'conversations';
    },
    topbarOptions,
    appBarOptions,
  });
}

export function initKoreLiveWebShell(options = {}) {
  const {
    urls,
    path,
    section,
    topbarOptions,
    appBarOptions,
  } = options;

  return initServiceShell({
    currentService: 'koreliveweb',
    urls,
    path,
    section,
    shellMeta: {
      monitor: { brandLabel: 'KoreLiveWeb', overline: 'Live Web MCP', brandIcon: 'koreliveweb' },
      mcp:     { brandLabel: 'KoreLiveWeb', overline: 'Live Web MCP', brandIcon: 'koreliveweb' },
      status:  { brandLabel: 'KoreLiveWeb', overline: 'Live Web MCP', brandIcon: 'koreliveweb' },
    },
    shellTabs: [
      { key: 'monitor', label: 'Monitor', href: '/ui' },
      { key: 'mcp',     label: 'MCP',     href: '/mcp' },
      { key: 'status',  label: 'Status',  href: '/status' },
    ],
    resolveSection(resolvedPath) {
      return resolvedPath.startsWith('/mcp')    ? 'mcp'
           : resolvedPath.startsWith('/status') ? 'status'
           : 'monitor';
    },
    topbarOptions,
    appBarOptions,
  });
}
