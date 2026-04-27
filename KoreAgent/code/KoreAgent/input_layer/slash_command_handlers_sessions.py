import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

import KoreAgent.koreconv_client as koreconv_client
from KoreAgent.input_layer.slash_command_context import SlashCommandContext


_KC_TIMEOUT = 8
_WEBCHAT_PREFIX = "webchat_"


def _kc_get(path: str) -> dict | list | None:
    base = koreconv_client.get_base_url()
    if not base:
        raise RuntimeError("KoreConversation is not configured")
    req = urllib.request.Request(f"{base}{path}", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_KC_TIMEOUT) as resp:
            if resp.status == 204:
                return None
            raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"KoreConversation HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KoreConversation unreachable: {exc.reason}") from exc


def _kc_post(path: str, payload: dict) -> dict | None:
    base = koreconv_client.get_base_url()
    if not base:
        raise RuntimeError("KoreConversation is not configured")
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_KC_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"KoreConversation HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KoreConversation unreachable: {exc.reason}") from exc


def _kc_patch(path: str, payload: dict) -> dict | None:
    base = koreconv_client.get_base_url()
    if not base:
        raise RuntimeError("KoreConversation is not configured")
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="PATCH",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_KC_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"KoreConversation HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KoreConversation unreachable: {exc.reason}") from exc


def _kc_delete(path: str) -> None:
    base = koreconv_client.get_base_url()
    if not base:
        raise RuntimeError("KoreConversation is not configured")
    req = urllib.request.Request(f"{base}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=_KC_TIMEOUT):
            return None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"KoreConversation HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KoreConversation unreachable: {exc.reason}") from exc


def _external_id_for_session(session_id: str) -> str:
    return f"{_WEBCHAT_PREFIX}{session_id}"


def _session_id_from_external_id(external_id: str) -> str:
    if external_id.startswith(_WEBCHAT_PREFIX):
        return external_id[len(_WEBCHAT_PREFIX):]
    return external_id


def _display_name(conv: dict) -> str:
    subject = (conv.get("subject") or "").strip()
    if subject:
        return subject
    external_id = str(conv.get("external_id") or "")
    if external_id.startswith(_WEBCHAT_PREFIX):
        return _session_id_from_external_id(external_id)
    return f"conversation_{conv.get('id', '?')}"


def _list_all_conversations() -> list[dict]:
    result = _kc_get("/conversations?limit=500") or []
    if not isinstance(result, list):
        return []
    conversations = list(result)
    conversations.sort(key=lambda item: item.get("last_activity_at") or "", reverse=True)
    return conversations


def _list_webchat_conversations() -> list[dict]:
    result = _kc_get("/conversations?channel_type=webchat&limit=500") or []
    if not isinstance(result, list):
        return []
    conversations = list(result)
    conversations.sort(key=lambda item: item.get("last_activity_at") or "", reverse=True)
    return conversations


def _find_conversation_by_session(session_id: str) -> dict | None:
    external_id = urllib.parse.quote(_external_id_for_session(session_id), safe="")
    result = _kc_get(f"/conversations/by-external-id/{external_id}")
    return result if isinstance(result, dict) else None


def _ensure_conversation_for_session(session_id: str) -> dict:
    existing = _find_conversation_by_session(session_id)
    if existing is not None:
        return existing
    created = _kc_post(
        "/conversations",
        {
            "channel_type": "webchat",
            "subject": f"Webchat {session_id}",
            "external_id": _external_id_for_session(session_id),
        },
    )
    if not created:
        raise RuntimeError("Failed to create KoreConversation conversation")
    return created


def _find_conversation_by_name(name: str) -> dict | None:
    target = name.strip().lower()
    if not target:
        return None
    conversations = _list_webchat_conversations()
    exact = next((conv for conv in conversations if _display_name(conv).lower() == target), None)
    if exact is not None:
        return exact
    return next((conv for conv in conversations if target in _display_name(conv).lower()), None)


def _clone_conversation(source: dict, new_name: str, session_id: str) -> dict:
    payload = {
        "channel_type": source.get("channel_type") or "webchat",
        "subject": new_name,
        "background_context": source.get("background_context") or "",
        "profile": source.get("profile") or None,
        "external_id": _external_id_for_session(session_id),
    }
    created = _kc_post("/conversations", payload)
    if not created or not created.get("id"):
        raise RuntimeError("Failed to create KoreConversation copy")

    source_messages = _kc_get(f"/conversations/{source['id']}/messages?limit=1000") or []
    if not isinstance(source_messages, list):
        source_messages = []

    for message in source_messages:
        _kc_post(
            f"/conversations/{created['id']}/messages",
            {
                "direction": message.get("direction") or "inbound",
                "content": message.get("content") or "",
                "sender_display": message.get("sender_display") or "",
                "status": message.get("status") or "received",
            },
        )

    _kc_patch(
        f"/conversations/{created['id']}",
        {
            "thread_summary": source.get("thread_summary") or "",
            "scratchpad": source.get("scratchpad") or {},
            "token_estimate": source.get("token_estimate") or 0,
            "turn_count": source.get("turn_count") or 0,
            "status": "active",
        },
    )
    return _kc_get(f"/conversations/{created['id']}") or created


def _cmd_session(arg: str, ctx: SlashCommandContext) -> None:
    sub_parts = arg.strip().split(None, 1)
    sub = sub_parts[0].lower() if sub_parts else ""
    rest = sub_parts[1].strip() if len(sub_parts) > 1 else ""

    try:
        if sub == "new":
            new_session_id = f"web_{int(time.time() * 1000)}"
            name = rest.strip()
            if ctx.switch_session:
                ctx.switch_session(new_session_id, name)
                label = f"'{name}'" if name else "a new chat"
                ctx.output(f"Conversation history cleared - starting {label}.", "success")
            else:
                ctx.output("Session switching is not available in this mode.", "error")
            return

        if sub == "name":
            if not rest:
                ctx.output("Usage: /session name <alias>", "dim")
                return
            if not ctx.session_id:
                ctx.output("Session naming is not available in this mode.", "error")
                return
            conv = _ensure_conversation_for_session(ctx.session_id)
            updated = _kc_patch(f"/conversations/{conv['id']}", {"subject": rest}) or conv
            ctx.output(f"Conversation named '{rest}'.", "success")
            if ctx.rename_session:
                ctx.rename_session(ctx.session_id, updated.get("subject") or rest)
            return

        if sub == "list":
            conversations = _list_all_conversations()
            if not conversations:
                ctx.output("No KoreConversation conversations found.", "dim")
                return
            ctx.output(f"{len(conversations)} conversation(s):", "info")
            for conv in conversations:
                label   = _display_name(conv)
                turns   = conv.get("turn_count", 0)
                status  = conv.get("status", "-")
                channel = conv.get("channel_type", "-")
                ctx.output(f"  {label:<30}  {turns} turn(s)  [{status}]  [{channel}]", "item")
            return

        if sub == "resume":
            if not rest:
                ctx.output("Usage: /session resume <name>", "dim")
                return
            if not ctx.switch_session:
                ctx.output("Session switching is not available in this mode.", "error")
                return
            conv = _find_conversation_by_name(rest)
            if conv is None:
                ctx.output(f"No conversation named '{rest}' found.", "error")
                return
            session_id = _session_id_from_external_id(str(conv.get("external_id") or ""))
            label = _display_name(conv)
            ctx.output(f"Switching to '{label}'.", "success")
            ctx.switch_session(session_id, label)
            return

        if sub == "resumecopy":
            parts2 = rest.split(None, 1)
            if len(parts2) < 2:
                ctx.output("Usage: /session resumecopy <oldname> <newname>", "dim")
                return
            if not ctx.switch_session:
                ctx.output("Session switching is not available in this mode.", "error")
                return
            source = _find_conversation_by_name(parts2[0].strip())
            if source is None:
                ctx.output(f"No conversation named '{parts2[0].strip()}' found.", "error")
                return
            new_name = parts2[1].strip()
            new_session_id = f"web_{int(time.time() * 1000)}"
            copied = _clone_conversation(source, new_name, new_session_id)
            ctx.output(f"Copied '{_display_name(source)}' -> '{_display_name(copied)}'.", "success")
            ctx.switch_session(new_session_id, _display_name(copied))
            return

        if sub == "park":
            if not ctx.switch_session:
                ctx.output("Session parking is not available in this mode.", "error")
                return
            new_id = f"web_{int(time.time() * 1000)}"
            ctx.output("Current conversation parked - starting fresh chat.", "success")
            ctx.switch_session(new_id, "")
            return

        if sub == "delete":
            if not rest:
                ctx.output("Usage: /session delete <name>  |  /session delete all", "dim")
                return
            conversations = _list_webchat_conversations()
            if rest.lower() == "all":
                if not conversations:
                    ctx.output("No conversations to delete.", "dim")
                    return
                deleted_current = False
                for conv in conversations:
                    session_id = _session_id_from_external_id(str(conv.get("external_id") or ""))
                    if session_id == ctx.session_id:
                        deleted_current = True
                    if ctx.delete_session_state:
                        ctx.delete_session_state(session_id)
                    else:
                        _kc_delete(f"/conversations/{conv['id']}")
                    ctx.output(f"  Deleted '{_display_name(conv)}'.", "item")
                ctx.output(f"{len(conversations)} conversation(s) deleted.", "success")
                if deleted_current and ctx.switch_session:
                    ctx.switch_session(f"web_{int(time.time() * 1000)}", "")
                return
            conv = _find_conversation_by_name(rest)
            if conv is None:
                ctx.output(f"No conversation named '{rest}' found.", "error")
                return
            session_id = _session_id_from_external_id(str(conv.get("external_id") or ""))
            if ctx.delete_session_state:
                ctx.delete_session_state(session_id)
            else:
                _kc_delete(f"/conversations/{conv['id']}")
            ctx.output(f"Deleted conversation '{_display_name(conv)}'.", "success")
            if session_id == ctx.session_id and ctx.switch_session:
                ctx.switch_session(f"web_{int(time.time() * 1000)}", "")
            return

        if sub == "info":
            if not ctx.session_id:
                ctx.output("No active session.", "dim")
                return
            conv = _find_conversation_by_session(ctx.session_id)
            if conv is None:
                ctx.output(f"Session '{ctx.session_id}' has no KoreConversation yet.", "dim")
                return
            ctx.output("Current conversation:", "info")
            ctx.output(f"  Name:       {_display_name(conv)}", "item")
            ctx.output(f"  Session ID: {ctx.session_id}", "item")
            ctx.output(f"  Conv ID:    {conv.get('id')}", "item")
            ctx.output(f"  Status:     {conv.get('status', '-')}", "item")
            ctx.output(f"  Turns:      {conv.get('turn_count', 0)}", "item")
            ctx.output(f"  Tokens:     {conv.get('token_estimate', 0)}", "item")
            return

    except RuntimeError as exc:
        ctx.output(str(exc), "error")
        return

    ctx.output("Usage: /session <new|name|list|resume|resumecopy|park|delete|info>", "dim")
    ctx.output("  /session new [name]                  - clear history and start a fresh chat (optional name)", "item")
    ctx.output("  /session name <alias>                - rename the current KoreConversation", "item")
    ctx.output("  /session list                        - list webchat KoreConversations", "item")
    ctx.output("  /session resume <name>               - switch to a named KoreConversation", "item")
    ctx.output("  /session resumecopy <old> <new>      - copy a KoreConversation and resume the copy", "item")
    ctx.output("  /session park                        - start a fresh webchat session", "item")
    ctx.output("  /session delete <name>               - delete a KoreConversation by name", "item")
    ctx.output("  /session delete all                  - delete all webchat KoreConversations", "item")
    ctx.output("  /session info                        - show current KoreConversation details", "item")


# ----------------------------------------------------------------------------------------------------
def _cmd_kccompress(arg: str, ctx: SlashCommandContext) -> None:
    # /kccompress          -> queue a compress_needed event for the current conversation
    # /kccompress <name>   -> queue for a named conversation
    try:
        if arg.strip():
            conv = _find_conversation_by_name(arg.strip())
            if conv is None:
                ctx.output(f"No conversation named '{arg.strip()}' found.", "error")
                return
        else:
            if not ctx.session_id:
                ctx.output("No active session.", "error")
                return
            conv = _find_conversation_by_session(ctx.session_id)
            if conv is None:
                ctx.output("No KoreConversation found for the current session.", "error")
                return

        conv_id = conv.get("id")
        unsummarised = _kc_get(f"/conversations/{conv_id}/messages?summarised=0&limit=1") or []
        if not unsummarised:
            ctx.output("No unsummarised messages - nothing to compress.", "dim")
            return

        _kc_post("/events", {
            "conversation_id": conv_id,
            "event_type":      "compress_needed",
            "priority":        10,
            "payload":         {},
        })
        ctx.output(
            f"compress_needed event queued for '{_display_name(conv)}' (conv {conv_id}).",
            "success",
        )
        ctx.output("Check the log panel - compression will run on the next agent poll.", "dim")
    except RuntimeError as exc:
        # If KoreConversation is unavailable, fall back to the in-memory compression path
        # when the caller has wired one up (e.g. CLI chat-sequence test mode).
        if ctx.compress_history is not None:
            result = ctx.compress_history()
            ctx.output(result, "success")
        else:
            ctx.output(f"Cannot compress conversation: {exc}", "error")


def register_session_slash_commands(registry: dict[str, Callable], descriptions: dict[str, str]) -> None:
    registry["/session"]    = _cmd_session
    registry["/kccompress"] = _cmd_kccompress
    descriptions["/session"]    = "new [name] | name <alias> | list | resume <name> | park | delete <name|all> | info  - manage sessions and KoreConversation"
    descriptions["/kccompress"] = "[<name>]  Queue a compress_needed event for the current (or named) KoreConversation"
