/**
 * dialogs.js - shared alert/confirm dialog host for Kore suite applications.
 */

let _dialogHostInitialised = false;
let _alertDialog           = null;
let _alertTitle            = null;
let _alertBody             = null;
let _confirmDialog         = null;
let _confirmTitle          = null;
let _confirmBody           = null;
let _confirmOk             = null;

function _dialogButton(label, { value, quiet = false, danger = false } = {}) {
  const button = document.createElement('button');
  button.type = 'button';
  button.value = value || '';
  button.textContent = label;
  button.className = [
    'kcui-btn',
    'kcui-btn-toolbar',
    quiet ? 'kcui-btn-quiet' : 'kcui-btn-filled',
    danger ? 'kcui-btn-danger' : '',
  ].filter(Boolean).join(' ');
  return button;
}

function _dialogField(field) {
  const label = document.createElement('label');
  const control = field.type === 'select'   ? document.createElement('select')
    : field.type === 'textarea' ? document.createElement('textarea')
    :                              document.createElement('input');
  const name = String(field.name || 'value');

  label.className = 'kcui-dialog-label';
  label.textContent = field.label || '';
  control.className = field.type === 'select' ? 'kcui-dialog-select' : 'kcui-dialog-input';
  control.name = name;
  control.id = `kcui-dialog-${name}`;
  control.required = Boolean(field.required);
  control.autocomplete = field.autocomplete || 'off';
  if (field.maxLength) control.maxLength = field.maxLength;

  if (field.type === 'select') {
    for (const optionSpec of field.options || []) {
      const option = document.createElement('option');
      option.value = String(optionSpec.value);
      option.textContent = optionSpec.label || String(optionSpec.value);
      option.selected = String(optionSpec.value) === String(field.value ?? '');
      control.appendChild(option);
    }
  } else if (field.type === 'textarea') {
    control.value = field.value ?? '';
    control.placeholder = field.placeholder || '';
    control.rows = field.rows || 4;
    control.spellcheck = field.spellcheck ?? false;
  } else {
    control.type = field.type || 'text';
    control.value = field.value ?? '';
    control.placeholder = field.placeholder || '';
    control.spellcheck = field.spellcheck ?? false;
  }

  label.htmlFor = control.id;
  return { label: field.label ? label : null, control };
}

function _bindDialogClose(dialog, resolve, mapper) {
  const onClose = () => {
    dialog.removeEventListener('close', onClose);
    resolve(mapper(dialog.returnValue));
  };
  dialog.addEventListener('close', onClose, { once: true });
}

function _buildDialogHost() {
  const host = document.createElement('div');
  host.innerHTML = `
    <dialog id="kcui-alert-dialog" class="kcui-dialog">
      <form method="dialog" class="kcui-dialog-form">
        <h3 id="kcui-alert-title" class="kcui-dialog-title">Notice</h3>
        <p  id="kcui-alert-body"  class="kcui-dialog-body"></p>
        <div class="dlg-actions kcui-dialog-actions">
          <button id="kcui-alert-ok" class="kcui-btn kcui-btn-toolbar kcui-btn-filled" value="ok">OK</button>
        </div>
      </form>
    </dialog>
    <dialog id="kcui-confirm-dialog" class="kcui-dialog">
      <form method="dialog" class="kcui-dialog-form">
        <h3 id="kcui-confirm-title" class="kcui-dialog-title">Confirm</h3>
        <p  id="kcui-confirm-body"  class="kcui-dialog-body"></p>
        <div class="dlg-actions kcui-dialog-actions">
          <button id="kcui-confirm-cancel" class="kcui-btn kcui-btn-toolbar kcui-btn-quiet"  value="cancel">Cancel</button>
          <button id="kcui-confirm-ok"     class="kcui-btn kcui-btn-toolbar kcui-btn-filled kcui-btn-danger" value="ok">Confirm</button>
        </div>
      </form>
    </dialog>
  `;
  document.body.appendChild(host);

  _alertDialog   = host.querySelector('#kcui-alert-dialog');
  _alertTitle    = host.querySelector('#kcui-alert-title');
  _alertBody     = host.querySelector('#kcui-alert-body');
  _confirmDialog = host.querySelector('#kcui-confirm-dialog');
  _confirmTitle  = host.querySelector('#kcui-confirm-title');
  _confirmBody   = host.querySelector('#kcui-confirm-body');
  _confirmOk     = host.querySelector('#kcui-confirm-ok');
}

export function initDialogHost() {
  if (_dialogHostInitialised) return;
  _dialogHostInitialised = true;
  _buildDialogHost();

  window.kcuiAlert = function kcuiAlert(title, message = '') {
    return new Promise(resolve => {
      _alertTitle.textContent = title || 'Notice';
      _alertBody.textContent  = message || '';
      _bindDialogClose(_alertDialog, resolve, () => true);
      _alertDialog.showModal();
    });
  };

  window.kcuiConfirm = function kcuiConfirm(title, message = '', options = {}) {
    return new Promise(resolve => {
      _confirmTitle.textContent = title || 'Confirm';
      _confirmBody.textContent  = message || '';
      _confirmOk.textContent    = String(options.confirmLabel || 'Confirm');
      _bindDialogClose(_confirmDialog, resolve, value => value === 'ok');
      _confirmDialog.showModal();
    });
  };

  window.kcuiForm = kcuiForm;
  window.kcuiPrompt = kcuiPrompt;
}

export function kcuiAlert(title, message = '') {
  initDialogHost();
  return window.kcuiAlert(title, message);
}

export function kcuiConfirm(title, message = '', options = {}) {
  initDialogHost();
  return window.kcuiConfirm(title, message, options);
}

/**
 * Present a small, consistently styled form.  Resolves to named values, or
 * null if the user cancels it.  Use this for routine input rather than a
 * browser prompt or a subsystem-owned modal.
 */
export function kcuiForm(title, options = {}) {
  initDialogHost();

  const dialog  = document.createElement('dialog');
  const form    = document.createElement('form');
  const heading = document.createElement('h3');
  const message = document.createElement('p');
  const actions = document.createElement('div');
  const fields  = [];
  const specs   = Array.isArray(options.fields) ? options.fields : [];

  dialog.className = 'kcui-dialog';
  form.className = 'kcui-dialog-form';
  heading.className = 'kcui-dialog-title';
  heading.textContent = title || 'Input';
  form.appendChild(heading);

  if (options.message) {
    message.className = 'kcui-dialog-body';
    message.textContent = options.message;
    form.appendChild(message);
  }

  for (const spec of specs) {
    const field = _dialogField(spec);
    fields.push(field);
    if (field.label) form.appendChild(field.label);
    form.appendChild(field.control);
  }

  actions.className = 'dlg-actions kcui-dialog-actions';
  const cancel = _dialogButton(options.cancelLabel || 'Cancel', { value: 'cancel', quiet: true });
  const confirm = _dialogButton(options.confirmLabel || 'OK', {
    value:  'ok',
    danger: options.danger === true,
  });
  actions.append(cancel, confirm);
  form.appendChild(actions);
  dialog.appendChild(form);
  document.body.appendChild(dialog);

  return new Promise(resolve => {
    let settled = false;
    let result  = null;
    const finish = value => {
      if (settled) return;
      settled = true;
      dialog.remove();
      resolve(value);
    };
    const cancelDialog = () => dialog.close('cancel');

    cancel.addEventListener('click', cancelDialog);
    form.addEventListener('submit', event => {
      event.preventDefault();
      confirm.click();
    });
    confirm.addEventListener('click', () => {
      if (!form.reportValidity()) return;
      const values = Object.fromEntries(fields.map(({ control }) => [control.name, control.value.trim()]));
      const validationError = typeof options.validate === 'function' ? options.validate(values) : null;
      if (validationError) {
        window.kcuiAlert(title || 'Input', validationError);
        return;
      }
      result = values;
      dialog.close('ok');
    });
    dialog.addEventListener('cancel', event => {
      event.preventDefault();
      cancelDialog();
    });
    dialog.addEventListener('close', () => finish(result), { once: true });
    dialog.showModal();
    const firstControl = fields[0]?.control;
    firstControl?.focus();
    if (firstControl instanceof HTMLInputElement && firstControl.type !== 'password') firstControl.select();
  });
}

/** Show a single text input using the shared dialog host. */
export async function kcuiPrompt(title, options = {}) {
  const values = await kcuiForm(title, {
    ...options,
    fields: [{
      name:         'value',
      label:        options.label || '',
      type:         options.type || 'text',
      value:        options.initial || '',
      placeholder:  options.placeholder || '',
      required:     options.required ?? true,
      autocomplete: options.autocomplete,
      spellcheck:   options.spellcheck,
    }],
  });
  return values?.value ?? null;
}

/**
 * Present a small decision dialog.  Optional details are hidden behind a
 * comparison control, which keeps conflict resolution consistent without
 * forcing callers to own a modal implementation.
 */
export function kcuiChoice(title, message = '', options = {}) {
  initDialogHost();

  const dialog  = document.createElement('dialog');
  const form    = document.createElement('form');
  const heading = document.createElement('h3');
  const body    = document.createElement('p');
  const details = document.createElement('div');
  const actions = document.createElement('div');
  const choices = Array.isArray(options.choices) ? options.choices : [];

  dialog.className = 'kcui-dialog kcui-dialog--choice';
  form.className = 'kcui-dialog-form';
  heading.className = 'kcui-dialog-title';
  heading.textContent = title || 'Choose an action';
  body.className = 'kcui-dialog-body';
  body.textContent = message;
  form.append(heading, body);

  const detailItems = Array.isArray(options.details) ? options.details : [];
  if (detailItems.length) {
    details.className = 'kcui-dialog-details';
    details.hidden = true;
    for (const detail of detailItems) {
      const item = document.createElement('section');
      const label = document.createElement('h4');
      const content = document.createElement('pre');
      label.textContent = detail.label || 'Details';
      content.textContent = detail.content || '';
      item.append(label, content);
      details.appendChild(item);
    }
    form.appendChild(details);
  }

  actions.className = 'dlg-actions kcui-dialog-actions';
  if (detailItems.length) {
    const compare = _dialogButton(options.detailsLabel || 'Compare', { quiet: true });
    compare.addEventListener('click', () => { details.hidden = !details.hidden; });
    actions.appendChild(compare);
  }
  const buttons = choices.map(choice => {
    const button = _dialogButton(choice.label || choice.value, choice);
    actions.appendChild(button);
    return { ...choice, button };
  });
  form.appendChild(actions);
  dialog.appendChild(form);
  document.body.appendChild(dialog);

  return new Promise(resolve => {
    let settled = false;
    let result  = null;
    const finish = value => {
      if (settled) return;
      settled = true;
      dialog.remove();
      resolve(value);
    };
    const defaultValue = options.cancelValue ?? choices[0]?.value ?? null;

    for (const choice of buttons) {
      choice.button.addEventListener('click', () => {
        result = choice.value;
        dialog.close(choice.value);
      });
    }
    dialog.addEventListener('cancel', event => {
      event.preventDefault();
      result = defaultValue;
      dialog.close(String(result ?? ''));
    });
    dialog.addEventListener('close', () => finish(result ?? defaultValue), { once: true });
    dialog.showModal();
    buttons.at(-1)?.button.focus();
  });
}

export function bindConfirmActions(root = document) {
  initDialogHost();
  root.querySelectorAll('[data-kcui-confirm]').forEach((node) => {
    if (node.dataset.kcuiConfirmBound === '1') return;
    node.dataset.kcuiConfirmBound = '1';
    node.addEventListener('click', async (event) => {
      event.preventDefault();
      event.stopPropagation();
      const message = node.getAttribute('data-kcui-confirm') || 'Continue?';
      const title = node.getAttribute('data-kcui-confirm-title') || 'Confirm';
      const confirmLabel = node.getAttribute('data-kcui-confirm-label') || 'Confirm';
      const ok = await kcuiConfirm(title, message, { confirmLabel });
      if (!ok) return;
      const form = node.closest('form');
      if (form instanceof HTMLFormElement) {
        form.requestSubmit(node instanceof HTMLButtonElement ? node : undefined);
        return;
      }
      if (node instanceof HTMLAnchorElement && node.href) {
        window.location.href = node.href;
      }
    });
  });
}
