from __future__ import annotations

import csv
import json
import os
import sqlite3
import subprocess
import sys
import threading
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
        model TEXT, git_version TEXT, test_chat_id INTEGER, status TEXT NOT NULL, result_json TEXT NOT NULL
    )""")
    return conn


def _recover_stale_runs(conn: sqlite3.Connection) -> None:
    """Close runs left open by a KoreTest restart or process interruption."""
    cutoff = datetime.now(timezone.utc).timestamp() - 300
    rows = conn.execute("SELECT run_id, started_at FROM test_runs WHERE status='running'").fetchall()
    for row in rows:
        try:
            started = datetime.fromisoformat(str(row["started_at"])).timestamp()
        except ValueError:
            started = 0
        if started < cutoff:
            conn.execute(
                "UPDATE test_runs SET finished_at=?, status=?, result_json=? WHERE run_id=?",
                (datetime.now(timezone.utc).isoformat(), "interrupted", json.dumps({"reason": "KoreTest restarted before the run was recorded."}), row["run_id"]),
            )
    conn.commit()


def _http(method: str, url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
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


def _run_suite(name: str, model: str | None = None) -> dict:
    suite = _suite_path(name)
    run_id = f"testrun_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}"
    chat_id = _fresh_test_chat()
    started = datetime.now(timezone.utc).isoformat()
    version = _git_version()
    conn = _db()
    conn.execute("INSERT INTO test_runs VALUES (?, ?, NULL, ?, ?, ?, ?, 'running', '{}')", (run_id, started, suite.name, model or "", version, chat_id))
    conn.commit()
    prompts = json.loads(suite.read_text(encoding="utf-8"))
    results_dir = get_suite_datacontrol_dir() / "test_results" / datetime.now().strftime("%Y-%m-%d")
    results_dir.mkdir(parents=True, exist_ok=True)
    output = results_dir / f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{suite.stem}.csv"
    command = [sys.executable, str(ROOT / "KoreTest" / "app" / "system" / "runner.py"), "--prompts-file", str(suite), "--output-file", str(output), "--source-file", suite.name]
    if model:
        command.extend(["--model", model])
    proc = subprocess.run(command, cwd=APP_ROOT, text=True, capture_output=True)
    rows = list(csv.DictReader(output.open(encoding="utf-8", newline=""))) if output.exists() else []
    passed = sum(row.get("passed", "").upper() in {"PASS", "TRUE"} for row in rows)
    result = {"run_id": run_id, "suite": suite.name, "exit_code": proc.returncode, "total": len(rows), "passed": passed, "failed": len(rows) - passed, "csv": str(output), "stdout": proc.stdout[-12000:], "stderr": proc.stderr[-4000:]}
    korechat, _agent = _urls()
    for row in rows:
        _http("POST", f"{korechat}/api/conversations/{chat_id}/turns", {"inbound_content": row.get("prompt", ""), "outbound_content": row.get("final_output", ""), "inbound_sender": "KoreTest", "outbound_sender": "KoreAgent"})
    status = "passed" if proc.returncode == 0 and passed == len(rows) else "failed"
    conn.execute("UPDATE test_runs SET finished_at=?, status=?, result_json=? WHERE run_id=?", (datetime.now(timezone.utc).isoformat(), status, json.dumps(result), run_id))
    conn.commit(); conn.close()
    return result | {"status": status, "test_chat_id": chat_id, "version": version, "console": proc.stdout}


def _run_requested_suite(name: str, model: str | None = None) -> dict:
    if name.strip().lower() != "all":
        return _run_suite(name, model)
    base = Path(os.environ.get("KORE_TEST_PROMPTS_DIR", str(get_suite_datacontrol_dir() / "test_prompts")))
    suite_results = [_run_suite(path.name, model) for path in sorted(base.glob("*.json"))]
    total  = sum(int(result["total"]) for result in suite_results)
    passed = sum(int(result["passed"]) for result in suite_results)
    return {
        "suite": "all",
        "status": "passed" if total and passed == total else "failed",
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "runs": suite_results,
        "console": "\n".join(str(result.get("console", "")) for result in suite_results),
    }


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
    _recover_stale_runs(conn)
    rows = conn.execute("SELECT * FROM test_runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()
    return {"runs": [{**dict(row), "result": json.loads(row["result_json"])} for row in rows]}


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
