const STORAGE_KEY = 'korecode:workbench-layout';

function loadLayout() {
  try {
    return { explorer: true, assistant: true, console: true, assistantDock: 'right', ...JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') };
  } catch {
    return { explorer: true, assistant: true, console: true, assistantDock: 'right' };
  }
}

export function initWorkbenchLayout({ onAssistantHidden = null } = {}) {
  const app       = document.getElementById('code-app');
  const button    = document.getElementById('btn-layout');
  const popover   = document.getElementById('layout-popover');
  const explorer  = document.getElementById('layout-explorer');
  const assistant = document.getElementById('layout-assistant');
  const consoleEl = document.getElementById('layout-console');
  const dock      = document.getElementById('layout-assistant-dock');
  let layout      = loadLayout();

  function save() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  }

  function apply() {
    app.classList.toggle('is-explorer-hidden', !layout.explorer);
    app.classList.toggle('is-assistant-hidden', !layout.assistant);
    app.classList.toggle('is-console-hidden', !layout.console);
    app.classList.toggle('is-chat-left', layout.assistantDock === 'left');
    explorer.checked = layout.explorer;
    assistant.checked = layout.assistant;
    consoleEl.checked = layout.console;
    dock.value = layout.assistantDock;
    if (!layout.assistant) onAssistantHidden?.();
  }

  button.addEventListener('click', () => {
    popover.hidden = !popover.hidden;
    button.classList.toggle('is-active', !popover.hidden);
  });
  document.addEventListener('click', (event) => {
    if (!popover.hidden && !popover.contains(event.target) && event.target !== button) {
      popover.hidden = true;
      button.classList.remove('is-active');
    }
  });
  explorer.addEventListener('change', () => { layout.explorer = explorer.checked; save(); apply(); });
  assistant.addEventListener('change', () => { layout.assistant = assistant.checked; save(); apply(); });
  consoleEl.addEventListener('change', () => { layout.console = consoleEl.checked; save(); apply(); });
  dock.addEventListener('change', () => { layout.assistantDock = dock.value; save(); apply(); });
  apply();
}
