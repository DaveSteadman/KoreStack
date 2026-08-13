from __future__ import annotations

import csv
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.fastmcp import FastMCP
import uvicorn


ROOT       = Path(__file__).resolve().parents[1]
APP_ROOT   = ROOT / "KoreAgent" / "app"
CONFIG     = ROOT / "config" / "korestack_config.json"
sys.path.insert(0, str(ROOT))
from KoreCommon.suite_paths import get_suite_datacontrol_dir
from KoreCommon.service_app import register_suite_shell_routes

DATA_ROOT  = Path(os.environ.get("KORE_TEST_DATA_DIR", str(get_suite_datacontrol_dir() / "koretest"))).resolve()
DB_PATH    = DATA_ROOT / "runs.sqlite3"
TEST_CHAT_EXTERNAL_ID = "koretest:TEST"
UI_ROOT    = ROOT / "KoreUI" / "KoreTest"
UI_ELEMENTS_ASSETS = ROOT / "KoreUI" / "UIElements" / "assets"
_RUN_LOCK = threading.Lock()


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _urls() -> tuple[str, str]:
    cfg = _config()
    host = cfg.get("network", {}).get("host", "127.0.0.1")
    services = cfg.get("services", {})
    return f"http://{host}:{services['korechat']['port']}", f"http://{host}:{services['koreagent']['port']}"


def _db() -> sqlite3.Connection:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS test_runs (
        run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, suite TEXT NOT NULL,
        model TEXT, git_version TEXT, test_chat_id INTEGER, collection_id TEXT, status TEXT NOT NULL, result_json TEXT NOT NULL
    )""")
    columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(test_runs)")}
    if "collection_id" not in columns:
        conn.execute("ALTER TABLE test_runs ADD COLUMN collection_id TEXT")
        conn.commit()
    return conn


def _recover_interrupted_runs(conn: sqlite3.Connection) -> None:
    """Close runs owned by a previous KoreTest process during startup.

    Full test collections routinely run for much longer than five minutes, so
    active runs must never be inferred stale merely from their start time.
    """
    rows = conn.execute("SELECT run_id FROM test_runs WHERE status='running'").fetchall()
    for row in rows:
        conn.execute(
            "UPDATE test_runs SET finished_at=?, status=?, result_json=? WHERE run_id=?",
            (datetime.now(timezone.utc).isoformat(), "interrupted", json.dumps({"reason": "KoreTest restarted before the run was recorded."}), row["run_id"]),
        )
    conn.commit()


def _http(method: str, url: str, payload: dict | None = None, *, timeout: float = 15.0) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8")) if response.length != 0 else {}


def _fresh_test_chat() -> int:
    korechat, _agent = _urls()
    try:
        existing = _http("GET", f"{korechat}/api/conversations/by-external-id/{TEST_CHAT_EXTERNAL_ID}")
        _http("DELETE", f"{korechat}/api/conversations/{existing['id']}")
    except Exception:
        pass
    created = _http("POST", f"{korechat}/api/conversations", {
        "channel_type": "test", "subject": "TEST", "protected": True, "external_id": TEST_CHAT_EXTERNAL_ID,
    })
    return int(created["id"])


def _git_version() -> str:
    try:
        return subprocess.check_output(["git", "describe", "--always", "--dirty"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def _suite_path(name: str) -> Path:
    base = Path(os.environ.get("KORE_TEST_PROMPTS_DIR", str(get_suite_datacontrol_dir() / "test_prompts")))
    if name.strip().lower() == "all":
        raise HTTPException(status_code=400, detail="'all' is a suite collection, not a suite path")
    candidate = (base / name).with_suffix(".json") if not name.endswith(".json") else base / name
    if not candidate.exists():
        matches = sorted(path for path in base.glob("*.json") if name.lower() in path.stem.lower())
        if matches:
            candidate = matches[0]
    if not candidate.exists() or candidate.parent.resolve() != base.resolve():
        raise HTTPException(status_code=404, detail="Test suite not found")
    return candidate


def _run_suite(name: str, model: str | None = None, *, collection_id: str = "") -> dict:
    suite = _suite_path(name)
    run_id = f"testrun_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
    chat_id = _fresh_test_chat()
    started = datetime.now(timezone.utc).isoformat()
    version = _git_version()
    prompts = json.loads(suite.read_text(encoding="utf-8"))
    results_dir = get_suite_datacontrol_dir() / "test_results" / datetime.now().strftime("%Y-%m-%d")
    results_dir.mkdir(parents=True, exist_ok=True)
    output = results_dir / f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{suite.stem}.csv"
    initial_result = {
        "run_id": run_id,
        "suite": suite.name,
        "csv": str(output),
        "progress": {"completed_tests": 0, "total_tests": len(prompts), "passed_tests": 0, "failed_tests": 0},
    }
    conn = _db()
    conn.execute(
        "INSERT INTO test_runs (run_id, started_at, finished_at, suite, model, git_version, test_chat_id, collection_id, status, result_json) "
        "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'running', ?)",
        (run_id, started, suite.name, model or "", version, chat_id, collection_id, json.dumps(initial_result)),
    )
    conn.commit()
    command = [sys.executable, str(ROOT / "KoreTest" / "app" / "system" / "runner.py"), "--prompts-file", str(suite), "--output-file", str(output), "--source-file", suite.name]
    if model:
        command.extend(["--model", model])
    proc = subprocess.run(command, cwd=APP_ROOT, text=True, capture_output=True)
    rows = list(csv.DictReader(output.open(encoding="utf-8", newline=""))) if output.exists() else []
    passed = sum(row.get("passed", "").upper() in {"PASS", "TRUE"} for row in rows)
    result = {"run_id": run_id, "suite": suite.name, "exit_code": proc.returncode, "total": len(rows), "passed": passed, "failed": len(rows) - passed, "csv": str(output), "stdout": proc.stdout[-12000:], "stderr": proc.stderr[-4000:]}
    result["progress"] = _live_progress(suite.name, result)
    archive_error = ""
    korechat, _agent = _urls()
    for row in rows:
        try:
            _http(
                "POST",
                f"{korechat}/api/conversations/{chat_id}/turns",
                {"inbound_content": row.get("prompt", ""), "outbound_content": row.get("final_output", ""), "inbound_sender": "KoreTest", "outbound_sender": "KoreAgent"},
                timeout=3.0,
            )
        except Exception as exc:
            archive_error = f"KoreChat archival stopped: {exc.__class__.__name__}"
            break
    if archive_error:
        result["archive_error"] = archive_error
    status = "passed" if proc.returncode == 0 and passed == len(rows) else "failed"
    conn.execute("UPDATE test_runs SET finished_at=?, status=?, result_json=? WHERE run_id=?", (datetime.now(timezone.utc).isoformat(), status, json.dumps(result), run_id))
    conn.commit(); conn.close()
    return result | {"status": status, "test_chat_id": chat_id, "version": version, "console": proc.stdout}


def _live_progress(suite_name: str, result: dict) -> dict:
    progress = dict(result.get("progress") or {})
    try:
        total_tests = len(json.loads(_suite_path(suite_name).read_text(encoding="utf-8")))
    except Exception:
        total_tests = int(progress.get("total_tests") or 0)
    csv_value = str(result.get("csv") or "").strip()
    csv_path  = Path(csv_value) if csv_value else None
    exchanges: dict[str, bool] = {}
    if csv_path is not None and csv_path.is_file():
        for row in csv.DictReader(csv_path.open(encoding="utf-8", newline="")):
            exchange = str(row.get("exchange_name") or "")
            exchanges[exchange] = str(row.get("passed") or "").upper() == "PASS"
    progress.update({
        "completed_tests": len(exchanges),
        "total_tests":     total_tests,
        "passed_tests":    sum(exchanges.values()),
        "failed_tests":    len(exchanges) - sum(exchanges.values()),
    })
    return progress


def _format_elapsed(seconds: float) -> str:
    total   = max(0, round(seconds))
    hours   = total // 3600
    minutes = (total % 3600) // 60
    rest    = total % 60
    return f"{hours}h {minutes}m {rest}s"


def _collection_stats(suite_results: list[dict], *, elapsed_seconds: float, model: str | None) -> dict:
    exchanges: dict[tuple[str, str], bool] = {}
    prompt_tokens = 0
    throughput_samples: list[float] = []
    for result in suite_results:
        csv_path = Path(str(result.get("csv") or ""))
        if not csv_path.exists():
            continue
        for row in csv.DictReader(csv_path.open(encoding="utf-8", newline="")):
            key = (str(row.get("source_file") or ""), str(row.get("exchange_name") or ""))
            exchanges[key] = str(row.get("passed") or "").upper() == "PASS"
            try:
                prompt_tokens += int(float(row.get("prompt_tokens") or 0))
            except ValueError:
                continue

        for match in re.finditer(r"\[TURN \d+\] tokens=\d+ tps=([\d.]+)", str(result.get("stdout") or "")):
            try:
                tokens_per_second = float(match.group(1))
            except ValueError:
                continue
            if tokens_per_second > 0:
                throughput_samples.append(tokens_per_second)

    total  = len(exchanges)
    passed = sum(exchanges.values())
    _korechat_url, agent_url = _urls()
    try:
        runtime = _http("GET", f"{agent_url}/status", timeout=3.0)
    except Exception:
        runtime = {}
    config = json.loads((ROOT / "config" / "koreagent_config.json").read_text(encoding="utf-8"))
    host = str(runtime.get("host") or config.get("llmhost") or "unknown")
    active_model = str(model or runtime.get("model") or config.get("model") or "default")
    pass_rate = round((100 * passed / total) if total else 0)
    tokens_per_second = sum(throughput_samples) / len(throughput_samples) if throughput_samples else 0.0
    stats_line = (
        f"[ALL TESTS COMPLETE]  host={host}  model={active_model}  "
        f"elapsed={_format_elapsed(elapsed_seconds)}  pass rate={pass_rate}% ({passed}/{total})  "
        f"prompt tokens={prompt_tokens:,}  avg tok/s={tokens_per_second:.1f}"
    )
    return {
        "total":         total,
        "passed":        passed,
        "failed":        total - passed,
        "prompt_tokens": prompt_tokens,
        "avg_tok_s":     round(tokens_per_second, 1),
        "stats_line":    stats_line,
    }


def _start_collection_run(collection_id: str, model: str | None) -> None:
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO test_runs (run_id, started_at, finished_at, suite, model, git_version, test_chat_id, collection_id, status, result_json) "
            "VALUES (?, ?, NULL, ?, ?, ?, NULL, ?, 'running', ?)",
            (
                collection_id,
                datetime.now(timezone.utc).isoformat(),
                "all",
                model or "",
                _git_version(),
                collection_id,
                json.dumps({"suite": "all"}),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _finish_collection_run(collection_id: str, result: dict) -> None:
    conn = _db()
    try:
        conn.execute(
            "UPDATE test_runs SET finished_at=?, status=?, result_json=? WHERE run_id=?",
            (datetime.now(timezone.utc).isoformat(), str(result["status"]), json.dumps(result), collection_id),
        )
        conn.commit()
    finally:
        conn.close()


def _run_requested_suite(name: str, model: str | None = None) -> dict:
    if not _RUN_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A KoreTest run is already in progress.")
    try:
        if name.strip().lower() != "all":
            return _run_suite(name, model)
        base = Path(os.environ.get("KORE_TEST_PROMPTS_DIR", str(get_suite_datacontrol_dir() / "test_prompts")))
        started_at = time.monotonic()
        collection_id = f"testcollection_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
        _start_collection_run(collection_id, model)
        suite_results = [_run_suite(path.name, model, collection_id=collection_id) for path in sorted(base.glob("*.json"))]
        stats = _collection_stats(suite_results, elapsed_seconds=time.monotonic() - started_at, model=model)
        print(stats["stats_line"], flush=True)
        result = {
            "suite": "all",
            "status": "passed" if stats["total"] and stats["passed"] == stats["total"] else "failed",
            **stats,
            "runs": suite_results,
            "collection_id": collection_id,
            "console": "\n".join([*(str(result.get("console", "")) for result in suite_results), stats["stats_line"]]),
        }
        _finish_collection_run(collection_id, result)
        return result
    finally:
        _RUN_LOCK.release()


def _trend_lines(filter_name: str = "") -> list[str]:
    points = _trend_points(filter_name)
    if not points:
        return ["No matching test runs found."]
    return ["Run                           Total  Pass%"] + [f"{point['label']:<29} {point['total']:>5}  {point['pass_rate']:>5.0f}%" for point in points]


def _trend_points(filter_name: str = "") -> list[dict]:
    points: list[dict] = []
    normalized = filter_name.lower().replace(" ", "_").removesuffix(".json")
    result_root = get_suite_datacontrol_dir() / "test_results"
    for csv_path in result_root.rglob("test_results_*.csv"):
        if "_analysis" in csv_path.stem or (normalized and normalized not in csv_path.stem.lower()):
            continue
        try:
            rows = list(csv.DictReader(csv_path.open(encoding="utf-8", newline="")))
        except OSError:
            continue
        total  = len(rows)
        passed = sum(row.get("passed", "").upper() in {"PASS", "TRUE"} for row in rows)
        duration_seconds = sum(float(row.get("duration_seconds") or 0) for row in rows)
        has_token_data   = any(str(row.get("prompt_tokens", "")).strip() for row in rows)
        prompt_tokens    = sum(int(float(row.get("prompt_tokens") or 0)) for row in rows) if has_token_data else None
        points.append({
            "label":     csv_path.stem.removeprefix("test_results_"),
            "passed":    passed,
            "total":     total,
            "pass_rate": round(100 * passed / total, 1) if total else 0.0,
            "duration_seconds": round(duration_seconds, 1),
            "prompt_tokens":    prompt_tokens,
        })
    return sorted(points, key=lambda point: point["label"])


def _run_unit_checks() -> dict:
    command = [sys.executable, str(ROOT / "KoreTest" / "app" / "unit" / "runner.py")]
    proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, encoding="utf-8")
    return {"passed": proc.returncode == 0, "console": proc.stdout + proc.stderr}


app = FastAPI(title="KoreTest")
_mcp = FastMCP("KoreTest")
register_suite_shell_routes(
    app,
    service_key            = "koretest",
    service_label          = "KoreTest",
    ui_elements_assets_dir = UI_ELEMENTS_ASSETS,
)
app.mount("/static", StaticFiles(directory=str(UI_ROOT / "static")), name="koretest-static")


@app.on_event("startup")
def recover_interrupted_runs() -> None:
    conn = _db()
    try:
        _recover_interrupted_runs(conn)
    finally:
        conn.close()


@app.get("/status")
def status(): return {"ok": True, "service": "KoreTest"}


@app.get("/api/suites")
def suites():
    base = Path(os.environ.get("KORE_TEST_PROMPTS_DIR", str(get_suite_datacontrol_dir() / "test_prompts")))
    return {"suites": [path.name for path in sorted(base.glob("*.json"))]}


@app.post("/api/runs")
def run_suite(payload: dict): return _run_requested_suite(str(payload.get("suite") or ""), str(payload.get("model") or "") or None)


@app.get("/api/runs")
def runs(limit: int = 50):
    conn = _db()
    try:
        rows = conn.execute("SELECT * FROM test_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
        payload: list[dict] = []
        for row in rows:
            item = dict(row)
            result = json.loads(row["result_json"])
            if row["status"] == "running":
                result["progress"] = _live_progress(str(row["suite"]), result)
            item["result"] = result
            payload.append(item)
        return {"runs": payload}
    finally:
        conn.close()


@app.get("/api/trends")
def trends(filter: str = ""): return {"lines": _trend_lines(filter)}


@app.get("/api/trend-points")
def trend_points(suite: str = ""): return {"points": _trend_points(suite)}


@app.post("/api/unit-runs")
def unit_runs(): return _run_unit_checks()


@app.get("/ui", include_in_schema=False)
def ui() -> FileResponse:
    return FileResponse(UI_ROOT / "static" / "test" / "index.html")


@_mcp.tool()
def test_list_suites() -> dict: return suites()

@_mcp.tool()
def test_run_suite(name: str, model: str = "") -> dict: return _run_requested_suite(name, model or None)

@_mcp.tool()
def test_list_runs(limit: int = 50) -> dict: return runs(limit)

@_mcp.tool()
def test_get_trend(name: str = "") -> dict: return {"lines": _trend_lines(name)}


app.mount("/mcp", _mcp.streamable_http_app())

if __name__ == "__main__":
    port = int(_config()["services"]["koretest"]["port"])
    uvicorn.run(app, host="127.0.0.1", port=port)
