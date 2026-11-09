import { initKoreDataShell } from '/ui-elements/assets/js/chrome.js';

initKoreDataShell({ section: 'tex', urls: window.__koreSuiteUrls || {} });

const form = document.getElementById('tex-form');
const source = document.getElementById('source-text');
const systemPrompt = document.getElementById('system-prompt');
const button = document.getElementById('process-button');
const modelStatus = document.getElementById('model-status');
const processStatus = document.getElementById('process-status');
const result = document.getElementById('json-result');
let ready = false;
let busy = false;
let checking = false;

function updateButton() {
  button.disabled = busy || !ready || !source.value.trim() || !source.validity.valid
    || !systemPrompt.value.trim() || !systemPrompt.validity.valid;
}

async function checkReadiness() {
  if (checking) return;
  checking = true;
  try {
    const response = await fetch('/api/readiness', { cache: 'no-store', signal: AbortSignal.timeout(12000) });
    if (!response.ok) throw new Error('Cannot check model availability.');
    const state = await response.json();
    ready = state.ready === true;
    modelStatus.textContent = ready ? `Ready: ${state.model}` : state.reason;
  } catch (error) {
    ready = false;
    modelStatus.textContent = `Processing unavailable: ${error.message}`;
  } finally {
    checking = false;
    updateButton();
  }
}

source.addEventListener('input', updateButton);
systemPrompt.addEventListener('input', updateButton);
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy || button.disabled) return;
  busy = true;
  source.disabled = true;
  systemPrompt.disabled = true;
  form.setAttribute('aria-busy', 'true');
  updateButton();
  result.textContent = '';
  processStatus.className = 'kcui-field__hint';
  processStatus.textContent = 'Processing...';
  try {
    const response = await fetch('/api/process', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: source.value, system_prompt: systemPrompt.value }),
      signal: AbortSignal.timeout(320000),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === 'string' ? data.detail : 'Processing failed. Check your text and try again.');
    }
    result.textContent = JSON.stringify(data.result, null, 2);
    processStatus.textContent = `Preview generated using ${data.model}. Nothing has been saved.`;
  } catch (error) {
    processStatus.className = 'kcui-field__error';
    processStatus.textContent = error.message;
  } finally {
    busy = false;
    ready = false;
    source.disabled = false;
    systemPrompt.disabled = false;
    form.setAttribute('aria-busy', 'false');
    updateButton();
    await checkReadiness();
  }
});

checkReadiness();
setInterval(checkReadiness, 3000);
window.addEventListener('focus', checkReadiness);
