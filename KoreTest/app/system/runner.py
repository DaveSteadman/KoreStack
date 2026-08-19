# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Test runner for KoreAgent.
#
# Invoked as a subprocess by the /systemtest slash command.
# Not intended for interactive use.
#
# Data flow:
#   1. load_prompts_file()   -- reads a JSON array of plain prompts or multi-turn exchanges
#   2. invoke_exchange()     -- submits normal session prompts to the live KoreAgent API
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
#       all_contains|a||b     -- output must contain all listed fragments (case-insensitive)
#       none_contains|a||b    -- output must contain none of the listed fragments (case-insensitive)
#       regex|<pattern>       -- output must match regex pattern
#       not_regex|<pattern>   -- output must not match regex pattern
#       not_empty             -- output must be non-empty
#       exit_code|<n>         -- subprocess exit code must equal n
# MARK: FUNCTIONS
# Function inventory:
# - load_prompts_file: Loads prompts file for this module.
# - invoke_framework: Implements the invoke framework operation for this module.
# - invoke_exchange: Implements the invoke exchange operation for this module.
# - _agent_base_url: Implements the  agent base url operation for this module.
# - _agent_request: Implements the  agent request operation for this module.
# - _invoke_agent_turn: Implements the  invoke agent turn operation for this module.
# - extract_log_file: Extracts log file for this module.
# - _parse_turn_outputs: Implements the  parse turn outputs operation for this module.
# - _parse_turn_metrics: Implements the  parse turn metrics operation for this module.
# - extract_final_output: Extracts final output for this module.
# - _log_indicates_validation_failure: Implements the  log indicates validation failure operation for this module.
# - _output_indicates_no_results: Implements the  output indicates no results operation for this module.
# - _normalize_assert_text: Implements the  normalize assert text operation for this module.
# - _has_explicit_asserts: Implements the  has explicit asserts operation for this module.
# - _should_tolerate_validation_failure: Implements the  should tolerate validation failure operation for this module.
# - _single_item_pass_status: Implements the  single item pass status operation for this module.
# - _exchange_pass_status: Implements the  exchange pass status operation for this module.
# - _evaluate_assert: Implements the  evaluate assert operation for this module.
# - initialize_csv: Implements the initialize csv operation for this module.
# - append_csv_row: Appends csv row for this module.
# - _base_row: Implements the  base row operation for this module.
# - _fmt_duration: Implements the  fmt duration operation for this module.
# - _write_summary_md: Implements the  write summary md operation for this module.
# - run_tests: Runs tests for this module.
# - _run_single_item: Implements the  run single item operation for this module.
# - _run_exchange_item: Implements the  run exchange item operation for this module.
# - parse_args: Parses args for this module.
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
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

REPO_ROOT     = Path(__file__).resolve().parents[3]


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
# Maximum time in seconds to wait for a single framework invocation before aborting.
SUBPROCESS_TIMEOUT_SECONDS = 300

CSV_FIELDS = [
    "timestamp", "source_file", "prompt", "exchange_name", "turn_index",
    "final_output", "assert_result", "passed", "failure_reason",
    "duration_seconds", "prompt_tokens", "exit_code", "log_file", "stderr",
]


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
    """Run prompts against the live KoreAgent API and collect its ordinary SSE events.

    KoreTest owns the test protocol.  KoreAgent is treated strictly as the
    system under test: it receives normal session prompts and has no test mode.
    """
    start_time = time.monotonic()
    session_id = f"koretest_{uuid.uuid4().hex}"
    output: list[str] = []
    errors: list[str] = []
    exit_code = 0
    try:
        for turn_index, prompt in enumerate(turn_prompts, start=1):
            output.append(f"[TURN {turn_index}] User: {prompt}")
            result = _invoke_agent_turn(session_id, prompt)
            output.append(f"Log file: {result['log_file']}") if result["log_file"] else None
            output.append(f"[TURN {turn_index}] Agent: {result['response']}")
            output.append(f"[TURN {turn_index}] tokens={result['tokens']} tps={result['tps']}")
            if result["error"]:
                errors.append(result["error"])
                exit_code = 1
                break
    except (OSError, urllib.error.URLError, ValueError) as exc:
        errors.append(str(exc))
        exit_code = 1
    finally:
        try:
            _agent_request("DELETE", f"/api/sessions/{urllib.parse.quote(session_id, safe='')}")
        except (OSError, urllib.error.URLError, ValueError):
            pass

    duration = time.monotonic() - start_time
    return duration, exit_code, "\n".join(output), "\n".join(errors)


def _agent_base_url() -> str:
    config = json.loads((REPO_ROOT / "config" / "korestack_config.json").read_text(encoding="utf-8"))
    host   = str(config.get("network", {}).get("host", "127.0.0.1"))
    port   = int(config["services"]["koreagent"]["port"])
    return f"http://{host}:{port}"


def _agent_request(method: str, path: str, payload: dict | None = None):
    body    = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        _agent_base_url() + path,
        data    = body,
        method  = method,
        headers = {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=SUBPROCESS_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _invoke_agent_turn(session_id: str, prompt: str) -> dict:
    submitted = _agent_request("POST", f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/prompt", {"prompt": prompt})
    run_id    = str(submitted["run_id"])
    request   = urllib.request.Request(_agent_base_url() + f"/api/runs/{urllib.parse.quote(run_id, safe='')}/stream")
    result    = {"response": "", "tokens": 0, "tps": "0", "log_file": "", "error": ""}
    progress_lines: list[str] = []
    with urllib.request.urlopen(request, timeout=SUBPROCESS_TIMEOUT_SECONDS) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            event = json.loads(line.removeprefix("data:").strip())
            event_type = event.get("type")
            if event_type == "response":
                result["response"] = str(event.get("response") or "")
                result["tokens"]   = int(event.get("tokens") or 0)
                result["tps"]      = str(event.get("tps") or "0")
            elif event_type == "log_file":
                result["log_file"] = str(event.get("path") or "")
            elif event_type == "error":
                result["error"] = str(event.get("message") or "Agent request failed")
            elif event_type == "progress":
                text = str(event.get("text") or "").strip()
                if text:
                    progress_lines.append(text)
            elif event_type == "done":
                break
    if not result["response"] and progress_lines:
        result["response"] = "\n".join(progress_lines)
    return result


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


def _normalize_assert_text(text: str) -> str:
    normalized = str(text or "").replace("\u202f", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    return normalized.replace(",", "")


def _has_explicit_asserts(assert_results: list[str]) -> bool:
    return any(str(item or "").strip() and str(item).upper() != "SKIP" for item in assert_results)


def _should_tolerate_validation_failure(
    *,
    turn_outputs: dict[int, str],
    assert_results: list[str],
) -> bool:
    if not turn_outputs or not _has_explicit_asserts(assert_results):
        return False
    if any(not str(output or "").strip() for output in turn_outputs.values()):
        return False
    return all(str(result or "").upper() != "FAIL" for result in assert_results if str(result or "").strip())


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
def _exchange_pass_status(
    exit_code: int,
    turn_outputs: dict[int, str],
    any_assert_fail: bool,
    log_file: str,
    allow_no_results: bool = False,
    assert_results: list[str] | None = None,
) -> tuple[bool, str]:
    """Return (passed, failure_reason) for a multi-turn exchange run."""
    if exit_code != 0:
        return False, f"Exit code {exit_code}"
    if any_assert_fail:
        return False, "Assert failed"
    if any(not str(output).strip() for output in turn_outputs.values()):
        return False, "One or more turns produced empty output"
    if (not allow_no_results) and any(_output_indicates_no_results(str(output)) for output in turn_outputs.values()):
        return False, "Search returned no results"
    if _log_indicates_validation_failure(log_file):
        if _should_tolerate_validation_failure(
            turn_outputs  = turn_outputs,
            assert_results = list(assert_results or []),
        ):
            return True, ""
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
        return "PASS" if _normalize_assert_text(value) in _normalize_assert_text(final_output) else "FAIL"
    if op == "not_contains":
        return "PASS" if _normalize_assert_text(value) not in _normalize_assert_text(final_output) else "FAIL"
    if op == "all_contains":
        parts = [p.strip() for p in value.split("||") if p.strip()]
        if not parts:
            return "SKIP"
        normalized_output = _normalize_assert_text(final_output)
        return "PASS" if all(_normalize_assert_text(part) in normalized_output for part in parts) else "FAIL"
    if op == "none_contains":
        parts = [p.strip() for p in value.split("||") if p.strip()]
        if not parts:
            return "SKIP"
        normalized_output = _normalize_assert_text(final_output)
        return "PASS" if all(_normalize_assert_text(part) not in normalized_output for part in parts) else "FAIL"
    if op == "regex":
        try:
            return "PASS" if re.search(value, final_output, flags=re.IGNORECASE) else "FAIL"
        except re.error:
            return "SKIP"
    if op == "not_regex":
        try:
            return "PASS" if not re.search(value, final_output, flags=re.IGNORECASE) else "FAIL"
        except re.error:
            return "SKIP"
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
        "passed":           "",
        "failure_reason":   "",
        "duration_seconds": "0.000",
        "prompt_tokens":    "0",
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
                    "prompt_tokens": sum(tokens for tokens, _tps in turn_metrics.values()),
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

    _passed, _failure_reason = _single_item_pass_status(
        exit_code=int(row["exit_code"]),
        final_output=row["final_output"],
        log_file=str(row["log_file"]),
    )
    row["passed"] = "PASS" if _passed else "FAIL"
    row["failure_reason"] = _failure_reason
    append_csv_row(output_path=output_path, row=row)
    for turn_idx, (prompt_tokens, tps_str) in sorted(turn_metrics.items()):
        print(f"[TURN {turn_idx}] tokens={prompt_tokens} tps={tps_str}")

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
    assert_results: list[str] = []
    allow_no_results = any(bool(turn.get("allow_no_results")) for turn in turns)

    pending_rows: list[dict] = []
    pending_prints: list[tuple[int, str, str, str]] = []
    for turn_idx, turn in enumerate(turns, start=1):
        user_prompt  = turn["user"]
        assert_expr  = turn.get("assert", "")
        final_output = turn_outputs.get(turn_idx, "")
        assert_result = _evaluate_assert(assert_expr, final_output, exit_code)
        prompt_tokens, tps_str = turn_metrics.get(turn_idx, (0, "0"))
        assert_results.append(assert_result)
        if assert_result == "FAIL":
            any_assert_fail = True

        row = _base_row(run_timestamp, source_file, user_prompt, exchange_name=name, turn_index=turn_idx)
        row.update({
            "final_output":     final_output,
            "assert_result":    assert_result,
            "duration_seconds": f"{per_turn_dur:.3f}",
            "prompt_tokens":    prompt_tokens,
            "exit_code":        exit_code,
            "log_file":         log_file,
            "stderr":           stderr.strip(),
        })
        pending_rows.append(row)

        status_label = "OK" if exit_code == 0 else "FAIL"
        assert_label = f"  assert={assert_result}" if assert_expr else ""
        pending_prints.append((turn_idx, str(prompt_tokens), tps_str, f"  [Turn {turn_idx}/{n}] [{status_label}]{assert_label}: {user_prompt!r}"))

    _passed, _reason = _exchange_pass_status(
        exit_code=exit_code,
        turn_outputs=turn_outputs,
        any_assert_fail=any_assert_fail,
        log_file=log_file,
        allow_no_results=allow_no_results,
        assert_results=assert_results,
    )
    for row in pending_rows:
        row["passed"] = "PASS" if _passed else "FAIL"
        row["failure_reason"] = _reason
        append_csv_row(output_path=output_path, row=row)

    for turn_idx, prompt_tokens, tps_str, status_line in pending_prints:
        print(f"[TURN {turn_idx}] tokens={prompt_tokens} tps={tps_str}")
        print(status_line)

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
        description="System-test runner for KoreAgent - invoked by /systemtest."
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
