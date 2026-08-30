from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Korechat client helpers for KoreCode/app.
# Provides the focused helpers and module-level behaviour grouped into this file.
# MARK: FUNCTIONS
# Function inventory:
# - _suite_root: Implements the  suite root operation for this module.
# - _read_suite_config: Implements the  read suite config operation for this module.
# - _service_url: Implements the  service url operation for this module.
# - korechat_base_url: Implements the korechat base url operation for this module.
# - _normalize_thread_path: Implements the  normalize thread path operation for this module.
# - _new_conversation_name: Implements the  new conversation name operation for this module.
# - _json_request: Implements the  json request operation for this module.
# - _get_conversation_by_external_id: Implements the  get conversation by external id operation for this module.
# - _create_conversation: Implements the  create conversation operation for this module.
# - _patch_conversation: Implements the  patch conversation operation for this module.
# - _scratchpad_dict: Implements the  scratchpad dict operation for this module.
# - _seed_workspace_menu_scratchpad: Implements the  seed workspace menu scratchpad operation for this module.
# - _remove_workspace_menu_scratchpad: Implements the  remove workspace menu scratchpad operation for this module.
# - _sync_workspace_menu_scratchpad: Implements the  sync workspace menu scratchpad operation for this module.
# - ensure_conversation: Ensures conversation for this module.
# - set_workspace_context_enabled: Sets workspace context enabled for this module.
# - _conversation_detail: Implements the  conversation detail operation for this module.
# - _visible_messages_from_raw: Implements the  visible messages from raw operation for this module.
# - _pending_response: Implements the  pending response operation for this module.
# - get_thread: Returns thread for this module.
# - append_visible_message_for_conversation: Appends visible message for conversation for this module.
# - append_internal_followup: Appends internal followup for this module.
# - delete_thread: Deletes thread for this module.
# ====================================================================================================

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .workspace_menu import MENU_FILENAME
from .workspace_menu import read_workspace_menu


_DEFAULT_HOST         = "127.0.0.1"
_WORKSPACE_THREAD_KEY = "__workspace__"
_INTERNAL_SENDER      = "__korecode_internal__"
_WORKSPACE_MENU_KEY   = "korecode_workspace_menu"
_WORKSPACE_MENU_META  = "korecode_workspace_menu_meta"


def _suite_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_suite_config() -> dict:
    cfg_path = _suite_root() / "config" / "korestack_config.json"
    if not cfg_path.exists():
        return {}
    with cfg_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def _service_url(service_key: str) -> str:
    raw_urls = str(os.environ.get("KORE_SUITE_URLS", "") or "").strip()
    if raw_urls:
        try:
            parsed = json.loads(raw_urls)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            candidate = str(parsed.get(service_key, "") or "").strip().rstrip("/")
            if candidate:
                return candidate

    suite_cfg = _read_suite_config()
    host      = str(suite_cfg.get("network", {}).get("host", _DEFAULT_HOST) or _DEFAULT_HOST).strip() or _DEFAULT_HOST
    port      = suite_cfg.get("services", {}).get(service_key, {}).get("port")
    if port:
        return f"http://{host}:{int(port)}"

    raise RuntimeError(f"Missing services.{service_key}.port in config/korestack_config.json")


def korechat_base_url() -> str:
    # Suite URL maps use /ui for browser navigation; API clients need the service origin.
    return _service_url("korechat").removesuffix("/ui")


def _normalize_thread_path(thread_path: str | None) -> str:
    raw = str(thread_path or "").strip().replace("\\", "/")
    if not raw or raw == ".":
        return _WORKSPACE_THREAD_KEY
    return raw


def _new_conversation_name() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return f"KoreChat_{stamp}"


def _json_request(method: str, url: str, payload: dict | None = None) -> dict | list | None:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8").strip()
            if not raw:
                return None
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"KoreChat HTTP {exc.code} for {method} {url}: {detail[:240]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KoreChat unreachable: {exc.reason}") from exc


def _get_conversation_by_external_id(external_id: str) -> dict | None:
    encoded = urllib.parse.quote(external_id, safe="")
    try:
        payload = _json_request("GET", f"{korechat_base_url()}/api/conversations/by-external-id/{encoded}")
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return None
        raise
    return payload if isinstance(payload, dict) else None


def _create_conversation(external_id: str, subject: str) -> dict:
    payload = _json_request(
        "POST",
        f"{korechat_base_url()}/api/conversations",
        {
            "channel_type":       "webchat",
            "subject":            subject,
            "protected":          True,
            "background_context": "",
            "profile":            "admin",
            "external_id":        external_id,
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("KoreChat create conversation returned no payload")
    return payload


def _patch_conversation(conversation_id: int, payload: dict) -> dict:
    result = _json_request("PATCH", f"{korechat_base_url()}/api/conversations/{conversation_id}", payload)
    if not isinstance(result, dict):
        raise RuntimeError("KoreChat patch conversation returned no payload")
    return result


def _working_data_values(conversation: dict) -> dict:
    working_data = conversation.get("working_data") or {}
    values = working_data.get("values") if isinstance(working_data, dict) else {}
    return values if isinstance(values, dict) else {}


def _seed_workspace_menu_working_data(workspace_root: Path, conversation: dict) -> dict:
    menu_payload = read_workspace_menu(workspace_root)
    if not menu_payload:
        return conversation

    menu_content = str(menu_payload.get("content") or "")
    if not menu_content.strip():
        return conversation

    menu_path = str(menu_payload.get("menu_path") or (workspace_root / MENU_FILENAME))
    menu_hash = hashlib.sha256(menu_content.encode("utf-8")).hexdigest()
    values = _working_data_values(conversation)

    existing_meta = values.get(_WORKSPACE_MENU_META)
    if isinstance(existing_meta, dict) and str(existing_meta.get("content_hash") or "") == menu_hash:
        return conversation

    updated_values = dict(values)
    updated_values[_WORKSPACE_MENU_KEY] = menu_content
    updated_values[_WORKSPACE_MENU_META] = {
        "file_name":    MENU_FILENAME,
        "menu_path":    menu_path,
        "content_hash": menu_hash,
        "chars":        len(menu_content),
    }
    return _patch_conversation(int(conversation["id"]), {"working_data": {"values": updated_values, "collections": {}}})


def _remove_workspace_menu_working_data(conversation: dict) -> dict:
    values = _working_data_values(conversation)
    if _WORKSPACE_MENU_KEY not in values and _WORKSPACE_MENU_META not in values:
        return conversation

    updated_values = dict(values)
    updated_values.pop(_WORKSPACE_MENU_KEY, None)
    updated_values.pop(_WORKSPACE_MENU_META, None)
    return _patch_conversation(int(conversation["id"]), {"working_data": {"values": updated_values, "collections": {}}})


def _sync_workspace_menu_working_data(workspace_root: Path, conversation: dict, enabled: bool) -> dict:
    if enabled:
        return _seed_workspace_menu_working_data(workspace_root, conversation)
    return _remove_workspace_menu_working_data(conversation)


def ensure_conversation(
    workspace_root: Path,
    _thread_path: str,
    conversation_external_id: str | None = None,
    *,
    workspace_context_enabled: bool = True,
) -> dict:
    requested_external_id = str(conversation_external_id or "").strip()
    if requested_external_id:
        conversation = _get_conversation_by_external_id(requested_external_id)
        if conversation is not None:
            return _sync_workspace_menu_working_data(workspace_root, conversation, workspace_context_enabled)
        created = _create_conversation(requested_external_id, requested_external_id)
        return _sync_workspace_menu_working_data(workspace_root, created, workspace_context_enabled)

    conversation_name = _new_conversation_name()
    created = _create_conversation(conversation_name, conversation_name)
    return _sync_workspace_menu_working_data(workspace_root, created, workspace_context_enabled)


def set_workspace_context_enabled(
    workspace_root: Path,
    conversation_external_id: str | None,
    enabled: bool,
) -> dict | None:
    external_id = str(conversation_external_id or "").strip()
    if not external_id:
        return None
    conversation = _get_conversation_by_external_id(external_id)
    if conversation is None:
        return None
    return _sync_workspace_menu_working_data(workspace_root, conversation, enabled)


def _conversation_detail(conversation_id: int) -> dict:
    payload = _json_request("GET", f"{korechat_base_url()}/api/conversations/{conversation_id}/detail")
    if not isinstance(payload, dict):
        raise RuntimeError("KoreChat conversation detail returned no payload")
    return payload


def _visible_messages_from_raw(raw_messages: list[dict]) -> list[dict]:
    visible: list[dict] = []
    for message in raw_messages:
        direction      = str(message.get("direction") or "").strip().lower()
        sender_display = str(message.get("sender_display") or "").strip()
        if direction == "inbound" and sender_display == _INTERNAL_SENDER:
            continue
        role = "assistant" if direction == "outbound" else "user"
        visible.append({
            "id":             message.get("id"),
            "role":           role,
            "text":           str(message.get("content") or ""),
            "sender_display": sender_display,
            "created_at":     message.get("created_at"),
        })
    return visible


def _pending_response(conversation: dict, events: list[dict]) -> bool:
    status = str(conversation.get("status") or "").strip().lower()
    if status in {"waiting_agent", "agent_processing"}:
        return True
    for event in events:
        if str(event.get("event_type") or "").strip() != "response_needed":
            continue
        if str(event.get("status") or "").strip() in {"pending", "claimed"}:
            return True
    return False


def get_thread(
    workspace_root: Path,
    thread_path: str,
    *,
    create: bool = False,
    conversation_external_id: str | None = None,
    workspace_context_enabled: bool = True,
) -> dict:
    normalized  = _normalize_thread_path(thread_path)
    external_id = str(conversation_external_id or "").strip()
    conversation = _get_conversation_by_external_id(external_id) if external_id else None
    if conversation is None:
        if not create:
            return {
                "path":             normalized,
                "title":            external_id or "KoreCode",
                "conversation_id":  None,
                "external_id":      external_id,
                "pending_response": False,
                "messages":         [],
                "raw_messages":     [],
                "last_assistant":   None,
            }
        conversation = ensure_conversation(
            workspace_root,
            normalized,
            conversation_external_id=external_id or None,
            workspace_context_enabled=workspace_context_enabled,
        )
        external_id = str(conversation.get("external_id") or external_id)
    else:
        conversation = _sync_workspace_menu_working_data(workspace_root, conversation, workspace_context_enabled)

    detail       = _conversation_detail(int(conversation["id"]))
    conv_record  = detail.get("conversation") or conversation
    raw_messages = detail.get("messages") or []
    events       = detail.get("events") or []
    last_assistant = next(
        (message for message in reversed(raw_messages) if str(message.get("direction") or "") == "outbound"),
        None,
    )
    return {
        "path":             normalized,
        "title":            str(conv_record.get("subject") or "KoreCode"),
        "conversation_id":  conv_record.get("id"),
        "external_id":      external_id,
        "pending_response": _pending_response(conv_record, events),
        "messages":         _visible_messages_from_raw(raw_messages),
        "raw_messages":     raw_messages,
        "last_assistant":   last_assistant,
    }


def append_visible_message_for_conversation(
    workspace_root: Path,
    thread_path: str,
    visible_text: str,
    prompt_override: str,
    conversation_external_id: str | None = None,
    *,
    workspace_context_enabled: bool = True,
) -> dict:
    conversation = ensure_conversation(
        workspace_root,
        thread_path,
        conversation_external_id=conversation_external_id,
        workspace_context_enabled=workspace_context_enabled,
    )
    payload = {
        "direction":        "inbound",
        "content":          visible_text,
        "sender_display":   "user",
        "status":           "received",
        "response_payload": {
            "prompt_override": prompt_override,
            "visible_text":    visible_text,
        },
    }
    _json_request("POST", f"{korechat_base_url()}/api/conversations/{conversation['id']}/messages", payload)
    return get_thread(
        workspace_root,
        thread_path,
        create=True,
        conversation_external_id=str(conversation.get("external_id") or ""),
        workspace_context_enabled=workspace_context_enabled,
    )


def append_internal_followup(
    workspace_root: Path,
    thread_path: str,
    prompt_text: str,
    visible_text: str = "",
    conversation_external_id: str | None = None,
    outbound_sender_display: str = "agent",
    *,
    workspace_context_enabled: bool = True,
) -> dict:
    conversation = ensure_conversation(
        workspace_root,
        thread_path,
        conversation_external_id=conversation_external_id,
        workspace_context_enabled=workspace_context_enabled,
    )
    payload = {
        "direction":        "inbound",
        "content":          prompt_text,
        "sender_display":   _INTERNAL_SENDER,
        "status":           "received",
        "response_payload": {
            "prompt_override":         prompt_text,
            "visible_text":            visible_text,
            "outbound_sender_display": outbound_sender_display,
        },
    }
    _json_request("POST", f"{korechat_base_url()}/api/conversations/{conversation['id']}/messages", payload)
    return get_thread(
        workspace_root,
        thread_path,
        create=True,
        conversation_external_id=str(conversation.get("external_id") or ""),
        workspace_context_enabled=workspace_context_enabled,
    )


def delete_thread(workspace_root: Path, thread_path: str, conversation_external_id: str | None = None) -> bool:
    _normalized = _normalize_thread_path(thread_path)
    external_id = str(conversation_external_id or "").strip()
    if not external_id:
        return False
    conversation = _get_conversation_by_external_id(external_id)
    if conversation is None:
        return False
    _json_request("DELETE", f"{korechat_base_url()}/api/conversations/{conversation['id']}")
    return True
