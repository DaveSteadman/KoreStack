# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Test runner for MiniAgentFramework.
#
# Invoked as a subprocess by the /test slash command in slash_commands.py.
# Not intended for interactive use.
#
# Data flow:
#   1. load_prompts_file()   -- reads a JSON array of plain prompts or multi-turn exchanges
#   2. invoke_exchange()     -- spawns main.py via CHAT_SEQUENCE_FILE env var and captures stdout
#   3. Output parsers        -- extract turn responses, token metrics, log file path, assert results
#   4. CSV writers           -- append one row per turn to the shared results CSV
#   5. run_tests()           -- dispatches each item to _run_single_item or _run_exchange_item
#
# Prompt file format (JSON array):
#   Plain string  -- single standalone prompt
#   Exchange dict -- multi-turn sequence with optional per-turn assertions:
#       {
#           "exchange": "label",
#           "turns": [
#               { "user": "first prompt" },
#               { "user": "follow-up", "assert": "contains|expected text" }
#           ]
#       }
#   Assert expressions:
#       contains|<text>       -- output must contain text (case-insensitive)
#       not_contains|<text>   -- output must NOT contain text (case-insensitive)
#       not_empty             -- output must be non-empty
#       exit_code|<n>         -- subprocess exit code must equal n
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import argparse
import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

# sys.path must include the code/ directory before project modules can be imported.
_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "code"))

from KoreAgent.utils.workspace_utils import get_test_results_dir


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
REPO_ROOT = _REPO_ROOT
MAIN_SCRIPT = REPO_ROOT / "code" / "main.py"

# Maximum time in seconds to wait for a single framework invocation before aborting.
SUBPROCESS_TIMEOUT_SECONDS = 300

CSV_FIELDS = ["timestamp", "source_file", "prompt", "exchange_name", "turn_index",
              "final_output", "assert_result", "duration_seconds", "exit_code", "log_file", "stderr"]


# ====================================================================================================
# MARK: PROMPTS LOADING
# ====================================================================================================
def load_prompts_file(path: Path) -> list:
    """Load a JSON array of prompt strings or exchange objects from a file.

    Returns a mixed list: each element is either a plain str (existing format)
    or an exchange dict with keys 'exchange' and 'turns'.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Prompts file must contain a JSON array: {path}")
    result = []
    for item in data:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict) and "exchange" in item and "turns" in item:
            result.append(item)
        else:
            result.append(str(item))   # best-effort coerce unknown entries
    return result


# ====================================================================================================
# MARK: FRAMEWORK INVOCATION
# ====================================================================================================
def invoke_framework(
    prompt: str,
    model: str | None = None,
    llmhost: str | None = None,
) -> tuple[float, int, str, str]:
    # Single-prompt convenience wrapper - routes through invoke_exchange so output
    # is always in [TURN N] format, consistent with multi-turn exchanges.
    return invoke_exchange(
        [prompt],
        model=model,
        llmhost=llmhost,
    )


# ----------------------------------------------------------------------------------------------------
def invoke_exchange(
    turn_prompts: list[str],
    model: str | None = None,
    llmhost: str | None = None,
) -> tuple[float, int, str, str]:
    # Writes prompts to a temp JSON file, passes the path to main.py via the
    # CHAT_SEQUENCE_FILE environment variable, and returns (duration_secs, exit_code, stdout, stderr).
    start_time = time.monotonic()

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(turn_prompts, tmp, ensure_ascii=False)
        tmp_path = tmp.name

    try:
        cmd = [sys.executable, str(MAIN_SCRIPT)]
        if model:
            cmd += ["--model", model]
        if llmhost:
            cmd += ["--llmhost", llmhost]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SUBPROCESS_TIMEOUT_SECONDS * len(turn_prompts),
            env={**os.environ, "CHAT_SEQUENCE_FILE": tmp_path},
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    duration = time.monotonic() - start_time
    return duration, result.returncode, result.stdout, result.stderr


# ====================================================================================================
# MARK: OUTPUT PARSING
# Parse the structured stdout that main.py emits in chat-sequence mode.
# Each turn produces:
#   [TURN N] User: <prompt>
#   [TURN N] Agent: <response, may be multi-line>
#   [TURN N] tokens=<n> tps=<f>
# ====================================================================================================
def extract_log_file(stdout_text: str) -> str:
    # Pull the log file path from the SYSTEM STATUS header line.
    for line in stdout_text.splitlines():
        if line.strip().startswith("Log file:"):
            return line.split("Log file:", maxsplit=1)[1].strip()
    return ""


# ----------------------------------------------------------------------------------------------------
def _parse_turn_outputs(stdout_text: str) -> dict[int, str]:
    # Returns {turn_idx: agent_response_text} for every turn in the output.
    outputs: dict[int, str] = {}
    current_turn: int | None = None
    current_lines: list[str] = []

    for line in stdout_text.splitlines():
        agent_match = line.startswith("[TURN ") and "] Agent: " in line
        if agent_match:
            # Flush any previous turn accumulation.
            if current_turn is not None:
                outputs[current_turn] = "\n".join(current_lines).strip()
            bracket_end = line.index("]")
            current_turn = int(line[6:bracket_end])
            current_lines = [line.split("] Agent: ", 1)[1]]
        elif current_turn is not None:
            # Check if a new TURN marker starts (tokens line or next turn).
            if line.startswith(f"[TURN {current_turn}] tokens="):
                outputs[current_turn] = "\n".join(current_lines).strip()
                current_turn = None
                current_lines = []
            elif line.startswith("[TURN "):
                outputs[current_turn] = "\n".join(current_lines).strip()
                current_turn = None
                current_lines = []
            else:
                current_lines.append(line)

    if current_turn is not None:
        outputs[current_turn] = "\n".join(current_lines).strip()

    return outputs


# ----------------------------------------------------------------------------------------------------
def _parse_turn_metrics(stdout_text: str) -> dict[int, tuple[int, str]]:
    # Returns {turn_idx: (prompt_tokens, tps_str)} for every turn.
    metrics: dict[int, tuple[int, str]] = {}
    pattern = re.compile(r"^\[TURN\s+(\d+)\]\s+tokens=(\d+)\s+tps=([0-9.]+)$")
    for line in stdout_text.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        metrics[int(match.group(1))] = (int(match.group(2)), match.group(3))
    return metrics


# ----------------------------------------------------------------------------------------------------
def extract_final_output(stdout_text: str) -> str:
    # Convenience accessor for the single-prompt case: returns turn 1 agent response.
    return _parse_turn_outputs(stdout_text).get(1, "").replace("\u202f", " ")


# ----------------------------------------------------------------------------------------------------
def _log_indicates_validation_failure(log_file: str) -> bool:
    """Return True when the run log records orchestration validation failure."""
    if not log_file:
        return False
    try:
        text = Path(log_file).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    lowered = text.lower()
    return (
        "[warn] orchestration validation failed" in lowered
        or "validation failed" in lowered
    )


# ----------------------------------------------------------------------------------------------------
def _output_indicates_no_results(final_output: str) -> bool:
    """Return True when the model output is a known no-results / search-failed sentinel."""
    text = (final_output or "").strip().lower()
    if not text:
        return False
    return (
        text.startswith("no results were found")
        or text.startswith("search failed")
        or text.startswith("duckduckgo returned no results")
    )


# ----------------------------------------------------------------------------------------------------
def _single_item_pass_status(exit_code: int, final_output: str, log_file: str) -> tuple[bool, str]:
    """Return (passed, failure_reason) for a standalone prompt run."""
    if exit_code != 0:
        return False, f"Exit code {exit_code}"
    if not final_output.strip():
        return False, "Empty final output"
    if _output_indicates_no_results(final_output):
        return False, "Search returned no results"
    if _log_indicates_validation_failure(log_file):
        return False, "Orchestration validation failed"
    return True, ""


# ----------------------------------------------------------------------------------------------------
def _exchange_pass_status(exit_code: int, turn_outputs: dict[int, str], any_assert_fail: bool, log_file: str) -> tuple[bool, str]:
    """Return (passed, failure_reason) for a multi-turn exchange run."""
    if exit_code != 0:
        return False, f"Exit code {exit_code}"
    if any_assert_fail:
        return False, "Assert failed"
    if any(not str(output).strip() for output in turn_outputs.values()):
        return False, "One or more turns produced empty output"
    if any(_output_indicates_no_results(str(output)) for output in turn_outputs.values()):
        return False, "Search returned no results"
    if _log_indicates_validation_failure(log_file):
        return False, "Orchestration validation failed"
    return True, ""


# ----------------------------------------------------------------------------------------------------
def _evaluate_assert(expression: str, final_output: str, exit_code: int) -> str:
    # Returns 'PASS', 'FAIL', or 'SKIP' (no expression).
    if not expression:
        return "SKIP"
    op, _, value = expression.partition("|")
    op = op.strip().lower()
    if op == "contains":
        return "PASS" if value.lower() in final_output.lower() else "FAIL"
    if op == "not_contains":
        return "PASS" if value.lower() not in final_output.lower() else "FAIL"
    if op == "not_empty":
        return "PASS" if final_output.strip() else "FAIL"
    if op == "exit_code":
        try:
            return "PASS" if exit_code == int(value) else "FAIL"
        except ValueError:
            return "SKIP"
    return "SKIP"


# ====================================================================================================
# MARK: CSV OUTPUT
# ====================================================================================================
def initialize_csv(output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Only write the header row when the file is new or empty so that
    # multiple test files can be appended to one shared results file.
    is_new = not output_path.exists() or output_path.stat().st_size == 0
    with output_path.open("a", newline="", encoding="utf-8") as csv_file:
        if is_new:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=CSV_FIELDS,
                quoting=csv.QUOTE_ALL,
            )
            writer.writeheader()


# ----------------------------------------------------------------------------------------------------
def append_csv_row(output_path: Path, row: dict) -> None:
    sanitized_row = {}
    for key, value in row.items():
        if isinstance(value, str):
            sanitized_row[key] = value.replace("\r", " ")
        else:
            sanitized_row[key] = value

    with output_path.open("a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=CSV_FIELDS,
            quoting=csv.QUOTE_ALL,
        )
        writer.writerow(sanitized_row)
        csv_file.flush()
        os.fsync(csv_file.fileno())


# ----------------------------------------------------------------------------------------------------
def _base_row(run_timestamp: str, source_file: str, prompt: str, exchange_name: str = "", turn_index: int = 0) -> dict:
    # Pre-populated CSV row dict with all fields at safe defaults.
    return {
        "timestamp":        run_timestamp,
        "source_file":      source_file,
        "prompt":           prompt,
        "exchange_name":    exchange_name,
        "turn_index":       turn_index,
        "final_output":     "",
        "assert_result":    "",
        "duration_seconds": "0.000",
        "exit_code":        -1,
        "log_file":         "",
        "stderr":           "",
    }


# ====================================================================================================
# MARK: SUMMARY REPORT
# ====================================================================================================
def _fmt_duration(seconds: float) -> str:
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m}m {s:.0f}s" if m else f"{s:.0f}s"


# ----------------------------------------------------------------------------------------------------
def _write_summary_md(csv_path: Path, records: list[dict], wall_clock: float) -> Path:
    # Write a Markdown summary alongside the CSV results file.
    # Groups results by suite, lists the 5 slowest items, and catalogues failures by reason.
    # Returns the path of the written file.
    md_path = csv_path.with_name(csv_path.stem.replace("test_results", "summary") + ".md")

    total  = len(records)
    passed = sum(1 for r in records if r["passed"])
    failed = total - passed
    now    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    suites: dict[str, dict] = {}
    for r in records:
        sf = r["source_file"] or "unknown"
        if sf not in suites:
            suites[sf] = {"pass": 0, "fail": 0}
        if r["passed"]:
            suites[sf]["pass"] += 1
        else:
            suites[sf]["fail"] += 1

    lines: list[str] = [
        "# Test Run Summary",
        "",
        f"Run: {now}  |  Passed: **{passed}/{total}**  |  Wall-clock: {_fmt_duration(wall_clock)}",
        "",
        "## Results by Suite",
        "",
        "| Suite | Pass | Fail | Total |",
        "| ----- | ---: | ---: | ----: |",
    ]
    for sf, counts in suites.items():
        t = counts["pass"] + counts["fail"]
        lines.append(f"| {sf} | {counts['pass']} | {counts['fail']} | {t} |")
    lines.append("")

    sorted_by_dur = sorted(records, key=lambda r: r["duration"], reverse=True)
    lines += [
        "## 5 Slowest Items",
        "",
        "| Duration | Label |",
        "| -------: | ----- |",
    ]
    for r in sorted_by_dur[:5]:
        lines.append(f"| {r['duration']:.1f}s | {r['label']} |")
    lines.append("")

    failures = [r for r in records if not r["passed"]]
    if failures:
        lines += [
            f"## Failures ({failed})",
            "",
            "| Label | Reason |",
            "| ----- | ------ |",
        ]
        for r in failures:
            lines.append(f"| {r['label']} | {r['failure_reason']} |")
    else:
        lines += [
            "## Failures",
            "",
            "None - all tests passed.",
        ]
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


# ====================================================================================================
# MARK: TEST RUNNER
# ====================================================================================================
def run_tests(
    prompts: list,
    output_path: Path,
    model: str | None = None,
    llmhost: str | None = None,
    source_file: str = "",
) -> Path:
    initialize_csv(output_path)
    model_label = f" (model: {model})" if model else ""
    host_label  = f" (host: {llmhost})" if llmhost else ""
    print(f"Results file initialized: {output_path}{model_label}{host_label}")

    total_items  = len(prompts)
    tests_run    = 0
    tests_passed = 0
    _wall_start  = time.monotonic()
    _records:    list[dict] = []

    for index, item in enumerate(prompts, start=1):
        tests_run += 1
        if isinstance(item, dict):   # exchange
            passed, record = _run_exchange_item(
                item, index, total_items, output_path,
                model=model, llmhost=llmhost, source_file=source_file,
            )
            if passed:
                tests_passed += 1
            _records.append(record)
        else:                        # plain string
            interrupted, passed, record = _run_single_item(
                str(item), index, total_items, output_path,
                model=model, llmhost=llmhost, source_file=source_file,
            )
            if passed:
                tests_passed += 1
            _records.append(record)
            if interrupted:
                break

    wall_clock   = time.monotonic() - _wall_start
    summary_path = _write_summary_md(output_path, _records, wall_clock)
    print(f"\nResults written to:  {output_path}")
    print(f"Summary written to:  {summary_path}")
    print(f"[TEST_SUMMARY] passed={tests_passed} total={tests_run}")
    return output_path


# ----------------------------------------------------------------------------------------------------
def _run_single_item(
    prompt: str,
    index: int,
    total_items: int,
    output_path: Path,
    model, llmhost,
    source_file: str = "",
) -> tuple[bool, bool, dict]:
    """Run a single standalone prompt.  Returns True if the run was interrupted."""
    run_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{run_timestamp}] Running prompt {index}/{total_items}: {prompt!r}")

    row = _base_row(run_timestamp, source_file, prompt)
    try:
        duration, exit_code, stdout, stderr = invoke_framework(
            prompt, model=model, llmhost=llmhost,
        )
        log_file     = extract_log_file(stdout_text=stdout)
        final_output = extract_final_output(stdout_text=stdout)
        turn_metrics = _parse_turn_metrics(stdout)
        row.update({"final_output": final_output, "duration_seconds": f"{duration:.3f}",
                    "exit_code": exit_code, "log_file": log_file, "stderr": stderr.strip()})
    except subprocess.TimeoutExpired as e:
        row.update({"duration_seconds": f"{SUBPROCESS_TIMEOUT_SECONDS}.000",
                    "exit_code": 124, "stderr": f"Timeout: {e}"})
        turn_metrics = {}
    except KeyboardInterrupt:
        row.update({"exit_code": 130, "stderr": "Interrupted by user."})
        append_csv_row(output_path=output_path, row=row)
        status_label = "FAIL"
        print(f"  [{status_label}] duration={row['duration_seconds']}s  exit_code={row['exit_code']}")
        print("Interrupted by user, ending test run.")
        return True, False, {"label": prompt[:80], "source_file": source_file, "duration": 0.0, "passed": False, "failure_reason": "Interrupted"}
    except Exception as e:
        row.update({"exit_code": 125, "stderr": f"Wrapper error: {e}"})
        turn_metrics = {}

    append_csv_row(output_path=output_path, row=row)
    for turn_idx, (prompt_tokens, tps_str) in sorted(turn_metrics.items()):
        print(f"[TURN {turn_idx}] tokens={prompt_tokens} tps={tps_str}")

    _passed, _failure_reason = _single_item_pass_status(
        exit_code=int(row["exit_code"]),
        final_output=row["final_output"],
        log_file=str(row["log_file"]),
    )
    status_label = "OK" if _passed else "FAIL"
    print(f"  [{status_label}] duration={row['duration_seconds']}s  exit_code={row['exit_code']}")
    _duration = float(row["duration_seconds"])
    _record   = {
        "label":          prompt[:80],
        "source_file":    source_file,
        "duration":       _duration,
        "passed":         _passed,
        "failure_reason": _failure_reason,
    }
    return False, _passed, _record


# ----------------------------------------------------------------------------------------------------
def _run_exchange_item(
    exchange: dict,
    index: int,
    total_items: int,
    output_path: Path,
    model, llmhost,
    source_file: str = "",
) -> tuple[bool, dict]:
    """Run a multi-turn exchange.  Writes one CSV row per turn."""
    name   = exchange.get("exchange", f"exchange_{index}")
    turns  = exchange.get("turns", [])
    n      = len(turns)

    run_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{run_timestamp}] Running exchange {index}/{total_items}: {name!r} ({n} turn(s))")

    turn_prompts = [t["user"] for t in turns]

    try:
        duration, exit_code, stdout, stderr = invoke_exchange(
            turn_prompts, model=model, llmhost=llmhost,
        )
    except subprocess.TimeoutExpired as e:
        duration, exit_code = float(SUBPROCESS_TIMEOUT_SECONDS * n), 124
        stdout, stderr = "", f"Timeout: {e}"
    except Exception as e:
        duration, exit_code = 0.0, 125
        stdout, stderr = "", f"Wrapper error: {e}"

    log_file      = extract_log_file(stdout_text=stdout)
    turn_outputs  = _parse_turn_outputs(stdout)
    turn_metrics  = _parse_turn_metrics(stdout)
    per_turn_dur  = duration / n if n else duration
    any_assert_fail = False

    for turn_idx, turn in enumerate(turns, start=1):
        user_prompt  = turn["user"]
        assert_expr  = turn.get("assert", "")
        final_output = turn_outputs.get(turn_idx, "")
        assert_result = _evaluate_assert(assert_expr, final_output, exit_code)
        if assert_result == "FAIL":
            any_assert_fail = True

        row = _base_row(run_timestamp, source_file, user_prompt, exchange_name=name, turn_index=turn_idx)
        row.update({
            "final_output":     final_output,
            "assert_result":    assert_result,
            "duration_seconds": f"{per_turn_dur:.3f}",
            "exit_code":        exit_code,
            "log_file":         log_file,
            "stderr":           stderr.strip(),
        })
        append_csv_row(output_path=output_path, row=row)

        prompt_tokens, tps_str = turn_metrics.get(turn_idx, (0, "0"))
        print(f"[TURN {turn_idx}] tokens={prompt_tokens} tps={tps_str}")

        status_label = "OK" if exit_code == 0 else "FAIL"
        assert_label = f"  assert={assert_result}" if assert_expr else ""
        print(f"  [Turn {turn_idx}/{n}] [{status_label}]{assert_label}: {user_prompt!r}")

    _passed, _reason = _exchange_pass_status(
        exit_code=exit_code,
        turn_outputs=turn_outputs,
        any_assert_fail=any_assert_fail,
        log_file=log_file,
    )
    _record = {
        "label":          name,
        "source_file":    source_file,
        "duration":       duration,
        "passed":         _passed,
        "failure_reason": _reason,
    }
    return _passed, _record


# ====================================================================================================
# MARK: ENTRYPOINT
# ====================================================================================================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test runner for MiniAgentFramework - invoked by /test slash command."
    )
    parser.add_argument(
        "--prompts-file",
        type=Path,
        required=True,
        help="Path to a JSON file containing an array of prompt strings or exchange objects.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Ollama model alias to pass to main.py (overrides its default).",
    )
    parser.add_argument(
        "--llmhost",
        type=str,
        default=None,
        help="LLM server host URL to pass to main.py (e.g. http://MONTBLANC:11434 or http://MONTBLANC:1234).",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        default=None,
        help="Exact output CSV path. Appends to the file if it already exists (header skipped).",
    )
    parser.add_argument(
        "--source-file",
        type=str,
        default="",
        help="Label written to the source_file column in the CSV (typically the prompts filename).",
    )
    return parser.parse_args()


# ====================================================================================================
if __name__ == "__main__":
    args = parse_args()
    if args.output_file is None:
        _now = datetime.now()
        _out_dir = get_test_results_dir() / _now.strftime("%Y-%m-%d")
        args.output_file = _out_dir / f"test_results_{_now.strftime('%Y%m%d_%H%M%S')}.csv"
    run_tests(
        prompts=load_prompts_file(args.prompts_file),
        output_path=args.output_file,
        model=args.model,
        llmhost=args.llmhost,
        source_file=args.source_file or args.prompts_file.name,
    )
