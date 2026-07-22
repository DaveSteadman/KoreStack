# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Slash command handlers for test suite execution and result reporting.
#
# Commands handled:
#   /test              -- list available test prompt files
#   /test <file>       -- run a test prompt file and report pass/fail counts
#   /test results      -- show the most recent test result summary
#
# Delegates test execution to run_helpers.run_prompt_batch().  Reads raw CSV output and
# emits [TEST COMPLETE] / [ALL TESTS COMPLETE] lines that routes_sessions.py captures
# for KoreChat summary writes.
#
# Related modules:
#   - input_layer/slash_commands.py    -- registers all handlers
#   - run_helpers.py                   -- run_prompt_batch (test execution)
#   - testing/test_wrapper.py          -- the underlying test runner invoked per file
#   - utils/workspace_utils.py         -- get_test_prompts_dir, get_test_results_dir
# ====================================================================================================
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from llm_client import get_active_host
from input_layer.slash_command_context import SlashCommandContext
from utils.workspace_utils import get_test_prompts_dir
from utils.workspace_utils import get_test_results_dir


_POST_TEST_SMOKE_TARGETS: tuple[str, ...] = (
    "testing.test_guardrail_smoke.GuardrailSmokeTests.test_test_wrapper_extracts_delegate2_log_evidence",
    "testing.test_guardrail_smoke.GuardrailSmokeTests.test_test_wrapper_fails_single_prompt_on_no_results_output",
    "testing.test_guardrail_smoke.GuardrailSmokeTests.test_test_wrapper_fails_exchange_on_search_failure_output",
    "testing.test_guardrail_smoke.GuardrailSmokeTests.test_queue_timeout_for_prompt_disables_scheduler_timeout_only_for_test",
    "testing.test_guardrail_smoke.GuardrailSmokeTests.test_slash_command_outputs_use_ascii_arrows",
    "testing.test_guardrail_data.GuardrailDataTests.test_koreconv_prompt_renders_datasets_separately",
    "testing.test_guardrail_data.GuardrailDataTests.test_koreconv_event_restores_datasets_before_orchestration",
)


def _count_test_items(candidate: Path) -> int:
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(data) if isinstance(data, list) else 0


def _write_all_run_summary(
    csv_path: Path,
    suite_results: list[dict],
    elapsed_seconds: float,
    completed_suites: int,
    planned_suites: int,
    planned_tests: int,
) -> Path:
    summary_path = csv_path.with_name(csv_path.stem.replace("test_results", "summary", 1) + ".md")
    total_tests   = sum(int(item["total"])  for item in suite_results)
    total_passed  = sum(int(item["passed"]) for item in suite_results)
    total_failed  = max(total_tests - total_passed, 0)
    mins, seconds = divmod(int(elapsed_seconds), 60)
    wall_clock    = f"{mins}m {seconds}s" if mins else f"{seconds}s"
    run_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# Test Run Summary",
        "",
        f"Run: {run_timestamp}  |  Passed: **{total_passed}/{total_tests}**  |  Wall-clock: {wall_clock}",
        f"Suites completed: **{completed_suites}/{planned_suites}**  |  Planned tests: **{planned_tests}**",
        "",
        "## Results by Suite",
        "",
        "| Suite | Pass | Fail | Total |",
        "| ----- | ---: | ---: | ----: |",
    ]
    for item in suite_results:
        failed = max(int(item["total"]) - int(item["passed"]), 0)
        lines.append(f"| {item['name']} | {item['passed']} | {failed} | {item['total']} |")
    lines.append("")

    if total_failed:
        lines += [
            f"## Failures ({total_failed})",
            "",
            "| Suite | Pass | Fail | Total |",
            "| ----- | ---: | ---: | ----: |",
        ]
        for item in suite_results:
            failed = max(int(item["total"]) - int(item["passed"]), 0)
            if failed > 0:
                lines.append(f"| {item['name']} | {item['passed']} | {failed} | {item['total']} |")
    else:
        lines += [
            "## Failures",
            "",
            "None - all tests passed.",
        ]
    lines.append("")

    summary_path.write_text("\n".join(lines), encoding="utf-8")
    return summary_path


def _row_outcome(row: dict) -> str:
    passed_value = row.get("passed", "").strip().upper()
    if passed_value in {"PASS", "TRUE", "1", "YES", "OK"}:
        return "PASS"
    if passed_value in {"FAIL", "FALSE", "0", "NO"}:
        return "FAIL"

    failure_reason = row.get("failure_reason", "").strip()
    if failure_reason:
        return "FAIL"

    assert_result = row.get("assert_result", "").strip().upper()
    if assert_result == "FAIL":
        return "FAIL"
    if assert_result == "PASS":
        return "PASS"
    try:
        code = int(row.get("exit_code", "0"))
    except (ValueError, TypeError):
        code = -1
    if code != 0 or not row.get("final_output", "").strip():
        return "FAIL"
    return "PASS"


def _summary_counts_from_markdown(raw_csv_path: Path) -> tuple[int, int] | None:
    summary_name = raw_csv_path.stem.replace("test_results", "summary", 1) + ".md"
    summary_path = raw_csv_path.with_name(summary_name)
    if not summary_path.exists():
        return None
    try:
        text = summary_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"Passed:\s+\*\*(\d+)/(\d+)\*\*", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _result_counts(raw_rows: list[dict], raw_csv_path: Path) -> tuple[int, int, int, int]:
    has_persisted_outcome = any(
        row.get("passed", "").strip() or row.get("failure_reason", "").strip()
        for row in raw_rows
    )
    if not has_persisted_outcome:
        summary_counts = _summary_counts_from_markdown(raw_csv_path)
        if summary_counts is not None:
            passes, total = summary_counts
            fails = max(total - passes, 0)
            return total, passes, fails, 0

    outcomes = [_row_outcome(row) for row in raw_rows]
    total = len(outcomes)
    passes = outcomes.count("PASS")
    fails = outcomes.count("FAIL")
    gaps = outcomes.count("GAP")
    return total, passes, fails, gaps


def _run_one_test_file(candidate, ctx, wrapper, model: str, active_host: str, re_mod, subprocess_mod, sys_mod, output_file=None) -> dict:
    cmd = [sys_mod.executable, str(wrapper), "--prompts-file", str(candidate), "--model", model]
    if "localhost" not in active_host and "127.0.0.1" not in active_host:
        cmd += ["--llmhost", active_host]
    if output_file is not None:
        cmd += ["--output-file", str(output_file)]
    cmd += ["--source-file", candidate.name]

    summary_re = re_mod.compile(r"^\[TEST_SUMMARY\] passed=(\d+) total=(\d+)$")
    metrics_re = re_mod.compile(r"^\[TURN\s+(\d+)\]\s+tokens=(\d+)\s+tps=([0-9.]+)$")
    test_passed = test_total = None
    prompt_tokens_total = 0
    tps_sum = 0.0
    tps_samples = 0
    try:
        proc = subprocess_mod.Popen(
            cmd,
            stdout=subprocess_mod.PIPE,
            stderr=subprocess_mod.STDOUT,
            text=True,
            encoding="utf-8",
        )
        import threading as _threading
        import time as _time

        stopped_by_user = [False]
        stop_reason     = [""]
        watcher_done = [False]

        def _watch(_proc=proc) -> None:
            from agent.orchestration.engine import get_stop_reason
            from agent.orchestration.engine import is_stop_requested

            while not watcher_done[0]:
                if is_stop_requested():
                    stopped_by_user[0] = True
                    stop_reason[0] = get_stop_reason()
                    try:
                        _proc.terminate()
                    except Exception:
                        pass
                    return
                _time.sleep(0.2)

        watcher = _threading.Thread(target=_watch, daemon=True)
        watcher.start()
        try:
            for line in proc.stdout:
                stripped = line.rstrip()
                match = summary_re.match(stripped)
                if match:
                    test_passed = int(match.group(1))
                    test_total = int(match.group(2))
                    continue
                metrics_match = metrics_re.match(stripped)
                if metrics_match:
                    prompt_tokens_total += int(metrics_match.group(2))
                    turn_tps = float(metrics_match.group(3))
                    if turn_tps > 0:
                        tps_sum += turn_tps
                        tps_samples += 1
                    continue
                if stripped:
                    ctx.output(stripped, "dim")
            proc.wait()
        finally:
            watcher_done[0] = True
            watcher.join(timeout=1.0)

        if stopped_by_user[0]:
            reason = str(stop_reason[0] or "").strip().lower()
            if reason == "stoprun":
                ctx.output("[Test stopped by /stoprun]", "error")
            elif reason == "timeout":
                ctx.output("[Test stopped by scheduler timeout]", "error")
            elif reason:
                ctx.output(f"[Test stopped by cancellation request: {reason}]", "error")
            else:
                ctx.output("[Test stopped by external cancellation request]", "error")
            return {
                "passed": 0,
                "total": 0,
                "prompt_tokens": prompt_tokens_total,
                "stopped": True,
                "tps_sum": tps_sum,
                "tps_samples": tps_samples,
            }
    except Exception as exc:
        ctx.output(f"Error running {candidate.name}: {exc}", "error")
        return {"passed": 0, "total": 0, "prompt_tokens": 0, "stopped": False, "tps_sum": 0.0, "tps_samples": 0}

    if test_passed is not None:
        suspicious_metrics = test_total > 0 and prompt_tokens_total == 0 and tps_samples == 0
        level = "success" if test_passed == test_total and not suspicious_metrics else "error"
        pass_rate = (100.0 * test_passed / test_total) if test_total else 0.0
        avg_tps = (tps_sum / tps_samples) if tps_samples else 0.0
        ctx.output(f"[Test: {candidate.name}  Passed {test_passed}/{test_total}]", level)
        ctx.output(
            f"[TEST COMPLETE] {candidate.name} | pass rate={pass_rate:.0f}% ({test_passed}/{test_total})"
            f" | prompt tokens={prompt_tokens_total:,} | avg tok/s={avg_tps:.1f}",
            level,
        )
        if suspicious_metrics:
            ctx.output(
                "[TEST WARNING] Suite reported no prompt-token or tok/s metrics; treat this run as suspicious and inspect the CSV/logs.",
                "error",
            )
        return {
            "passed": test_passed,
            "total": test_total,
            "prompt_tokens": prompt_tokens_total,
            "stopped": False,
            "tps_sum": tps_sum,
            "tps_samples": tps_samples,
        }
    if proc.returncode == 0:
        ctx.output(f"[Test: {candidate.name}  completed (no summary)]", "dim")
    else:
        ctx.output(f"[Test: {candidate.name}  exited with code {proc.returncode}]", "error")
    return {"passed": 0, "total": 0, "prompt_tokens": prompt_tokens_total, "stopped": False, "tps_sum": tps_sum, "tps_samples": tps_samples}


def _run_post_test_checks(ctx, csv_path, testcode_dir, subprocess_mod, sys_mod) -> None:
    ctx.output("--- Post-test checks ---", "dim")
    ctx.output("  guardrail smoke checks ...", "dim")
    try:
        proc = subprocess_mod.run(
            [sys_mod.executable, "-m", "unittest", *_POST_TEST_SMOKE_TARGETS],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(testcode_dir.parent),
        )
        combined = (proc.stdout + proc.stderr).strip()
        for line in combined.splitlines():
            ctx.output(f"    {line}", "dim" if proc.returncode == 0 else "error")
        ctx.output(
            f"  [guardrail_smoke: {'OK' if proc.returncode == 0 else 'FAILED'}]",
            "success" if proc.returncode == 0 else "error",
        )
    except Exception as exc:
        ctx.output(f"  Error running guardrail smoke checks: {exc}", "error")

    script = testcode_dir / "test_thinking_strip.py"
    ctx.output("  test_thinking_strip.py ...", "dim")
    try:
        proc = subprocess_mod.run(
            [sys_mod.executable, str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        combined = (proc.stdout + proc.stderr).strip()
        for line in combined.splitlines():
            ctx.output(f"    {line}", "dim" if proc.returncode == 0 else "error")
        ctx.output(
            f"  [test_thinking_strip.py: {'OK' if proc.returncode == 0 else 'FAILED'}]",
            "success" if proc.returncode == 0 else "error",
        )
    except Exception as exc:
        ctx.output(f"  Error running test_thinking_strip.py: {exc}", "error")

    ctx.output(
        "  Full internal test suite is available for manual runs: python -m unittest discover -s testing -p 'test_guardrail*.py'",
        "dim",
    )
    if csv_path is not None and csv_path.exists():
        analyzer = testcode_dir / "test_analyzer.py"
        ctx.output(f"  test_analyzer on {csv_path.name} ...", "dim")
        try:
            proc = subprocess_mod.run(
                [sys_mod.executable, str(analyzer), str(csv_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            for line in (proc.stdout + proc.stderr).splitlines():
                ctx.output(f"    {line}", "dim" if proc.returncode == 0 else "error")
        except Exception as exc:
            ctx.output(f"  Error running test_analyzer: {exc}", "error")


def _cmd_test(arg: str, ctx: SlashCommandContext) -> None:
    import subprocess
    import sys
    import time
    from agent.orchestration.engine import clear_stop

    test_prompts_dir = get_test_prompts_dir()
    wrapper = Path(__file__).resolve().parent.parent / "testing" / "test_wrapper.py"

    if not arg:
        ctx.output(f"Usage: /test <prompts-file|all>  (filename from {test_prompts_dir} or full path)", "dim")
        if test_prompts_dir.exists():
            files = sorted(test_prompts_dir.glob("*.json"))
            if files:
                ctx.output("Available files:", "info")
                for file_path in files:
                    ctx.output(f"  {file_path.name}", "item")
        return

    if arg.strip().lower() == "all":
        clear_stop()
        if not test_prompts_dir.exists():
            ctx.output("Test prompts directory not found.", "error")
            return
        all_files = sorted(test_prompts_dir.glob("*.json"))
        if not all_files:
            ctx.output("No test files found.", "error")
            return

        def _run_all(_files=list(all_files), _wrapper=wrapper, _ctx=ctx) -> None:
            from agent.orchestration.engine import is_stop_requested

            model = _ctx.config.resolved_model
            host = get_active_host()
            now = datetime.now()
            shared_output = get_test_results_dir() / now.strftime("%Y-%m-%d") / f"test_results_{now.strftime('%Y%m%d_%H%M%S')}_all.csv"
            shared_output.parent.mkdir(parents=True, exist_ok=True)
            planned_tests = sum(_count_test_items(file_path) for file_path in _files)
            _ctx.output(f"Running all {len(_files)} test file(s) - host: {host}  model: {model}", "info")
            _ctx.output(f"Planned test count: {planned_tests}", "dim")
            _ctx.output(f"Results file: {shared_output}", "dim")
            total_passed = total_tests = total_prompt_tokens = total_tps_samples = 0
            completed_suites = 0
            run_stopped = False
            suite_results: list[dict] = []
            total_tps_sum = 0.0
            wall_start = time.monotonic()
            bar = "=" * 47
            for index, candidate in enumerate(_files, start=1):
                if is_stop_requested():
                    run_stopped = True
                    break
                _ctx.output(bar, "info")
                _ctx.output(f"= Test Suite: {candidate.stem}", "info")
                _ctx.output(bar, "info")
                _ctx.output(f"[{index}/{len(_files)}] Starting: {candidate.name}", "info")
                result = _run_one_test_file(candidate, _ctx, _wrapper, model, host, re, subprocess, sys, output_file=shared_output)
                if result.get("stopped"):
                    run_stopped = True
                    break
                total_passed += result["passed"]
                total_tests += result["total"]
                total_prompt_tokens += result["prompt_tokens"]
                total_tps_sum += result["tps_sum"]
                total_tps_samples += result["tps_samples"]
                completed_suites += 1
                suite_results.append({
                    "name":   candidate.name,
                    "passed": result["passed"],
                    "total":  result["total"],
                })
            elapsed = time.monotonic() - wall_start
            mins, sec = divmod(int(elapsed), 60)
            time_str = f"{mins}m {sec}s" if mins else f"{sec}s"
            pass_rate = (100.0 * total_passed / total_tests) if total_tests else 0.0
            avg_tps = (total_tps_sum / total_tps_samples) if total_tps_samples else 0.0
            if run_stopped:
                _ctx.output(
                    f"[TEST RUN STOPPED]  host={host}  model={model}  elapsed={time_str}  "
                    f"suites={completed_suites}/{len(_files)}  tests={total_passed}/{total_tests}"
                    f" of planned {planned_tests}  prompt tokens={total_prompt_tokens:,}  avg tok/s={avg_tps:.1f}",
                    "error",
                )
                return

            level = "success" if total_passed == total_tests and total_tests > 0 else "error"
            _ctx.output(
                f"[ALL TESTS COMPLETE]  host={host}  model={model}  elapsed={time_str}  "
                f"pass rate={pass_rate:.0f}% ({total_passed}/{total_tests})  "
                f"prompt tokens={total_prompt_tokens:,}  avg tok/s={avg_tps:.1f}",
                level,
            )
            summary_path = _write_all_run_summary(
                csv_path         = shared_output,
                suite_results    = suite_results,
                elapsed_seconds  = elapsed,
                completed_suites = completed_suites,
                planned_suites   = len(_files),
                planned_tests    = planned_tests,
            )
            _ctx.output(f"Summary written to: {summary_path}", "dim")
            _run_post_test_checks(_ctx, shared_output, _wrapper.parent, subprocess, sys)

        _run_all()
        return

    candidate = Path(arg)
    clear_stop()
    if not candidate.is_absolute():
        candidate = test_prompts_dir / arg
        if not candidate.suffix:
            candidate = candidate.with_suffix(".json")

    if not candidate.exists():
        if test_prompts_dir.exists():
            matches = sorted(file_path for file_path in test_prompts_dir.glob("*.json") if arg.lower() in file_path.stem.lower())
            if matches:
                candidate = matches[0]
                ctx.output(f"Matched: {candidate.name}", "dim")
            else:
                ctx.output(f"No test file matching '{arg}' found.", "error")
                return
        else:
            ctx.output(f"Prompts file not found: {candidate}", "error")
            return

    def _run_single(_candidate=candidate, _wrapper=wrapper, _ctx=ctx) -> None:
        import subprocess
        import sys

        model = _ctx.config.resolved_model
        host = get_active_host()
        now = datetime.now()
        output_file = get_test_results_dir() / now.strftime("%Y-%m-%d") / f"test_results_{now.strftime('%Y%m%d_%H%M%S')}_{_candidate.stem}.csv"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        _ctx.output(f"Running test suite: {_candidate.name} ...", "info")
        _run_one_test_file(_candidate, _ctx, _wrapper, model, host, re, subprocess, sys, output_file=output_file)
        _run_post_test_checks(_ctx, output_file, _wrapper.parent, subprocess, sys)

    _run_single()


def _cmd_testtrend(arg: str, ctx: SlashCommandContext) -> None:
    results_root = get_test_results_dir()
    if not results_root.exists():
        ctx.output("No test results directory found.", "error")
        return

    filter_name = arg.strip().lower().replace(" ", "_") if arg.strip() else ""
    fname_re = re.compile(r"^test_results_(\d{8}_\d{6})_(.+?)\.csv$")
    entries: list[tuple[str, str, Path]] = []
    for csv_path in results_root.rglob("*.csv"):
        if "_analysis" in csv_path.stem or "_gaps" in csv_path.stem:
            continue
        match = fname_re.match(csv_path.name)
        if not match:
            continue
        ts_key = match.group(1)
        prompts_name = match.group(2)
        if filter_name and filter_name not in prompts_name:
            continue
        entries.append((ts_key, prompts_name, csv_path))

    if not entries:
        hint = f" matching '{filter_name}'" if filter_name else ""
        ctx.output(f"No test result files found{hint}.", "dim")
        return

    entries.sort(key=lambda entry: entry[0])
    show_file_col = len({entry[1] for entry in entries}) > 1
    if show_file_col:
        ctx.output(
            f"{'Timestamp':<18}  {'Prompts file':<28}  {'Total':>5}  {'Pass%':>6}  {'Fail':>4}  {'Gap':>4}  {'AvgRnds':>7}  {'AvgSec':>6}  {'Runtime':<9}",
            "info",
        )
    else:
        ctx.output(f"Trend for: {entries[0][1]}", "info")
        ctx.output(
            f"{'Timestamp':<18}  {'Total':>5}  {'Pass%':>6}  {'Fail':>4}  {'Gap':>4}  {'AvgRnds':>7}  {'AvgSec':>6}  {'Runtime':<9}",
            "info",
        )
    ctx.output("-" * (90 if show_file_col else 75), "dim")

    def _fmt_runtime(total_seconds: float) -> str:
        total_int = int(total_seconds)
        mins, secs = divmod(total_int, 60)
        return f"{mins}m {secs:02d}s" if mins else f"{secs}s"

    for ts_key, prompts_name, raw_csv_path in entries:
        ts_display = f"{ts_key[:4]}-{ts_key[4:6]}-{ts_key[6:8]} {ts_key[9:11]}:{ts_key[11:13]}"
        try:
            with raw_csv_path.open(newline="", encoding="utf-8") as handle:
                raw_rows = list(csv.DictReader(handle))
        except OSError:
            ctx.output(f"  {ts_display}  (unreadable)", "error")
            continue
        if not raw_rows:
            ctx.output(f"  {ts_display}  (empty)", "dim")
            continue

        total, passes, fails, gaps = _result_counts(raw_rows, raw_csv_path)
        pass_pct = 100.0 * passes / total if total else 0.0

        durations: list[float] = []
        for row in raw_rows:
            try:
                durations.append(float(row.get("duration_seconds", 0)))
            except (ValueError, TypeError):
                pass
        total_secs = sum(durations)
        avg_dur = total_secs / len(durations) if durations else 0.0

        iter_vals: list[float] = []
        analysis_path = raw_csv_path.with_name(f"{raw_csv_path.stem}_analysis.csv")
        if analysis_path.exists():
            try:
                with analysis_path.open(newline="", encoding="utf-8") as handle:
                    for row in csv.DictReader(handle):
                        try:
                            iter_vals.append(float(row.get("iterations_used", 0)))
                        except (ValueError, TypeError):
                            pass
            except OSError:
                pass
        avg_rounds = sum(iter_vals) / len(iter_vals) if iter_vals else 0.0

        runtime_str = _fmt_runtime(total_secs)
        outcome_marker = "" if passes == total else " !"
        level = "success" if passes == total else "error" if fails > 0 else "dim"
        if show_file_col:
            ctx.output(
                f"{ts_display:<18}  {prompts_name:<28}  {total:>5}  {pass_pct:>5.0f}%  {fails:>4}  {gaps:>4}  {avg_rounds:>7.1f}  {avg_dur:>6.1f}  {runtime_str:<9}{outcome_marker}",
                level,
            )
        else:
            ctx.output(
                f"{ts_display:<18}  {total:>5}  {pass_pct:>5.0f}%  {fails:>4}  {gaps:>4}  {avg_rounds:>7.1f}  {avg_dur:>6.1f}  {runtime_str:<9}{outcome_marker}",
                level,
            )


def register_testing_slash_commands(registry: dict[str, Callable], descriptions: dict[str, str]) -> None:
    registry.update({"/test": _cmd_test, "/testtrend": _cmd_testtrend})
    descriptions.update(
        {
            "/test": "<prompts-file|all>  Run test_wrapper on a prompts file (or all files); streams results live",
            "/testtrend": "[prompts-file]  Show pass-rate trend across all historical test runs (filtered by prompts file if given)",
        }
    )
