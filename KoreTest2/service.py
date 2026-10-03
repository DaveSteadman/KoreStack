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
    for path in sorted(CASES_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if not match:
            continue
        spec = json.loads(match.group(1))
        prompt = text[:match.start()].strip()
        if prompt.startswith("#"):
            prompt = "\n".join(prompt.splitlines()[1:]).strip()
        found.append({"id": path.stem, "prompt": prompt, "spec": spec, "path": path})
    return found


def _bootstrap_legacy_cases() -> None:
    """Create one KoreTest2 case file for each legacy named exchange once."""
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    if any(CASES_DIR.glob("*.md")):
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
                "prompts": prompts,
                "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                "evaluation": {"type": "python", "assertions": assertions},
            }
            body = "\n\n".join(f"Turn {turn}:\n\n{prompt}" for turn, prompt in enumerate(prompts, start=1))
            (CASES_DIR / f"{test_id}.md").write_text(
                f"# {test_id} — {title}\n\n{body}\n\n```json\n{json.dumps(spec, indent=2)}\n```\n",
                encoding="utf-8",
            )


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
    return {"response": response_texts[-1], "responses": response_texts, "run_ids": run_ids}


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
            checked = [
                {"assert": str(assertion), "passed": _evaluate_assert(str(assertion), response)}
                for assertion, response in zip(assertions, responses or [])
            ]
            return len(checked) == len(assertions) and all(item["passed"] for item in checked), {
                "type": "python",
                "assertions": checked,
            }
        assertion = str(evaluation.get("assert") or "not_empty")
        passed = _evaluate_assert(assertion, output)
        return passed, {"type": "python", "assert": assertion}
    raise ValueError("evaluation must be a Python evaluator with module/function or a supported builtin assert")


def _evaluate_assert(expression: str, output: str) -> bool:
    operation, _, value = expression.partition("|")
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

    builds = [current_build, *[build for build in historic_builds if build != current_build]]
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


def summary() -> dict:
    result = grid()
    statuses = [
        test["results"].get(result["build_id"], {}).get("status", "pending")
        for test in result["tests"]
    ]
    return {
        "build_id": result["build_id"],
        "active":   result["active"],
        "total":    len(statuses),
        "passed":   statuses.count("passed"),
        "failed":   statuses.count("failed") + statuses.count("error") + statuses.count("timeout"),
        "pending":  statuses.count("pending"),
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
