"""Durable, isolated worker-chat execution and explicit result contracts."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from agent.orchestration.engine import OrchestratorConfig
from agent.orchestration.engine import orchestrate_prompt
from datasets_pkg.models import dataset_save
from scratchpad import scratchpad_save
from sessions.runtime import bind_session
from sessions.runtime import get_active_session_id
from sessions.tool_selection import set_selected_tools
from skills_catalog_builder import load_skills_payload
from system_skills.FileAccess.file_access_skill import file_write
from system_skills.WorkerChats.worker_chat_context import get_worker_chat_runtime_tls
from utils.runtime_logger import SessionLogger
from utils.runtime_logger import create_log_file_path
from utils.workspace_utils import get_controldata_dir
from utils.workspace_utils import get_logs_dir


_LOCK        = threading.RLock()
_TLS         = get_worker_chat_runtime_tls()
_CATALOG     = Path(__file__).resolve().parents[2] / "skills" / "skills_catalog.json"
_TERMINAL    = {"completed", "failed"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _record_path(chat_id: str) -> Path:
    directory = get_controldata_dir() / "koreagent" / "worker_chats"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{chat_id}.json"


def _read(chat_id: str) -> dict | None:
    try:
        return json.loads(_record_path(chat_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write(record: dict) -> None:
    path    = _record_path(str(record["chat_id"]))
    payload = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    temp    = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temp.write_text(payload, encoding="utf-8")
    temp.replace(path)


def _update(chat_id: str, **fields) -> dict | None:
    with _LOCK:
        record = _read(chat_id)
        if record is None:
            return None
        record.update(fields)
        _write(record)
        return record


def _tools(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _build_prompt(record: dict) -> str:
    inputs = record.get("inputs") if isinstance(record.get("inputs"), dict) else {}
    lines = [
        "You are an isolated worker chat.",
        "Complete only the assigned prompt. Return the requested result, not commentary about delegation.",
        "",
        "Worker prompt:",
        str(record.get("prompt") or ""),
    ]
    if inputs:
        lines.extend(["", "Structured inputs:", json.dumps(inputs, ensure_ascii=False, sort_keys=True)])
    result_format = str(record.get("result_format") or "").strip()
    if result_format:
        lines.extend(["", f"Required result format: {result_format}"])
    return "\n".join(lines)


def _persist_result(record: dict, answer: str) -> tuple[dict, str]:
    target = str(record.get("result_target") or "").strip()
    result = {"summary": answer.strip(), "artefacts": [], "saved_keys": [], "datasets": []}
    if not target:
        return result, ""
    if target.startswith("scratchpad:"):
        key = target.split(":", 1)[1].strip()
        if not key:
            return result, "result_target scratchpad key is empty"
        scratchpad_save(key, result["summary"], session_id=str(record.get("parent_session_id") or ""))
        result["saved_keys"].append(key)
        return result, ""
    if target.startswith("file:"):
        path = target.split(":", 1)[1].strip()
        written = file_write(path=path, content=result["summary"], skip_content_guard=True)
        if str(written).startswith("Error:"):
            return result, str(written)
        result["artefacts"].append(path)
        return result, ""
    if target.startswith("dataset:"):
        name = target.split(":", 1)[1].strip()
        try:
            records = json.loads(result["summary"])
            if isinstance(records, dict):
                records = [records]
            if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
                raise ValueError("result must be a JSON array of objects")
            dataset_save(name, records, source_tool="chat_spawn", source_args={"chat_id": record["chat_id"]}, replace=True, session_id=str(record.get("parent_session_id") or ""))
            result["datasets"].append(name)
            return result, ""
        except Exception as exc:
            return result, f"could not persist dataset result: {exc}"
    return result, f"unsupported result_target '{target}'"


def _run(chat_id: str) -> None:
    record = _read(chat_id)
    if record is None:
        return
    _update(chat_id, status="running", started_at=_now())
    log_path = create_log_file_path(log_dir=get_logs_dir())
    _update(chat_id, log_path=str(log_path))
    try:
        payload = load_skills_payload(_CATALOG)
        config  = OrchestratorConfig(
            resolved_model=str(record.get("model") or ""),
            num_ctx=int(record.get("num_ctx") or 32768),
            max_iterations=max(1, min(int(record.get("max_iterations") or 3), 12)),
            skills_payload=payload,
            skills_catalog_path=_CATALOG,
            catalog_mtime=_CATALOG.stat().st_mtime if _CATALOG.exists() else 0.0,
        )
        session_id = str(record["session_id"])
        set_selected_tools(_tools(record.get("tools_allowlist")), session_id=session_id, persist=False)
        with SessionLogger(log_path) as logger, bind_session(session_id):
            answer, prompt_tokens, completion_tokens, success, tps = orchestrate_prompt(
                user_prompt=_build_prompt(record), config=config, logger=logger,
                conversation_history=None, session_context=None, quiet=True,
                bound_session_id=session_id,
            )
        result, error = _persist_result(record, str(answer or ""))
        result.update({"prompt_tokens": int(prompt_tokens or 0), "completion_tokens": int(completion_tokens or 0), "tps": float(tps or 0.0), "error": error})
        _update(chat_id, status="completed" if success and not error else "failed", finished_at=_now(), result=result)
    except Exception as exc:
        _update(chat_id, status="failed", finished_at=_now(), result={"summary": "", "artefacts": [], "saved_keys": [], "datasets": [], "error": str(exc)})


def chat_spawn(*, prompt: str, tools_allowlist: list[str], result_target: str = "", result_format: str = "", max_iterations: int = 3, inputs: dict | None = None) -> dict:
    """Create and queue an isolated worker chat without switching the parent session."""
    prompt = str(prompt or "").strip()
    tools  = _tools(tools_allowlist)
    logger = getattr(_TLS, "logger", None)
    config = getattr(_TLS, "config", None)
    if not prompt or not tools:
        return {"status": "error", "error": "prompt and tools_allowlist are required."}
    if logger is None or config is None:
        return {"status": "error", "error": "Worker-chat context is not available."}
    chat_id    = f"chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    session_id = f"worker_chat_{chat_id}"
    parent_id  = get_active_session_id()
    record = {
        "chat_id": chat_id, "session_id": session_id, "parent_session_id": parent_id,
        "status": "queued", "prompt": prompt, "inputs": inputs if isinstance(inputs, dict) else {},
        "tools_allowlist": tools, "result_target": str(result_target or "").strip(), "result_format": str(result_format or "").strip(),
        "max_iterations": max(1, min(int(max_iterations or 3), 12)), "model": config.resolved_model, "num_ctx": int(config.num_ctx),
        "created_at": _now(), "started_at": None, "finished_at": None, "log_path": "", "result": {},
    }
    with _LOCK:
        _write(record)
    from scheduler.scheduler import task_queue
    parent = task_queue.get_active_for_session(parent_id)
    chain_id = str((parent or {}).get("metadata", {}).get("chain_id") or (parent or {}).get("name") or chat_id)
    queued = task_queue.enqueue(
        f"worker_chat_{chat_id}", "task_run", lambda _id=chat_id: _run(_id), label=prompt[:64],
        metadata={"workflow": "worker_chat", "chain_id": chain_id, "chain_stage": "child", "worker_chat_id": chat_id, "parent_session_id": parent_id, "child_session_id": session_id},
    )
    if not queued:
        _update(chat_id, status="failed", finished_at=_now(), result={"error": "Worker chat could not be queued."})
        return {"status": "error", "chat_id": chat_id, "error": "Worker chat could not be queued."}
    logger.log_file_only(f"[worker-chat] queued chat_id={chat_id} session_id={session_id}")
    return {"status": "queued", "chat_id": chat_id, "session_id": session_id, "result_target": record["result_target"]}


def chat_status(chat_id: str) -> dict:
    record = _read(str(chat_id or "").strip())
    if record is None:
        return {"chat_id": chat_id, "status": "error", "error": "Worker chat not found."}
    return {"chat_id": record["chat_id"], "status": record["status"], "session_id": record["session_id"], "error": (record.get("result") or {}).get("error", "")}


def chat_result(chat_id: str) -> dict:
    status = chat_status(chat_id)
    record = _read(str(chat_id or "").strip())
    status["ready"] = bool(record and record.get("status") in _TERMINAL)
    status["result"] = (record or {}).get("result") or {}
    return status
