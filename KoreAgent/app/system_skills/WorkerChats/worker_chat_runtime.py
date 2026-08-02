"""Durable, isolated worker-chat execution and explicit result contracts."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from agent.orchestration.engine import OrchestratorConfig
from agent.orchestration.engine import orchestrate_prompt
from datasets_pkg.models import dataset_save
from llm_client import get_active_model
from llm_client import get_active_num_ctx
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
_TERMINAL    = {"completed", "failed", "cancelled", "timed_out"}
_CANCEL_REASONS: dict[str, str] = {}


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
    last_error: PermissionError | None = None
    for attempt in range(6):
        temp = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        try:
            temp.write_text(payload, encoding="utf-8")
            temp.replace(path)
            return
        except PermissionError as exc:
            last_error = exc
            temp.unlink(missing_ok=True)
            if attempt < 5:
                time.sleep(0.05 * (2 ** attempt))
    if last_error is not None:
        raise last_error


def _update(chat_id: str, **fields) -> dict | None:
    with _LOCK:
        record = _read(chat_id)
        if record is None:
            return None
        record.update(fields)
        _write(record)
        return record


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
    target = str(record.get("result_target") or "scratchpad:prompt_result").strip()
    result = {"summary": answer.strip(), "artefacts": [], "saved_keys": [], "datasets": []}
    result_format = str(record.get("result_format") or "").lower()
    if not result["summary"]:
        return result, "worker returned an empty result"
    if "json" in result_format:
        try:
            json.loads(result["summary"])
        except json.JSONDecodeError as exc:
            return result, f"worker result is not valid JSON: {exc.msg}"
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
    if record is None or record.get("status") in _TERMINAL:
        return
    _update(chat_id, status="running", started_at=_now())
    log_path = create_log_file_path(log_dir=get_logs_dir())
    _update(chat_id, log_path=str(log_path))
    try:
        payload = load_skills_payload(_CATALOG)
        config  = OrchestratorConfig(
            resolved_model=get_active_model(),
            num_ctx=get_active_num_ctx(),
            max_iterations=max(1, min(int(record.get("max_iterations") or 3), 12)),
            skills_payload=payload,
            skills_catalog_path=_CATALOG,
            catalog_mtime=_CATALOG.stat().st_mtime if _CATALOG.exists() else 0.0,
        )
        session_id = str(record["session_id"])
        # A worker starts with no task-specific tools. It can discover and activate the
        # capabilities it needs through the normal tool-selection flow.
        set_selected_tools([], session_id=session_id, persist=False)
        with SessionLogger(log_path) as logger, bind_session(session_id):
            answer, prompt_tokens, completion_tokens, success, tps = orchestrate_prompt(
                user_prompt=_build_prompt(record), config=config, logger=logger,
                conversation_history=None, session_context=None, quiet=True,
                bound_session_id=session_id,
            )
            cancellation = _CANCEL_REASONS.pop(chat_id, "")
            if cancellation:
                terminal = "timed_out" if cancellation == "timeout" else "cancelled"
                cancelled_result = {"summary": "", "artefacts": [], "saved_keys": [], "datasets": [], "error": cancellation}
                _update(chat_id, status=terminal, finished_at=_now(), result=cancelled_result)
                logger.log_file_only(f"[worker-chat] finalised chat_id={chat_id} status={terminal} error={cancellation}")
                return
            result, error = _persist_result(record, str(answer or ""))
            result.update({"prompt_tokens": int(prompt_tokens or 0), "completion_tokens": int(completion_tokens or 0), "tps": float(tps or 0.0), "error": error})
            status = "completed" if success and not error else "failed"
            _update(chat_id, status=status, finished_at=_now(), result=result)
            logger.log_file_only(
                f"[worker-chat] finalised chat_id={chat_id} status={status} "
                f"result_target={record['result_target']} saved_keys={','.join(result['saved_keys']) or 'none'} "
                f"summary={str(result['summary']).replace(chr(10), ' ')[:200]!r}"
            )
    except Exception as exc:
        _update(chat_id, status="failed", finished_at=_now(), result={"summary": "", "artefacts": [], "saved_keys": [], "datasets": [], "error": str(exc)})


def chat_spawn(*, prompt: str, result_target: str = "", result_format: str = "", max_iterations: int = 3, inputs: dict | None = None) -> dict:
    """Run an isolated worker prompt and return its durable result to the caller."""
    prompt = str(prompt or "").strip()
    logger = getattr(_TLS, "logger", None)
    if not prompt:
        return {"status": "error", "error": "prompt is required."}
    if logger is None:
        return {"status": "error", "error": "Worker-chat context is not available."}
    chat_id    = f"chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    session_id = f"worker_chat_{chat_id}"
    parent_id  = get_active_session_id()
    record = {
        "chat_id": chat_id, "session_id": session_id, "parent_session_id": parent_id,
        "status": "queued", "prompt": prompt, "inputs": inputs if isinstance(inputs, dict) else {},
        "result_target": str(result_target or "scratchpad:prompt_result").strip(), "result_format": str(result_format or "").strip(),
        "max_iterations": max(1, min(int(max_iterations or 3), 12)),
        "created_at": _now(), "started_at": None, "finished_at": None, "log_path": "", "result": {},
    }
    with _LOCK:
        _write(record)
    logger.log_file_only(f"[worker-chat] running chat_id={chat_id} session_id={session_id}")
    _run(chat_id)

    completed = _read(chat_id) or record
    result    = completed.get("result") or {}
    status    = str(completed.get("status") or "failed")
    summary   = str(result.get("summary") or "").replace("\n", " ")[:200]
    logger.log_file_only(
        f"[worker-chat] returned chat_id={chat_id} status={status} "
        f"result_target={completed.get('result_target') or record['result_target']} summary={summary!r}"
    )
    return {
        "status":        status,
        "chat_id":       chat_id,
        "session_id":    session_id,
        "ready":         status in _TERMINAL,
        "result":        result,
        "result_target": completed.get("result_target") or record["result_target"],
        "error":         str(result.get("error") or ""),
    }


def chat_status(chat_id: str) -> dict:
    record = _read(str(chat_id or "").strip())
    if record is None:
        return {"chat_id": chat_id, "status": "error", "error": "Worker chat not found."}
    return {"chat_id": record["chat_id"], "status": record["status"], "session_id": record["session_id"], "error": (record.get("result") or {}).get("error", "")}


def _request_cancel(chat_id: str, reason: str) -> None:
    """Persist cancellation intent and stop the active orchestration run when possible."""
    _CANCEL_REASONS[chat_id] = reason
    record = _read(chat_id)
    if record and record.get("status") == "queued":
        _update(chat_id, status="cancelled", finished_at=_now(), result={"error": reason})
    try:
        from agent.orchestration.engine import request_stop
        request_stop(reason)
    except Exception:
        pass


def chat_result(chat_id: str) -> dict:
    status = chat_status(chat_id)
    record = _read(str(chat_id or "").strip())
    status["ready"] = bool(record and record.get("status") in _TERMINAL)
    status["result"] = (record or {}).get("result") or {}
    return status


def chat_cancel(chat_id: str) -> dict:
    """Cancel a queued or running worker chat and preserve a terminal status record."""
    record = _read(str(chat_id or "").strip())
    if record is None:
        return {"chat_id": chat_id, "status": "error", "error": "Worker chat not found."}
    if record.get("status") in _TERMINAL:
        return chat_status(chat_id)
    _request_cancel(str(record["chat_id"]), "cancelled by caller")
    return chat_status(chat_id)
