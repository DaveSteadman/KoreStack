from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from KoreCommon.suite_paths import get_suite_datacontrol_dir

ROOT            = Path(__file__).resolve().parents[1]
DATA_ROOT       = get_suite_datacontrol_dir() / "koreunittest"
TESTS_DIR       = DATA_ROOT / "tests"
RUNS_DIR        = DATA_ROOT / "runs"
DB_PATH         = DATA_ROOT / "results.sqlite3"
TEST_TIMEOUT_SECONDS = 300
OUTPUT_LIMIT_CHARS   = 60_000
_LOCK   = threading.Lock()
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
    """Every tests/<area>/test_*.py file is one test; its stem is the permanent test ID."""
    found, seen = [], {}
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        if path.stem in seen:
            raise ValueError(f"Duplicate test file name {path.stem}: {seen[path.stem]} and {path}")
        seen[path.stem] = path
        found.append({"id": path.stem, "area": path.parent.name, "path": path})
    return found


def _log_path(build: str, test_id: str) -> Path:
    safe_build = re.sub(r"[^A-Za-z0-9._-]+", "_", build)
    path = RUNS_DIR / safe_build / f"{test_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _clip(text: str) -> str:
    return text if len(text) <= OUTPUT_LIMIT_CHARS else "...[truncated]...\n" + text[-OUTPUT_LIMIT_CHARS:]


def _execute(case: dict) -> tuple[str, dict]:
    """Run one test file in its own interpreter, since each service has its own 'app' package."""
    relative = case["path"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "KoreUnitTest.run_file", str(relative)],
            cwd=str(ROOT), env=env, capture_output=True, timeout=TEST_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or b"") + (exc.stderr or b"")).decode("utf-8", errors="replace")
        return "timeout", {"error": f"Exceeded {TEST_TIMEOUT_SECONDS}s", "output": _clip(output)}
    output = (proc.stdout + proc.stderr).decode("utf-8", errors="replace")
    ran = re.search(r"^Ran (\d+) tests?", output, flags=re.MULTILINE)
    summary = {"returncode": proc.returncode, "tests_run": int(ran.group(1)) if ran else 0, "output": _clip(output)}
    if proc.returncode == 0:
        return "passed", summary
    return ("failed" if ran else "error"), summary


def _run_case(case: dict, build: str) -> None:
    started = datetime.now(timezone.utc)
    events = [{"at": started.isoformat(), "type": "started", "test_id": case["id"], "build_id": build, "file": str(case["path"])}]
    try:
        status, summary = _execute(case)
    except Exception as exc:
        status, summary = "error", {"error": str(exc)}
    finished = datetime.now(timezone.utc)
    events.append({"at": finished.isoformat(), "type": "finished", "status": status, "summary": summary})
    path = _log_path(build, case["id"])
    path.write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")
    conn = _db()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (case["id"], build, status, started.isoformat(), finished.isoformat(),
             (finished - started).total_seconds(), str(path),
             json.dumps({k: v for k, v in summary.items() if k != "output"})),
        )
        conn.commit()
    finally:
        conn.close()


def _pending(build: str) -> list[dict]:
    conn = _db()
    try:
        complete = {row["test_id"] for row in conn.execute("SELECT test_id FROM results WHERE build_id=?", (build,))}
    finally:
        conn.close()
    return [case for case in cases() if case["id"] not in complete]


def start_session(rerun: bool = False) -> dict:
    """Run every test with no result for the current build; rerun=True repeats all of them."""
    global _ACTIVE
    with _LOCK:
        if _ACTIVE:
            return {"started": False, "detail": "A KoreUnitTest session is already running."}
        _ACTIVE = True
    try:
        build = build_id()
        queue = cases() if rerun else _pending(build)
    except Exception:
        with _LOCK:
            _ACTIVE = False
        raise

    def worker() -> None:
        global _ACTIVE
        try:
            for case in queue:
                _run_case(case, build)
        finally:
            with _LOCK:
                _ACTIVE = False

    threading.Thread(target=worker, daemon=True, name="koreunittest").start()
    return {"started": True, "build_id": build, "queued": len(queue)}


def auto_run_if_new_build(delay_seconds: float = 0) -> None:
    """Start a session when the current build still has untested files (called at service start)."""
    def launch() -> None:
        time.sleep(delay_seconds)
        try:
            if _pending(build_id()):
                start_session()
        except Exception:
            pass
    threading.Thread(target=launch, daemon=True, name="koreunittest-autorun").start()


def grid() -> dict:
    current_build = build_id()
    conn = _db()
    try:
        historic = [
            row["build_id"]
            for row in conn.execute("SELECT build_id, MAX(started_at) AS latest FROM results GROUP BY build_id ORDER BY latest DESC")
        ]
        rows = [dict(row) for row in conn.execute("SELECT * FROM results")]
    finally:
        conn.close()
    builds = [current_build, *[build for build in historic if build != current_build]]
    by_key = {(row["test_id"], row["build_id"]): row for row in rows}
    return {
        "build_id": current_build,
        "builds": builds,
        "active": _ACTIVE,
        "tests": [
            {
                "id": case["id"],
                "area": case["area"],
                "results": {
                    build: {"status": by_key[(case["id"], build)]["status"], "finished_at": by_key[(case["id"], build)]["finished_at"]}
                    for build in builds if (case["id"], build) in by_key
                },
            }
            for case in cases()
        ],
    }


def summary(build: str | None = None) -> dict:
    result = grid()
    selected = build or result["build_id"]
    statuses = [test["results"].get(selected, {}).get("status", "pending") for test in result["tests"]]
    conn = _db()
    try:
        rows = conn.execute("SELECT summary_json, elapsed_seconds FROM results WHERE build_id=?", (selected,)).fetchall()
    finally:
        conn.close()
    elapsed = sum(float(row["elapsed_seconds"] or 0) for row in rows)
    tests_run = sum(int(json.loads(row["summary_json"] or "{}").get("tests_run") or 0) for row in rows)
    passed   = statuses.count("passed")
    finished = len(statuses) - statuses.count("pending")
    return {
        "build_id":  selected,
        "active":    result["active"],
        "total":     len(statuses),
        "passed":    passed,
        "failed":    statuses.count("failed") + statuses.count("error") + statuses.count("timeout"),
        "pending":   statuses.count("pending"),
        "pass_rate": round(100 * passed / finished) if finished else 0,
        "tests_run": tests_run,
        "elapsed_seconds": round(elapsed),
        "line": f"[UNIT TESTS] files={finished}/{len(statuses)} passed={passed} cases={tests_run} elapsed={int(elapsed)}s",
    }


def run_detail(test_id: str, build: str | None = None) -> dict:
    selected = build or build_id()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM results WHERE test_id=? AND build_id=?", (test_id, selected)).fetchone()
    finally:
        conn.close()
    if not row:
        return {"test_id": test_id, "build_id": selected, "status": "pending", "events": []}
    path = Path(row["log_path"])
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    detail = dict(row)
    detail["summary"] = json.loads(detail.pop("summary_json"))
    detail["events"] = events
    return detail
