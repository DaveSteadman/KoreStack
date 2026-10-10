from __future__ import annotations

import importlib
import json
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from KoreCommon.suite_paths import get_suite_datacontrol_dir

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = get_suite_datacontrol_dir() / "koretest2"
CASES_DIR = DATA_ROOT / "cases"
RUNS_DIR = DATA_ROOT / "runs"
DB_PATH = DATA_ROOT / "results.sqlite3"
LEGACY_CASES_DIR = get_suite_datacontrol_dir() / "koretest" / "test_prompts"
DEFAULT_TIMEOUT_SECONDS = 1_200
SESSION_LIMIT_SECONDS = 3_600
_LOCK = threading.Lock()
_ACTIVE = False


def build_id() -> str:
    text = (ROOT / "KoreUI" / "UIElements" / "assets" / "js" / "suiteMeta.js").read_text(encoding="utf-8")
    match = re.search(r"SUITE_VERSION\s*=\s*['\"]([^'\"]+)", text)
    if not match:
        raise RuntimeError("SUITE_VERSION is not defined in suiteMeta.js")
    return match.group(1)


def _db() -> sqlite3.Connection:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS results (
        test_id TEXT NOT NULL, build_id TEXT NOT NULL, status TEXT NOT NULL,
        started_at TEXT NOT NULL, finished_at TEXT, elapsed_seconds REAL,
        log_path TEXT NOT NULL, summary_json TEXT NOT NULL,
        PRIMARY KEY (test_id, build_id)
    )""")
    return conn


def cases() -> list[dict]:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    found = []
    for path in sorted(CASES_DIR.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(spec, dict):
            raise ValueError(f"{path.name} must contain a JSON object")
        spec = dict(spec)
        prompt = str(spec.pop("prompt", "") or "").strip()
        if not prompt and isinstance(spec.get("prompts"), list):
            prompt = "\n\n".join(str(item) for item in spec["prompts"])
        found.append({"id": path.stem, "prompt": prompt, "spec": spec, "path": path})
    return found


def _bootstrap_legacy_cases() -> None:
    """Create one KoreTest2 case file for each legacy named exchange once."""
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    if any(CASES_DIR.glob("*.json")):
        return
    for source in sorted(LEGACY_CASES_DIR.glob("*.json")):
        content = json.loads(source.read_text(encoding="utf-8"))
        for index, exchange in enumerate(content, start=1):
            if not isinstance(exchange, dict) or not isinstance(exchange.get("turns"), list):
                continue
            turns = exchange["turns"]
            prompts = [str(turn.get("user") or "") for turn in turns if isinstance(turn, dict)]
            assertions = [str(turn.get("assert") or "not_empty") for turn in turns if isinstance(turn, dict)]
            if not prompts or any(not prompt.strip() for prompt in prompts):
                continue
            source_name = source.stem.removeprefix("test_").removeprefix("kore")
            prefix = "".join(part.title() for part in source_name.split("_"))
            test_id = f"Kore{prefix}_{index:03d}"
            title = str(exchange.get("exchange") or test_id)
            spec = {
                "title": title,
                "prompts": prompts,
                "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                "evaluation": {"type": "python", "assertions": assertions},
            }
            (CASES_DIR / f"{test_id}.json").write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")


def _agent_base() -> str:
    config = json.loads((ROOT / "config" / "korestack_config.json").read_text(encoding="utf-8"))
    return f"http://{config.get('network', {}).get('host', '127.0.0.1')}:{config['services']['koreagent']['port']}"


def _request(method: str, path: str, payload: dict | None = None, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(_agent_base() + path, data=body, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _invoke(prompts: list[str], timeout_seconds: int, events: list[dict]) -> dict:
    session_id = f"koretest2_{uuid.uuid4().hex}"
    run_ids, response_texts = [], []
    tokens, tps_values = 0, []
    deadline = time.monotonic() + timeout_seconds
    try:
        for prompt in prompts:
            submitted = _request("POST", f"/api/sessions/{urllib.parse.quote(session_id, safe='')}/prompt", {"prompt": prompt})
            run_id = str(submitted["run_id"])
            run_ids.append(run_id)
            request = urllib.request.Request(_agent_base() + f"/api/runs/{urllib.parse.quote(run_id, safe='')}/stream")
            response_text = ""
            error = ""
            with urllib.request.urlopen(request, timeout=max(1, int(deadline - time.monotonic()))) as stream:
                for raw in stream:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Test timeout reached")
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    events.append({"at": datetime.now(timezone.utc).isoformat(), "event": event})
                    if event.get("type") == "response":
                        response_text = str(event.get("response") or "")
                        tokens += int(event.get("tokens") or 0)
                        try:
                            tps = float(event.get("tps") or 0)
                        except (TypeError, ValueError):
                            tps = 0.0
                        if tps > 0:
                            tps_values.append(tps)
                    if event.get("type") == "error":
                        error = str(event.get("message") or "Agent error")
                    if event.get("type") == "done":
                        break
            if error:
                raise RuntimeError(error)
            response_texts.append(response_text)
    finally:
        try:
            _request("DELETE", f"/api/sessions/{urllib.parse.quote(session_id, safe='')}")
        except Exception:
            pass
    return {
        "response": response_texts[-1], "responses": response_texts, "run_ids": run_ids,
        "tokens": tokens, "tps": round(sum(tps_values) / len(tps_values), 1) if tps_values else 0.0,
    }


def _evaluate(case: dict, output: str, responses: list[str] | None = None) -> tuple[bool, dict]:
    evaluation = case["spec"].get("evaluation", {})
    if evaluation.get("type") == "python":
        module = str(evaluation.get("module") or "")
        function = str(evaluation.get("function") or "")
        if module and function:
            verdict = getattr(importlib.import_module(module), function)(output, case)
            return bool(verdict), {"type": "python", "evidence": str(verdict)}
        assertions = evaluation.get("assertions")
        if isinstance(assertions, list):
            prompts = list(case["spec"].get("prompts") or [case["prompt"]])
            checked = [
                _check_turn(assertion, response, prompt)
                for assertion, response, prompt in zip(assertions, responses or [], prompts)
            ]
            return len(checked) == len(assertions) and all(item["passed"] for item in checked), {
                "type": "python",
                "assertions": checked,
            }
        asserts = evaluation.get("asserts")
        if asserts is None:
            asserts = [evaluation.get("assert") or "not_empty"]
        if not isinstance(asserts, list) or not asserts:
            raise ValueError("evaluation 'asserts' must be a non-empty list")
        prompt = list(case["spec"].get("prompts") or [case["prompt"]])[-1]
        checked = [_check_assert(str(assertion), output, prompt) for assertion in asserts]
        return all(item["passed"] for item in checked), {"type": "python", "asserts": checked}
    raise ValueError("evaluation must be a Python evaluator with module/function or a supported builtin assert")


# ====================================================================================================
# MARK: JUDGE ASSERTIONS
# ====================================================================================================
JUDGE_DEFAULT_PROBABILITY = 0.7
JUDGE_QUESTIONS = {
    "answers": "Does the response directly and sensibly answer the prompt?",
    "not_error": "Is the response free of error messages, tracebacks, and statements that the task could not be completed?",
}


def _judge(question: str, prompt: str, response: str) -> float:
    """Ask the System One decision model (via KoreAgent's work-packet route) for P(question is true)."""
    packet = {
        "route": "system_one",
        "state": {"prompt": prompt, "response": response},
        "questions": {"verdict": {"type": "noul", "instructions": question}},
    }
    result = _request("POST", "/api/work-packet", {"json_text": json.dumps(packet)}, timeout=300)
    answers = json.loads(result["response"])
    return float(answers["verdict"]["noul"])


def _check_turn(assertion: str | list, response: str, prompt: str) -> dict:
    """One turn's check: a single assertion string, or a list that must all pass."""
    if not isinstance(assertion, list):
        return _check_assert(str(assertion), response, prompt)
    checks = [_check_assert(str(item), response, prompt) for item in assertion]
    return {"assert": [str(item) for item in assertion], "passed": all(check["passed"] for check in checks), "checks": checks}


def _check_assert(expression: str, output: str, prompt: str) -> dict:
    operation, _, value = expression.partition("|")
    if operation != "judge":
        return {"assert": expression, "passed": _evaluate_assert(expression, output)}
    name, _, threshold_text = value.partition("||")
    name = name.strip()
    question = JUDGE_QUESTIONS.get(name, name)
    if not question:
        raise ValueError("judge requires a question or one of: " + ", ".join(JUDGE_QUESTIONS))
    threshold = float(threshold_text) if threshold_text.strip() else JUDGE_DEFAULT_PROBABILITY
    probability = _judge(question, prompt, output)
    return {"assert": expression, "passed": probability >= threshold, "probability": probability, "threshold": threshold}


_NUMBER_RE = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:[,_]\d{3})+|\d+)(?:\.\d+)?(?:[eE][-+]?\d+)?"
    r"|(?<![\w.])[-+]?\.\d+(?:[eE][-+]?\d+)?"
)


def extract_numbers(text: str) -> list[Decimal]:
    """Return every number in text, accepting 1,234 / 1_234 / 1.5e3 / 12.0 style formats."""
    numbers = []
    for token in _NUMBER_RE.findall(text):
        try:
            numbers.append(Decimal(token.replace(",", "").replace("_", "")))
        except InvalidOperation:
            continue
    return numbers


def numbers_equal(left: object, right: object, tolerance: object = 0) -> bool:
    """Compare two numbers given in any supported format, within an absolute tolerance."""
    try:
        a = left if isinstance(left, Decimal) else extract_numbers(str(left))[0]
        b = right if isinstance(right, Decimal) else extract_numbers(str(right))[0]
        return abs(a - b) <= Decimal(str(tolerance))
    except (IndexError, InvalidOperation):
        return False


def _evaluate_numeric(operation: str, value: str, output: str) -> bool:
    """number_equals|expected[||tolerance] or all_numbers|a||b||c — any number in the output may match."""
    parts = [part.strip() for part in value.split("||") if part.strip()]
    if not parts:
        raise ValueError(f"{operation} requires one or more numbers")
    found = extract_numbers(output)
    if operation == "number_equals":
        tolerance = parts[1] if len(parts) > 1 else 0
        return any(numbers_equal(number, parts[0], tolerance) for number in found)
    return all(any(numbers_equal(number, expected) for number in found) for expected in parts)


def _evaluate_assert(expression: str, output: str) -> bool:
    operation, _, value = expression.partition("|")
    if operation in {"number_equals", "all_numbers"}:
        return _evaluate_numeric(operation, value, output)
    normalized_output = output.casefold()
    normalized_value = value.casefold()
    if operation == "contains":
        return normalized_value in normalized_output
    if operation == "not_contains":
        return normalized_value not in normalized_output
    if operation in {"all_contains", "none_contains"}:
        values = [part.strip().casefold() for part in value.split("||") if part.strip()]
        if not values:
            raise ValueError(f"{operation} requires one or more values")
        if operation == "all_contains":
            return all(item in normalized_output for item in values)
        return all(item not in normalized_output for item in values)
    if operation in {"regex", "not_regex"}:
        try:
            matched = bool(re.search(value, output, flags=re.IGNORECASE))
        except re.error as exc:
            raise ValueError(f"Invalid regex assertion: {exc}") from exc
        return matched if operation == "regex" else not matched
    if operation == "not_empty":
        return bool(output.strip())
    raise ValueError(f"Unsupported builtin assertion: {operation}")


def _log_path(build: str, test_id: str) -> Path:
    safe_build = re.sub(r"[^A-Za-z0-9._-]+", "_", build)
    path = RUNS_DIR / safe_build / f"{test_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write_log(path: Path, events: list[dict]) -> None:
    path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")


def _run_case(case: dict, build: str, deadline: float) -> None:
    started = datetime.now(timezone.utc)
    events = [{"at": started.isoformat(), "type": "started", "test_id": case["id"], "build_id": build, "prompt": case["prompt"]}]
    timeout = min(int(case["spec"].get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)), max(1, int(deadline - time.monotonic())))
    status, summary = "error", {}
    try:
        prompts = list(case["spec"].get("prompts") or [case["prompt"]])
        result = _invoke(prompts, timeout, events)
        passed, evaluation = _evaluate(case, result["response"], result.get("responses"))
        status = "passed" if passed else "failed"
        summary = {**result, "evaluation": evaluation}
    except TimeoutError as exc:
        status, summary = "timeout", {"error": str(exc)}
    except Exception as exc:
        status, summary = "error", {"error": str(exc)}
    finished = datetime.now(timezone.utc)
    events.append({"at": finished.isoformat(), "type": "finished", "status": status, "summary": summary})
    path = _log_path(build, case["id"])
    _write_log(path, events)
    conn = _db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case["id"],
                build,
                status,
                started.isoformat(),
                finished.isoformat(),
                (finished - started).total_seconds(),
                str(path),
                json.dumps(summary),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def start_session() -> dict:
    global _ACTIVE
    with _LOCK:
        if _ACTIVE:
            return {"started": False, "detail": "A KoreTest2 session is already running."}
        _ACTIVE = True
    try:
        build = build_id()
    except Exception:
        with _LOCK:
            _ACTIVE = False
        raise

    def worker() -> None:
        global _ACTIVE
        try:
            deadline = time.monotonic() + SESSION_LIMIT_SECONDS
            _bootstrap_legacy_cases()
            conn = _db()
            try:
                complete = {
                    row["test_id"]
                    for row in conn.execute("SELECT test_id FROM results WHERE build_id=?", (build,))
                }
            finally:
                conn.close()
            for case in cases():
                if case["id"] in complete:
                    continue
                if time.monotonic() >= deadline:
                    break
                _run_case(case, build, deadline)
        finally:
            with _LOCK:
                _ACTIVE = False

    threading.Thread(target=worker, daemon=True, name="koretest2").start()
    return {"started": True, "build_id": build}


MAX_BUILD_COLUMNS = 10


def grid() -> dict:
    current_build = build_id()
    conn = _db()
    try:
        historic_builds = [
            row["build_id"]
            for row in conn.execute(
                "SELECT build_id, MAX(started_at) AS latest "
                "FROM results GROUP BY build_id ORDER BY latest DESC"
            )
        ]
        rows = [dict(row) for row in conn.execute("SELECT * FROM results")]
    finally:
        conn.close()

    builds = [current_build, *[build for build in historic_builds if build != current_build]][:MAX_BUILD_COLUMNS]
    by_test_and_build = {(row["test_id"], row["build_id"]): row for row in rows}
    return {
        "build_id": current_build,
        "builds": builds,
        "active": _ACTIVE,
        "tests": [
            {
                "id": case["id"],
                "results": {
                    build: {
                        "status": by_test_and_build[(case["id"], build)]["status"],
                        "finished_at": by_test_and_build[(case["id"], build)]["finished_at"],
                    }
                    for build in builds
                    if (case["id"], build) in by_test_and_build
                },
            }
            for case in cases()
        ],
    }


def summary(build: str | None = None) -> dict:
    result = grid()
    selected = build or result["build_id"]
    statuses = [
        test["results"].get(selected, {}).get("status", "pending")
        for test in result["tests"]
    ]
    conn = _db()
    try:
        rows = conn.execute("SELECT summary_json, elapsed_seconds FROM results WHERE build_id=?", (selected,)).fetchall()
    finally:
        conn.close()
    tokens, tps_values, elapsed = 0, [], 0.0
    for row in rows:
        data = json.loads(row["summary_json"] or "{}")
        tokens += int(data.get("tokens") or 0)
        if float(data.get("tps") or 0) > 0:
            tps_values.append(float(data["tps"]))
        elapsed += float(row["elapsed_seconds"] or 0)
    passed = statuses.count("passed")
    finished = len(statuses) - statuses.count("pending")
    return {
        "build_id": selected,
        "active":   result["active"],
        "total":    len(statuses),
        "passed":   passed,
        "failed":   statuses.count("failed") + statuses.count("error") + statuses.count("timeout"),
        "pending":  statuses.count("pending"),
        "pass_rate": round(100 * passed / finished) if finished else 0,
        "tokens":   tokens,
        "avg_tps":  round(sum(tps_values) / len(tps_values), 1) if tps_values else 0.0,
        "elapsed_seconds": round(elapsed),
        "line": (
            f"[ALL TESTS COMPLETE] elapsed={int(elapsed // 3600)}h {int(elapsed % 3600 // 60)}m {int(elapsed % 60)}s "
            f"pass rate={round(100 * passed / finished) if finished else 0}% ({passed}/{finished}) "
            f"tokens={tokens:,} avg tok/s={round(sum(tps_values) / len(tps_values), 1) if tps_values else 0.0}"
        ),
    }


def run_detail(test_id: str, build: str | None = None) -> dict:
    selected_build = build or build_id()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM results WHERE test_id=? AND build_id=?",
            (test_id, selected_build),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"test_id": test_id, "build_id": selected_build, "status": "pending", "events": []}
    path = Path(row["log_path"])
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    detail = dict(row)
    detail["summary"] = json.loads(detail.pop("summary_json"))
    detail["events"] = events
    return detail
