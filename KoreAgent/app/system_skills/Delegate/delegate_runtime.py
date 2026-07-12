# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Durable Gen2 delegation runtime.
#
# Public entry points used by delegate_skill.py:
#   - delegate_spawn(...)   -- create a structured child task and enqueue it
#   - delegate_status(...)  -- inspect one spawned child task
#   - delegate_collect(...) -- return the recorded child result
#
# This is intentionally queue-native and function-contract driven:
#   task_in, data_in, process, data_out
#
# Related modules:
#   - system_skills/Delegate/delegate_skill.py
#   - scheduler/scheduler.py
#   - orchestration.py
# ====================================================================================================

from __future__ import annotations

import copy
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from datasets import _get_dataset
from datasets import dataset_save
from llm_client import configure_host
from orchestration import OrchestratorConfig
from orchestration import orchestrate_prompt
from scratchpad import scratchpad_load
from scratchpad import scratchpad_save
from session_runtime import bind_session
from session_runtime import get_active_session_id
from system_skills.Delegate.delegate_runner import get_delegate_runtime_tls
from system_skills.FileAccess.file_access_skill import file_write
from tool_selection_state import set_selected_tools
from skills_catalog_builder import load_skills_payload
from utils.runtime_logger import SessionLogger
from utils.runtime_logger import create_log_file_path
from utils.workspace_utils import get_controldata_dir
from utils.workspace_utils import get_logs_dir


_delegate_tls: threading.local = get_delegate_runtime_tls()
_TASK_LOCK: threading.RLock    = threading.RLock()
_STATUS_PENDING:   str = "queued"
_STATUS_RUNNING:   str = "running"
_STATUS_COMPLETED: str = "completed"
_STATUS_FAILED:    str = "failed"
_DEFAULT_SKILLS_CATALOG_PATH = Path(__file__).resolve().parents[2] / "skills" / "skills_catalog.json"


def _utc_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _tasks_dir() -> Path:
    path = get_controldata_dir() / "koreagent" / "delegate_tasks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _task_path(task_id: str) -> Path:
    return _tasks_dir() / f"{task_id}.json"


def _normalize_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        cleaned = str(item or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        items.append(cleaned)
    return items


def _normalize_mapping(value: object) -> dict:
    return dict(value) if isinstance(value, dict) else {}


def _infer_result_target(task_in: str, process_map: dict, data_out_map: dict) -> str:
    explicit_target = str(data_out_map.get("result_target") or "").strip()
    if explicit_target:
        return explicit_target

    target_dataset = str(data_out_map.get("target_dataset") or data_out_map.get("dataset_name") or "").strip()
    if target_dataset:
        return f"dataset:{target_dataset}"

    target_scratchpad = str(data_out_map.get("scratchpad_key") or data_out_map.get("target_scratchpad") or "").strip()
    if target_scratchpad:
        return f"scratchpad:{target_scratchpad}"

    target_file = str(data_out_map.get("target_file") or data_out_map.get("file_path") or data_out_map.get("path") or "").strip()
    if target_file:
        return f"file:{target_file}"

    search_text = "\n".join([
        str(task_in or "").strip(),
        str(process_map.get("instructions") or "").strip(),
        str(data_out_map.get("text") or "").strip(),
    ])

    direct_match = re.search(r"\b(scratchpad|dataset|file):([^\s,;]+)", search_text, flags=re.IGNORECASE)
    if direct_match:
        target_kind  = str(direct_match.group(1) or "").strip().lower()
        target_value = str(direct_match.group(2) or "").strip().rstrip(".)]")
        if target_kind and target_value:
            return f"{target_kind}:{target_value}"

    named_dataset_match = re.search(r"dataset named ['\"]?([A-Za-z0-9_]+)['\"]?", search_text, flags=re.IGNORECASE)
    if named_dataset_match:
        dataset_name = str(named_dataset_match.group(1) or "").strip()
        if dataset_name:
            return f"dataset:{dataset_name}"

    return ""


def _new_task_id() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"dlg_{stamp}_{uuid4().hex[:8]}"


def _write_task_record(record: dict) -> None:
    path = _task_path(str(record["task_id"]))
    tmp  = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_task_record(task_id: str) -> dict | None:
    path = _task_path(task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _update_task_record(task_id: str, **fields) -> dict | None:
    with _TASK_LOCK:
        record = _read_task_record(task_id)
        if record is None:
            return None
        record.update(fields)
        _write_task_record(record)
        return record


def _coerce_task_contract(
    *,
    task_in: str,
    data_in: dict | None,
    process: dict | None,
    data_out: dict | None,
) -> tuple[str, dict, dict, dict]:
    cleaned_task = str(task_in or "").strip()
    if not cleaned_task:
        raise ValueError("task_in cannot be empty.")

    data_in_map  = _normalize_mapping(data_in)
    process_map  = _normalize_mapping(process)
    data_out_map = _normalize_mapping(data_out)

    tools_allowlist = _normalize_string_list(process_map.get("tools_allowlist"))
    if not tools_allowlist:
        raise ValueError("process.tools_allowlist must contain at least one tool name.")

    process_map["tools_allowlist"] = tools_allowlist
    process_map["max_iterations"]  = max(1, min(int(process_map.get("max_iterations") or 3), 12))
    process_map["instructions"]    = str(process_map.get("instructions") or "").strip()
    process_map["constraints"]     = _normalize_string_list(process_map.get("constraints"))

    data_in_map["scratchpad_keys"] = _normalize_string_list(data_in_map.get("scratchpad_keys"))
    data_in_map["datasets"]        = _normalize_string_list(data_in_map.get("datasets"))
    data_in_map["files"]           = _normalize_string_list(data_in_map.get("files"))
    data_in_map["refs"]            = _normalize_string_list(data_in_map.get("refs"))
    inline_text = str(data_in_map.get("text") or "").strip()
    if inline_text:
        data_in_map["text"] = inline_text
    else:
        data_in_map.pop("text", None)

    data_out_map["result_target"] = _infer_result_target(cleaned_task, process_map, data_out_map)
    data_out_map["result_format"] = str(data_out_map.get("result_format") or "").strip()

    return cleaned_task, data_in_map, process_map, data_out_map


def _task_result_stub(record: dict) -> dict:
    return {
        "status":      record.get("status", _STATUS_PENDING),
        "task_id":     record.get("task_id", ""),
        "task_in":     record.get("task_in", ""),
        "created_at":  record.get("created_at", ""),
        "started_at":  record.get("started_at"),
        "finished_at": record.get("finished_at"),
        "result":      record.get("result") or {},
        "result_target": str((record.get("data_out") or {}).get("result_target") or "").strip(),
        "log_path":    record.get("log_path") or "",
        "child_session_id": record.get("child_session_id") or "",
    }


def _build_child_prompt(record: dict) -> str:
    data_in  = _normalize_mapping(record.get("data_in"))
    process  = _normalize_mapping(record.get("process"))
    data_out = _normalize_mapping(record.get("data_out"))

    lines = [
        "You are executing one delegated child task for a controller.",
        "Complete only the assigned child remit. Do not answer the user directly.",
        "Return only the child payload. Do not include plans, reasoning, commentary, or tool narration.",
        "",
        "Task In:",
        str(record.get("task_in") or "").strip(),
    ]

    if process.get("instructions"):
        lines.extend(["", "Additional Instructions:", str(process["instructions"]).strip()])

    lines.extend(["", "Data In:"])
    scratchpad_keys = _normalize_string_list(data_in.get("scratchpad_keys"))
    datasets        = _normalize_string_list(data_in.get("datasets"))
    files           = _normalize_string_list(data_in.get("files"))
    refs            = _normalize_string_list(data_in.get("refs"))
    inline_text     = str(data_in.get("text") or "").strip()

    if scratchpad_keys:
        lines.append(f"- Scratchpad keys copied into this child session: {', '.join(scratchpad_keys)}")
    if datasets:
        lines.append(f"- Datasets copied into this child session: {', '.join(datasets)}")
    if files:
        lines.append(f"- File references: {', '.join(files)}")
    if refs:
        lines.append(f"- Refs: {', '.join(refs)}")
    if inline_text:
        lines.extend(["- Inline text:", inline_text])
    if not any([scratchpad_keys, datasets, files, refs, inline_text]):
        lines.append("- No explicit inputs were provided.")

    lines.extend(["", "Process:"])
    lines.append("- Use only the tools visible to you.")
    lines.append("- The controller runtime will persist the final answer to any configured scratchpad, dataset, or file target.")
    lines.append("- Do not attempt your own save step unless the task explicitly requires a separate side-effect beyond the configured result target.")
    for constraint in _normalize_string_list(process.get("constraints")):
        lines.append(f"- {constraint}")

    result_target = str(data_out.get("result_target") or "").strip()
    result_format = str(data_out.get("result_format") or "").strip()
    lines.extend(["", "Data Out:"])
    if result_target:
        lines.append(f"- Primary result target: {result_target}")
    if result_format:
        lines.append(f"- Expected result format: {result_format}")
    if result_target.startswith("dataset:"):
        lines.append("- Return valid JSON only: either an object or an array of objects suitable for dataset persistence.")
    else:
        lines.append("- Return exactly the requested child result and nothing else.")

    return "\n".join(lines).strip()


def _copy_inputs_to_child(record: dict) -> None:
    data_in            = _normalize_mapping(record.get("data_in"))
    parent_session_id  = str(record.get("parent_session_id") or "").strip()
    child_session_id   = str(record.get("child_session_id") or "").strip()

    for key in _normalize_string_list(data_in.get("scratchpad_keys")):
        value = scratchpad_load(key, session_id=parent_session_id)
        if not value.startswith("Scratchpad key '"):
            scratchpad_save(key, value, session_id=child_session_id)

    for dataset_name in _normalize_string_list(data_in.get("datasets")):
        dataset = _get_dataset(dataset_name, session_id=parent_session_id)
        dataset_save(
            dataset["name"],
            dataset.get("records") or [],
            source_tool = dataset.get("source_tool") or "delegate_parent_copy",
            source_args = dataset.get("source_args") or {"task_id": record["task_id"]},
            replace     = True,
            session_id  = child_session_id,
        )


def _apply_result_target(record: dict, answer: str) -> tuple[list[str], list[str], list[str], str]:
    data_out           = _normalize_mapping(record.get("data_out"))
    parent_session_id  = str(record.get("parent_session_id") or "").strip()
    task_id            = str(record.get("task_id") or "").strip()
    result_target      = str(data_out.get("result_target") or "").strip()
    saved_keys: list[str] = []
    datasets:   list[str] = []
    artifacts:  list[str] = []
    error_text = ""

    if not result_target:
        return saved_keys, datasets, artifacts, error_text

    if result_target.startswith("scratchpad:"):
        key = result_target.split(":", 1)[1].strip()
        if key:
            scratchpad_save(key, answer, session_id=parent_session_id)
            saved_keys.append(key.lower())
        return saved_keys, datasets, artifacts, error_text

    if result_target.startswith("file:"):
        path = result_target.split(":", 1)[1].strip()
        if path:
            result = file_write(path=path, content=answer, skip_content_guard=True)
            if str(result).startswith("Error:"):
                error_text = str(result)
            else:
                artifacts.append(path)
        return saved_keys, datasets, artifacts, error_text

    if result_target.startswith("dataset:"):
        dataset_name = result_target.split(":", 1)[1].strip()
        try:
            parsed = json.loads(answer)
            if isinstance(parsed, dict):
                parsed = [parsed]
            if not isinstance(parsed, list):
                raise ValueError("child output is not a JSON object or array of objects")
            save_result = dataset_save(
                dataset_name,
                parsed,
                source_tool = "delegate",
                source_args = {"task_id": task_id},
                replace     = True,
                session_id  = parent_session_id,
            )
            if str(save_result).startswith("Error:"):
                error_text = str(save_result)
            else:
                datasets.append(dataset_name.lower())
        except Exception as exc:
            error_text = f"Error: could not save dataset target '{dataset_name}': {exc}"
        return saved_keys, datasets, artifacts, error_text

    return saved_keys, datasets, artifacts, f"Error: unsupported result_target '{result_target}'."


def _run_delegate_task(task_id: str) -> None:
    record = _read_task_record(task_id)
    if record is None:
        return

    _update_task_record(task_id, status=_STATUS_RUNNING, started_at=_utc_now())
    record = _read_task_record(task_id) or record

    run_log_path = create_log_file_path(log_dir=get_logs_dir())
    _update_task_record(task_id, log_path=str(run_log_path))

    with SessionLogger(run_log_path) as run_logger:
        try:
            host = str(record.get("host") or "").strip()
            if host:
                configure_host(host)

            skills_payload = load_skills_payload(_DEFAULT_SKILLS_CATALOG_PATH)
            catalog_mtime  = _DEFAULT_SKILLS_CATALOG_PATH.stat().st_mtime if _DEFAULT_SKILLS_CATALOG_PATH.exists() else 0.0
            child_config   = OrchestratorConfig(
                resolved_model      = str(record.get("resolved_model") or "").strip(),
                num_ctx             = int(record.get("num_ctx") or 131072),
                max_iterations      = int((_normalize_mapping(record.get("process")).get("max_iterations")) or 3),
                skills_payload      = skills_payload,
                skills_catalog_path = _DEFAULT_SKILLS_CATALOG_PATH,
                catalog_mtime       = catalog_mtime,
            )

            child_session_id = str(record.get("child_session_id") or "").strip()
            tools_allowlist  = _normalize_string_list(_normalize_mapping(record.get("process")).get("tools_allowlist"))
            set_selected_tools(tools_allowlist, session_id=child_session_id, persist=False)
            _copy_inputs_to_child(record)
            child_prompt = _build_child_prompt(record)

            with bind_session(child_session_id):
                answer, prompt_tokens, completion_tokens, run_success, tps = orchestrate_prompt(
                    user_prompt          = child_prompt,
                    config               = child_config,
                    logger               = run_logger,
                    conversation_history = None,
                    session_context      = None,
                    quiet                = True,
                    delegate_depth       = 0,
                    conversation_entry   = None,
                    scratchpad_visible_keys = None,
                    bound_session_id     = child_session_id,
                )

            saved_keys, datasets, artifacts, output_error = _apply_result_target(record, str(answer or ""))
            final_status = _STATUS_COMPLETED if run_success and not output_error else _STATUS_FAILED
            result_payload = {
                "status":            "ok" if final_status == _STATUS_COMPLETED else "error",
                "summary":           str(answer or ""),
                "evidence":          [],
                "artifacts":         artifacts,
                "saved_keys":        saved_keys,
                "datasets":          datasets,
                "error":             output_error,
                "prompt_tokens":     int(prompt_tokens or 0),
                "completion_tokens": int(completion_tokens or 0),
                "tps":               float(tps or 0.0),
            }
            _update_task_record(
                task_id,
                status      = final_status,
                finished_at = _utc_now(),
                result      = result_payload,
            )
        except Exception as exc:
            _update_task_record(
                task_id,
                status      = _STATUS_FAILED,
                finished_at = _utc_now(),
                result      = {
                    "status":    "error",
                    "summary":   "",
                    "evidence":  [],
                    "artifacts": [],
                    "saved_keys": [],
                    "datasets":  [],
                    "error":     f"Delegate task failed: {exc}",
                },
            )


def delegate_spawn(
    *,
    task_in: str,
    data_in: dict | None = None,
    process: dict | None = None,
    data_out: dict | None = None,
) -> dict:
    """Spawn a durable delegated child task from a structured function-style contract."""
    logger = getattr(_delegate_tls, "logger", None)
    config = getattr(_delegate_tls, "config", None)
    conversation_entry = getattr(_delegate_tls, "conversation_entry", None)
    if logger is None or config is None:
        return {
            "status": "error",
            "error":  "Delegate runtime context is not available.",
        }

    try:
        cleaned_task, normalized_data_in, normalized_process, normalized_data_out = _coerce_task_contract(
            task_in  = task_in,
            data_in  = data_in,
            process  = process,
            data_out = data_out,
        )
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    task_id = _new_task_id()
    parent_session_id = get_active_session_id()
    child_session_id  = f"delegate_task_{task_id}"
    parent_conversation_id = None
    if isinstance(conversation_entry, dict) and conversation_entry.get("id") is not None:
        try:
            parent_conversation_id = int(conversation_entry["id"])
        except Exception:
            parent_conversation_id = None

    record = {
        "task_id":                task_id,
        "status":                 _STATUS_PENDING,
        "task_in":                cleaned_task,
        "data_in":                normalized_data_in,
        "process":                normalized_process,
        "data_out":               normalized_data_out,
        "parent_session_id":      parent_session_id,
        "parent_conversation_id": parent_conversation_id,
        "child_session_id":       child_session_id,
        "resolved_model":         config.resolved_model,
        "num_ctx":                int(config.num_ctx),
        "host":                   normalized_process.get("host_override") or "",
        "created_at":             _utc_now(),
        "started_at":             None,
        "finished_at":            None,
        "log_path":               "",
        "result":                 {},
    }

    with _TASK_LOCK:
        _write_task_record(record)

    from scheduler.scheduler import task_queue

    queue_name = f"delegate_task_{task_id}"
    queued = task_queue.enqueue(
        queue_name,
        "task_run",
        lambda _task_id=task_id: _run_delegate_task(_task_id),
        label = cleaned_task[:64],
        timeout_seconds = None,
    )
    if not queued:
        _update_task_record(
            task_id,
            status      = _STATUS_FAILED,
            finished_at = _utc_now(),
            result      = {"status": "error", "error": "Delegate task could not be queued."},
        )
        return {
            "status": "error",
            "task_id": task_id,
            "error": "Delegate task could not be queued.",
        }

    logger.log_file_only(f"[delegate2] queued task_id={task_id} child_session_id={child_session_id} task={cleaned_task[:120]}")
    return {
        "status":           "queued",
        "task_id":          task_id,
        "child_session_id": child_session_id,
        "result_target":    normalized_data_out.get("result_target") or "",
        "tools_allowlist":  list(normalized_process.get("tools_allowlist") or []),
    }


def delegate_status(task_id: str) -> dict:
    """Return the current state of one delegated child task."""
    cleaned = str(task_id or "").strip()
    if not cleaned:
        return {"status": "error", "error": "task_id cannot be empty."}
    record = _read_task_record(cleaned)
    if record is None:
        return {"status": "error", "error": f"Delegate task '{cleaned}' not found."}
    return _task_result_stub(record)


def delegate_collect(task_id: str) -> dict:
    """Return the recorded result payload for one delegated child task."""
    cleaned = str(task_id or "").strip()
    if not cleaned:
        return {"status": "error", "error": "task_id cannot be empty."}
    record = _read_task_record(cleaned)
    if record is None:
        return {"status": "error", "error": f"Delegate task '{cleaned}' not found."}
    payload = _task_result_stub(record)
    if payload["status"] not in {_STATUS_COMPLETED, _STATUS_FAILED}:
        payload["ready"] = False
        return payload
    payload["ready"] = True
    return payload
