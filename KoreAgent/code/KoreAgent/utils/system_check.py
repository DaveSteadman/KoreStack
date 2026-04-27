# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Diagnostic utility for monitoring memory usage while running Ollama model prompts.
#
# Spawns a background thread that samples the Ollama process RSS and total system RAM at a
# configurable interval. A baseline is established before each model call and the peak and delta
# values are reported afterwards. This helps characterise how much memory each model requires at
# inference time and whether it fits within available system resources.
#
# Usage:
#   python code/utils/system_check.py
#   python code/utils/system_check.py --ctx 4096
#
# Related modules:
#   - llm_client.py  -- provides model listing, resolution, runtime reporting, and call_ollama
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import argparse
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psutil

from KoreAgent.llm_client import call_ollama
from KoreAgent.llm_client import ensure_ollama_running
from KoreAgent.llm_client import format_running_model_report
from KoreAgent.llm_client import list_ollama_models
from KoreAgent.llm_client import resolve_model_name
from KoreAgent.utils.workspace_utils import trunc


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
MODELS            = ["20b", "120b"]
PROMPT            = "hello world"
SAMPLE_SECONDS    = 0.25
BASELINE_SECONDS  = 2.0
SETTLE_SECONDS    = 2.0


# ====================================================================================================
# MARK: CLI + HELPERS
# ====================================================================================================
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor memory while running model prompts.")
    parser.add_argument(
        "--ctx",
        type=int,
        default=None,
        help="Optional context window size to request (maps to Ollama num_ctx).",
    )
    return parser.parse_args()


# ----------------------------------------------------------------------------------------------------
def _bytes_to_gb(byte_count: int) -> float:
    return byte_count / (1024 ** 3)


# ----------------------------------------------------------------------------------------------------
def _sample_ollama_rss_bytes() -> int:
    total = 0
    for process in psutil.process_iter(["name", "memory_info"]):
        process_name = (process.info.get("name") or "").lower()
        if "ollama" not in process_name:
            continue

        try:
            memory_info = process.memory_info()
            total += memory_info.rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return total


# ----------------------------------------------------------------------------------------------------
def _sample_system_used_bytes() -> int:
    return psutil.virtual_memory().used


# ====================================================================================================
# MARK: MEMORY SAMPLER
# ====================================================================================================
class MemorySampler:
    def __init__(self, interval_seconds: float):
        self.interval_seconds = interval_seconds
        self._running         = False
        self._thread          = None
        self.ollama_samples   = []
        self.system_samples   = []

    # ----------------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ----------------------------------------------------------------------------------------------------
    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ----------------------------------------------------------------------------------------------------
    def _run(self) -> None:
        while self._running:
            self.ollama_samples.append(_sample_ollama_rss_bytes())
            self.system_samples.append(_sample_system_used_bytes())
            time.sleep(self.interval_seconds)


# ====================================================================================================
# MARK: REPORTING WORKFLOW
# ====================================================================================================
def _summarize_memory(samples: list[int]) -> tuple[int, int, int]:
    # Use the early samples as the baseline so pre-call idle memory is captured accurately.
    baseline = int(statistics.median(samples[: max(1, int(BASELINE_SECONDS / SAMPLE_SECONDS))]))
    peak     = max(samples)
    delta    = peak - baseline
    return baseline, peak, delta


# ----------------------------------------------------------------------------------------------------
def run_model_check(model_name: str, prompt: str, num_ctx: int | None = None) -> None:
    sampler = MemorySampler(interval_seconds=SAMPLE_SECONDS)
    sampler.start()

    time.sleep(BASELINE_SECONDS)

    response_preview = ""
    request_error    = ""

    try:
        response_preview = call_ollama(model_name=model_name, prompt=prompt, num_ctx=num_ctx).strip()
    except Exception as error:
        request_error = str(error)

    time.sleep(SETTLE_SECONDS)
    sampler.stop()

    if not sampler.ollama_samples or not sampler.system_samples:
        print(f"{model_name}: no memory samples collected")
        return

    ollama_baseline, ollama_peak, ollama_delta = _summarize_memory(sampler.ollama_samples)
    system_baseline, system_peak, system_delta = _summarize_memory(sampler.system_samples)

    print(f"\n=== Model: {model_name} ===")
    print(f"Ollama RSS baseline: {_bytes_to_gb(ollama_baseline):.2f} GB")
    print(f"Ollama RSS peak:     {_bytes_to_gb(ollama_peak):.2f} GB")
    print(f"Ollama RSS delta:    {_bytes_to_gb(ollama_delta):.2f} GB")
    print(f"System RAM baseline: {_bytes_to_gb(system_baseline):.2f} GB")
    print(f"System RAM peak:     {_bytes_to_gb(system_peak):.2f} GB")
    print(f"System RAM delta:    {_bytes_to_gb(system_delta):.2f} GB")

    if request_error:
        print(f"Request result:      ERROR - {request_error}")
    else:
        print(f"Request result:      OK - {trunc(response_preview, 100)}")


# ----------------------------------------------------------------------------------------------------
def main() -> None:
    args = _parse_args()

    print("Monitoring memory while running model prompts...")
    if args.ctx is not None:
        print(f"Requested context window (num_ctx): {args.ctx}")

    ensure_ollama_running()
    available_models = list_ollama_models()

    if not available_models:
        print("No models are installed in Ollama. Pull models first, then rerun.")
        return

    for model_name in MODELS:
        resolved_model = resolve_model_name(model_name, available_models)
        if resolved_model is None:
            print(f"\n=== Model: {model_name} ===")
            print(f"Skipped: model not installed. Available: {', '.join(available_models)}")
            continue

        if resolved_model != model_name:
            print(f"\n=== Model: {model_name} -> {resolved_model} ===")

        print(format_running_model_report(resolved_model))
        run_model_check(model_name=resolved_model, prompt=PROMPT, num_ctx=args.ctx)


# ----------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    main()
