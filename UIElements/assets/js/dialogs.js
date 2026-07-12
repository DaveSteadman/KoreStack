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
          <button id="kcui-alert-ok" class="kcui-dialog-btn kcui-dialog-btn--primary" value="ok">OK</button>
        </div>
      </form>
    </dialog>
    <dialog id="kcui-confirm-dialog" class="kcui-dialog">
      <form method="dialog" class="kcui-dialog-form">
        <h3 id="kcui-confirm-title" class="kcui-dialog-title">Confirm</h3>
        <p  id="kcui-confirm-body"  class="kcui-dialog-body"></p>
        <div class="dlg-actions kcui-dialog-actions">
          <button id="kcui-confirm-cancel" class="kcui-dialog-btn kcui-dialog-btn--ghost"  value="cancel">Cancel</button>
          <button id="kcui-confirm-ok"     class="kcui-dialog-btn kcui-dialog-btn--danger" value="ok">Confirm</button>
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
}

export function kcuiAlert(title, message = '') {
  initDialogHost();
  return window.kcuiAlert(title, message);
}

export function kcuiConfirm(title, message = '', options = {}) {
  initDialogHost();
  return window.kcuiConfirm(title, message, options);
}
