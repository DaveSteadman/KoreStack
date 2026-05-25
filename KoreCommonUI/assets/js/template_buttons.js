/**
 * template_buttons.js
 *
 * Renders button rows into explicit template page mount points so the page
 * layout remains copy/paste-visible while button construction stays centralized.
 */

function createActionButton(spec) {
  const button = document.createElement('button');
  const classes = ['kc-action-btn'];

  if (spec.tone) classes.push(`kc-action-btn--${spec.tone}`);
  if (spec.iconOnly) classes.push('kc-iconbtn');
  if (spec.active) classes.push('is-active');
  if (spec.isDisabledClass) classes.push('is-disabled');

  button.className = classes.join(' ');
  button.type = 'button';

  if (spec.disabled) button.disabled = true;
  if (spec.ariaDisabled) button.setAttribute('aria-disabled', 'true');
  if (spec.ariaLabel) button.setAttribute('aria-label', spec.ariaLabel);

  if (spec.iconClass) {
    const icon = document.createElement('span');
    icon.className = `kc-icon ${spec.iconClass}`;
    icon.setAttribute('aria-hidden', 'true');
    button.appendChild(icon);
  }

  if (spec.label) {
    button.appendChild(document.createTextNode(spec.label));
  }

  return button;
}

function appendButtonRow(target, specs) {
  if (!target) return;
  target.textContent = '';
  for (const spec of specs) {
    target.appendChild(createActionButton(spec));
  }
}

function renderButtonDemoRows() {
  appendButtonRow(document.getElementById('kc-demo-row-variants'), [
    { label: 'Default' },
    { label: 'Accent', tone: 'accent' },
    { label: 'Info', tone: 'info' },
    { label: 'Warning', tone: 'warning' },
    { label: 'Danger', tone: 'danger' },
    { label: 'Muted', tone: 'muted' },
  ]);

  appendButtonRow(document.getElementById('kc-demo-row-states'), [
    { label: 'is-active', tone: 'accent', active: true },
    { label: 'is-active', tone: 'info', active: true },
    { label: 'disabled', tone: 'accent', disabled: true },
    { label: 'is-disabled', tone: 'danger', isDisabledClass: true, ariaDisabled: true },
  ]);

  appendButtonRow(document.getElementById('kc-demo-row-icons'), [
    { iconOnly: true, ariaLabel: 'KoreStack', iconClass: 'kc-icon--stack' },
    { iconOnly: true, tone: 'accent', ariaLabel: 'KoreAgent', iconClass: 'kc-icon--agent' },
    { iconOnly: true, tone: 'danger', ariaLabel: 'KoreComms', iconClass: 'kc-icon--comms' },
    { iconOnly: true, tone: 'warning', ariaLabel: 'KoreData', iconClass: 'kc-icon--data' },
    { iconOnly: true, tone: 'info', ariaLabel: 'KoreDocs', iconClass: 'kc-icon--docs' },
    { iconOnly: true, tone: 'muted', ariaLabel: 'KoreCode', iconClass: 'kc-icon--code' },
  ]);

  appendButtonRow(document.getElementById('kc-demo-row-arrows'), [
    { iconOnly: true, ariaLabel: 'Up', label: '↑' },
    { iconOnly: true, ariaLabel: 'Down', label: '↓' },
    { iconOnly: true, ariaLabel: 'Left', label: '←' },
    { iconOnly: true, ariaLabel: 'Right', label: '→' },
  ]);
}

function renderPanelButtons() {
  appendButtonRow(document.getElementById('kc-panel-header-actions'), [
    { label: 'Edit', tone: 'muted' },
    { label: 'Delete', tone: 'danger' },
  ]);

  appendButtonRow(document.getElementById('kc-panel-footer-actions-left'), [
    { label: 'Cancel' },
  ]);

  appendButtonRow(document.getElementById('kc-panel-footer-actions-right'), [
    { label: 'Preview', tone: 'info' },
    { label: 'Save', tone: 'accent' },
  ]);
}

function initTemplateButtons() {
  renderPanelButtons();
  renderButtonDemoRows();
}

initTemplateButtons();
