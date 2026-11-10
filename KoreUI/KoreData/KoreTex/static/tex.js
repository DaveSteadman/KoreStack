import { initKoreDataShell } from '/ui-elements/assets/js/chrome.js';

initKoreDataShell({ section: 'tex', urls: window.__koreSuiteUrls || {} });

const form                = document.getElementById('tex-form');
const source              = document.getElementById('source-text');
const systemPrompt        = document.getElementById('system-prompt');
const button              = document.getElementById('process-button');
const modelStatus         = document.getElementById('model-status');
const processStatus       = document.getElementById('process-status');
const result              = document.getElementById('json-result');
const expansionForm       = document.getElementById('expansion-form');
const expansionPrompt     = document.getElementById('expansion-prompt');
const expandButton        = document.getElementById('expand-button');
const expansionStatus     = document.getElementById('expansion-status');
const expandedResult      = document.getElementById('expanded-result');
let ready                 = false;
let busy                  = false;
let checking              = false;
let extractedSource       = null;
let extractedStatements   = [];

function updateButton() {
  button.disabled = busy || !ready || !source.value.trim() || !source.validity.valid
    || !systemPrompt.value.trim() || !systemPrompt.validity.valid;
  expandButton.disabled = busy || !ready || !extractedStatements.length
    || source.value !== extractedSource || !expansionPrompt.value.trim()
    || !expansionPrompt.validity.valid;
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

source.addEventListener('input', () => {
  if (extractedSource !== null && source.value !== extractedSource) {
    expansionStatus.textContent = 'Source text changed. Process it again before expanding statements.';
  }
  updateButton();
});
systemPrompt.addEventListener('input', updateButton);
expansionPrompt.addEventListener('input', updateButton);
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy || button.disabled) return;
  busy = true;
  extractedSource = null;
  extractedStatements = [];
  source.disabled = true;
  systemPrompt.disabled = true;
  form.setAttribute('aria-busy', 'true');
  updateButton();
  result.textContent = '';
  expandedResult.value = '';
  expansionStatus.className = 'kcui-field__hint';
  expansionStatus.textContent = 'Waiting for evidence-based statements.';
  processStatus.className = 'kcui-field__hint';
  processStatus.textContent = 'Processing...';
  try {
    const originalText = source.value;
    const response = await fetch('/api/process', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: originalText, system_prompt: systemPrompt.value }),
      signal: AbortSignal.timeout(320000),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === 'string' ? data.detail : 'Processing failed. Check your text and try again.');
    }
    result.textContent = JSON.stringify(data.result, null, 2);
    extractedSource = originalText;
    extractedStatements = Array.isArray(data.result?.statements) ? data.result.statements : [];
    processStatus.textContent = `Preview generated using ${data.model}. Nothing has been saved.`;
    expansionStatus.textContent = extractedStatements.length
      ? `${extractedStatements.length} statement${extractedStatements.length === 1 ? '' : 's'} ready to expand.`
      : 'No factual statements were extracted to expand.';
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

expansionForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy || expandButton.disabled) return;
  busy = true;
  source.disabled = true;
  systemPrompt.disabled = true;
  expansionPrompt.disabled = true;
  expansionForm.setAttribute('aria-busy', 'true');
  updateButton();

  const statements = [...extractedStatements];
  const originalText = extractedSource;
  const prompt = expansionPrompt.value;
  const collated = [];
  const collatedByEvidence = new Map();
  const expansionWarnings = [];
  let currentStatement = 0;
  expandedResult.value = '';
  expandedResult.readOnly = true;
  expansionStatus.className = 'kcui-field__hint';
  try {
    for (const [index, original] of statements.entries()) {
      currentStatement = index + 1;
      expansionStatus.textContent = `Expanding statement ${index + 1} of ${statements.length}...`;
      const response = await fetch('/api/expand-statement', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: originalText, statement: original, expansion_prompt: prompt }),
        signal: AbortSignal.timeout(320000),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Statement expansion failed.');
      }
      for (const warning of data.warnings || []) {
        expansionWarnings.push(`Statement ${index + 1}: ${warning}`);
      }
      for (const item of data.result.statements) {
        const key = `${item.statement.trim().toLowerCase()}\n${item.source_excerpt.trim()}`;
        const existing = collatedByEvidence.get(key);
        if (existing) {
          if (!existing.expanded_from_indices.includes(index + 1)) {
            existing.expanded_from.push(original.statement);
            existing.expanded_from_excerpts.push(original.source_excerpt);
            existing.expanded_from_indices.push(index + 1);
          }
        } else {
          const entry = {
            statement: item.statement,
            source_excerpt: item.source_excerpt,
            expanded_from: [original.statement],
            expanded_from_excerpts: [original.source_excerpt],
            expanded_from_indices: [index + 1],
          };
          collatedByEvidence.set(key, entry);
          collated.push(entry);
        }
      }
      expandedResult.value = JSON.stringify({ statements: collated, warnings: expansionWarnings }, null, 2);
    }
    let finalStatements = collated;
    if (collated.length) {
      expansionStatus.textContent = `Deduplicating ${collated.length} collated statements...`;
      try {
        const response = await fetch('/api/collate-statements', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: originalText, statements: collated }),
          signal: AbortSignal.timeout(320000),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(typeof data.detail === 'string' ? data.detail : 'Final deduplication failed.');
        }
        finalStatements = data.result.statements;
        expansionWarnings.push(...(data.warnings || []));
      } catch (error) {
        expansionWarnings.push(`Final deduplication failed: ${error.message} The collated statements were retained.`);
      }
    }
    expandedResult.value = JSON.stringify({ statements: finalStatements, warnings: expansionWarnings }, null, 2);
    const warningCount = expansionWarnings.length;
    expansionStatus.textContent = `${finalStatements.length} sourced statement${finalStatements.length === 1 ? '' : 's'} from ${statements.length} expansion calls${collated.length ? ' and 1 deduplication call' : ''}. ${warningCount ? `${warningCount} warning${warningCount === 1 ? '' : 's'} in the JSON. ` : ''}Nothing has been saved.`;
  } catch (error) {
    expansionStatus.className = 'kcui-field__error';
    expansionStatus.textContent = `Stopped at statement ${currentStatement} of ${statements.length}: ${error.message} ${collated.length} sourced statements are available from completed calls.`;
  } finally {
    busy = false;
    ready = false;
    source.disabled = false;
    systemPrompt.disabled = false;
    expansionPrompt.disabled = false;
    expandedResult.readOnly = false;
    expansionForm.setAttribute('aria-busy', 'false');
    updateButton();
    await checkReadiness();
  }
});

checkReadiness();
setInterval(checkReadiness, 3000);
window.addEventListener('focus', checkReadiness);
