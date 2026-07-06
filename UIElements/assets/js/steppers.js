function finiteNumber(value, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function resolveStepperInput(button) {
  const stepper = button?.closest?.('.kcui-num-stepper, .num-stepper');
  const input = stepper?.querySelector?.('input[type="number"]');
  if (!(input instanceof HTMLInputElement)) {
    throw new Error('Number stepper button must live inside .kcui-num-stepper or .num-stepper');
  }
  return input;
}

export function stepNumberInput(button, delta) {
  const input = resolveStepperInput(button);
  const min = input.min !== '' ? finiteNumber(input.min, -Infinity) : -Infinity;
  const max = input.max !== '' ? finiteNumber(input.max, Infinity) : Infinity;
  const step = input.step !== '' ? finiteNumber(input.step, 1) : 1;

  let value = finiteNumber(input.value, NaN);
  if (!Number.isFinite(value)) {
    value = Number.isFinite(min) ? min : 0;
  }

  const nextValue = Math.min(max, Math.max(min, value + delta * step));
  input.value = Number.isInteger(nextValue) ? String(nextValue) : String(nextValue);
  input.dispatchEvent(new Event('input', { bubbles: true }));

  if (input.classList.contains('rate-input') && typeof window.saveRate === 'function') {
    window.saveRate(input);
  }

  return input;
}

export function installNumberStepperGlobals(globalName = 'stepNum') {
  window[globalName] = stepNumberInput;
  window.kcuiStepNum = stepNumberInput;
}

export function bindNumberStepperPairControls(root = document) {
  root.querySelectorAll('.num-stepper, .kcui-num-stepper').forEach((wrapper) => {
    const input = wrapper.querySelector('input[type="number"]');
    if (!(input instanceof HTMLInputElement)) return;

    wrapper.querySelector('.step-up')?.addEventListener('click', () => {
      stepNumberInput(wrapper.querySelector('.step-up'), 1);
    });
    wrapper.querySelector('.step-dn')?.addEventListener('click', () => {
      stepNumberInput(wrapper.querySelector('.step-dn'), -1);
    });
  });
}