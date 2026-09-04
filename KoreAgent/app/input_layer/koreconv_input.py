# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreChat input source for KoreAgent.
#
# Runs as a background polling thread (started by server_startup.py) that calls
# GET /events/next?claimed_by=agent on KoreChat. Each claimed event delivers
# a conversation record with its full message list. The agent builds a prompt from the
# conversation, runs orchestration, then writes the reply back as an outbound message,
# patches conversation state, and marks the event complete.
#
# Conversation lifecycle per run:
#   1. Claim event (GET /events/next) - returns event + full conversation
#   2. Build prompt from background_context + unsummarised messages + scratchpad
#   3. Run orchestrate_prompt
#   4. POST /conversations/{id}/messages  (outbound reply)
#   5. PATCH /conversations/{id}          (updated background_context, scratchpad, token_estimate, turn_count)
#   6. POST /events/{event_id}/complete   {status: "completed"}
#   7. POST /events                       {event_type: "outbound_ready"}  (for KoreComms if needed)
#
# Each conversation maps to a stable session_id "kc_conv_{id}" for orchestration history.
#
# Configuration:
#   "korechaturl" in the runtime config, derived from suite config.
#   If absent, the thread exits immediately with a notice.
#
# Public entry point:
#   start_koreconv_loop(config, push_log_line, task_queue, create_log_file_path,
#                       log_dir, session_logger_cls, shutdown)
#
# Related modules:
#   - api/startup.py     -- calls start_koreconv_loop during Agent startup
#   - execution_queue.py   -- task_queue singleton used for serialisation
#   - orchestration.py     -- orchestrate_prompt, OrchestratorConfig
#   - sessions/session_factory.py -- make_task_session
#   - koreconv_client.py   -- KoreChat URL accessor
# MARK: FUNCTIONS
# Function inventory:
# - _latest_message: Implements the  latest message operation for this module.
# - _event_prompt_label: Implements the  event prompt label operation for this module.
# - _get_base_url: Implements the  get base url operation for this module.
# - _http_get: Implements the  http get operation for this module.
# - _http_post: Implements the  http post operation for this module.
# - _http_patch: Implements the  http patch operation for this module.
# - _complete_event: Implements the  complete event operation for this module.
# - _coerce_conversation_scratchpad: Implements the  coerce conversation scratchpad operation for this module.
# - _coerce_conversation_datasets: Implements the  coerce conversation datasets operation for this module.
# - _build_prompt: Implements the  build prompt operation for this module.
# - _build_conversation_history: Builds bounded historical messages for the current turn.
# - _invalid_model_response_reason: Returns the reason a model response cannot be persisted.
# - _normalise_strict_json_response: Removes model prose from an explicitly JSON-only response.
# - _handle_compress_needed: Implements the  handle compress needed operation for this module.
# - _handle_event: Implements the  handle event operation for this module.
# - start_koreconv_loop: Starts koreconv loop for this module.
# - _loop: Implements the  loop operation for this module.
# - _run_event: Implements the  run event operation for this module.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import re
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

from conversation_state import decode_background_context
from conversation_state import decode_semantic_summary
from conversation_state import encode_background_context
from conversation_state import encode_semantic_summary
from conversation_state import estimate_next_turn_tokens
from agent.orchestration.engine import OrchestratorConfig
from agent.orchestration.engine import orchestrate_prompt
from context_compactor import build_compaction_messages
from context_compactor import compact_summary_text
from context_compactor import select_compaction_batch
from context_compactor import split_messages_for_compaction
from input_layer.slash_processing import process_slash_prompt
from llm_client import call_llm_chat
from sessions.session_factory import make_task_session
from working_data import build_persisted_working_data_payload
from working_data import coerce_persisted_working_data_payload
from working_data import hydrate_working_data
from working_data import working_data_clear
from utils.runtime_logger import SessionLogger
from utils.workspace_utils import load_runtime_config


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_CONFIG_KEY             = "korechaturl"
_DEFAULT_POLL_SECS      = 3
_DEFAULT_TIMEOUT        = 8
_SESSION_PREFIX         = "kc_conv_"
_WEBCHAT_PREFIX         = "webchat_"
_MAX_MODEL_ATTEMPTS     = 2
_LLM_CONTEXT_OMITTED_TAG = "llm_context_omitted"
_KORECODE_INTERNAL_SENDER = "__korecode_internal__"
_COMPACTION_STATUS_TAG  = "[compacting]"
_COMPACTION_THRESHOLD   = 0.80
_PLACEHOLDER_OUTPUT_RE  = re.compile(r"(?:<unused\d+>){4,}", re.IGNORECASE)
_STRICT_JSON_REQUEST_RE = re.compile(
    r"\b(?:just|only)\s+(?:the\s+)?json\b|\bno\s+(?:explanations|preamble|markdown)\b",
    re.IGNORECASE,
)


def _invalid_model_response_reason(response: str) -> str | None:
    """Return the reason an LLM response must not become a conversation turn."""
    content = str(response or "").strip()
    if not content:
        return "empty response"
    if _PLACEHOLDER_OUTPUT_RE.search(content):
        return "placeholder-token output"
    return None


def _normalise_strict_json_response(prompt: str, response: str) -> str:
    """Return JSON alone when the inbound prompt explicitly forbids surrounding prose."""
    if not _STRICT_JSON_REQUEST_RE.search(prompt):
        return response
    match     = re.search(r"```(?:json)?\s*(.*?)\s*```", response, flags=re.IGNORECASE | re.DOTALL)
    candidate = match.group(1) if match else response.strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return response
    if not isinstance(parsed, (dict, list)):
        return response
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _latest_message(messages: list[dict]) -> dict | None:
    """Return the newest message regardless of API ordering or list truncation."""
    if not messages:
        return None
    return max(
        (item for item in messages if isinstance(item, dict)),
        key=lambda item: (str(item.get("created_at") or ""), int(item.get("id") or 0)),
        default=None,
    )


def _is_llm_context_omitted(message: dict) -> bool:
    """Return whether a durable KoreChat message is retained for audit but hidden from LLM input."""
    if str(message.get("sender_display") or "").strip() == _KORECODE_INTERNAL_SENDER:
        return True

    tags = message.get("tags")
    if not isinstance(tags, list):
        metadata = message.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        tags = metadata.get("tags") if isinstance(metadata, dict) else []
    if not isinstance(tags, list):
        return False
    return _LLM_CONTEXT_OMITTED_TAG in {
        str(tag or "").strip().lower()
        for tag in tags
    }


def _event_prompt_label(event: dict) -> str:
    """Return the newest inbound prompt for queue observability."""
    conversation = event.get("conversation") or {}
    messages     = conversation.get("messages") or []
    latest       = next(
        (message for message in reversed(messages) if message.get("direction") == "inbound"),
        None,
    )
    return str((latest or {}).get("content") or "").strip()


# ====================================================================================================
# MARK: CONFIG
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _get_base_url() -> str | None:
    try:
        cfg = load_runtime_config()
        url = cfg.get(_CONFIG_KEY, "").strip().rstrip("/")
        return url if url else None
    except Exception:
        return None


# ====================================================================================================
# MARK: HTTP HELPERS
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _http_get(base: str, path: str, timeout: int = _DEFAULT_TIMEOUT) -> dict | None:
    url = f"{base}{path}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 204:
                return None
            raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        if exc.code == 204:
            return None
        raise RuntimeError(f"KC HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:120]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KC unreachable: {exc.reason}") from exc


# ----------------------------------------------------------------------------------------------------
def _http_post(base: str, path: str, payload: dict, timeout: int = _DEFAULT_TIMEOUT) -> dict | None:
    url  = f"{base}{path}"
    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url,
        data    = body,
        headers = {"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"KC HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:120]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KC unreachable: {exc.reason}") from exc


# ----------------------------------------------------------------------------------------------------
def _http_patch(base: str, path: str, payload: dict, timeout: int = _DEFAULT_TIMEOUT) -> dict | None:
    url  = f"{base}{path}"
    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url,
        data    = body,
        method  = "PATCH",
        headers = {"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"KC HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:120]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KC unreachable: {exc.reason}") from exc


# ----------------------------------------------------------------------------------------------------
def _http_delete(base: str, path: str, timeout: int = _DEFAULT_TIMEOUT) -> None:
    req = urllib.request.Request(f"{base}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return None
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"KC HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:120]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KC unreachable: {exc.reason}") from exc


def _complete_event(base: str, event_id: object, status: str, push_log_line, *, context: str = "") -> bool:
    if not event_id:
        return False
    context_prefix = f"[KORECHAT] {context}: " if context else "[KORECHAT] "
    for attempt in range(1, 4):
        try:
            _http_post(base, f"/events/{event_id}/complete", {"status": status})
            return True
        except Exception as exc:
            push_log_line(f"{context_prefix}Event {event_id} complete({status}) attempt {attempt}/3 failed: {exc}")
    return False


def _queue_compaction_if_needed(
    base:                  str,
    conv_id:               object,
    token_estimate:        int,
    context_window_tokens: int,
    push_log_line,
) -> None:
    """Queue one durable compaction job when conversation context reaches 80%."""
    if context_window_tokens <= 0:
        return
    threshold_tokens = int(context_window_tokens * _COMPACTION_THRESHOLD)
    if token_estimate < threshold_tokens:
        return

    try:
        pending_events = _http_get(
            base,
            f"/events?conversation_id={conv_id}&status=pending&limit=100",
        ) or []
        if any(event.get("event_type") == "compress_needed" for event in pending_events):
            push_log_line(f"[KORECHAT] Conv {conv_id}: context compaction is already queued")
            return
        _http_post(base, "/events", {
            "conversation_id": conv_id,
            "event_type":      "compress_needed",
            "priority":        10,
            "payload": {
                "threshold":     _COMPACTION_THRESHOLD,
                "token_estimate": token_estimate,
                "context_window": context_window_tokens,
            },
        })
        push_log_line(
            f"[KORECHAT] Conv {conv_id}: context at {token_estimate:,}/{context_window_tokens:,} tok "
            f"({_COMPACTION_THRESHOLD:.0%}); compaction queued"
        )
    except Exception as exc:
        # The completed response is already durable. A later turn may queue compaction again.
        push_log_line(f"[KORECHAT] Conv {conv_id}: could not queue context compaction: {exc}")


def _run_manual_compaction(
    base:          str,
    conv:          dict,
    config:        OrchestratorConfig,
    push_log_line,
) -> str:
    """Create and execute a compaction event in the current slash-command job."""
    conv_id = conv.get("id")
    if not conv_id:
        raise RuntimeError("Current conversation has no ID")

    pending_events = _http_get(
        base,
        f"/events?conversation_id={conv_id}&status=pending&limit=100",
    ) or []
    if any(event.get("event_type") == "compress_needed" for event in pending_events):
        return "Context compaction is already queued for this conversation."

    compaction_event = _http_post(base, "/events", {
        "conversation_id": conv_id,
        "event_type":      "compress_needed",
        "priority":        20,
        "payload":         {"requested_by": "slash_command"},
    }) or {}
    event_id = compaction_event.get("id")
    if not event_id:
        raise RuntimeError("KoreChat did not return an event ID")

    _handle_compress_needed(
        {"id": event_id, "conversation": conv},
        config,
        push_log_line,
    )
    return "Context compaction finished. Review the live log for the reduction details."


def _coerce_conversation_working_data(conv: dict, push_log_line=None) -> dict[str, dict]:
    working_data = conv.get("working_data") or {}
    if isinstance(working_data, str):
        try:
            working_data = json.loads(working_data)
        except Exception as exc:
            if push_log_line:
                push_log_line(f"[KORECHAT] Conv {conv.get('id', '?')}: working_data JSON decode failed - prompt built without Working Data: {exc}")
            working_data = {}
    return coerce_persisted_working_data_payload(
        working_data,
    )


# ----------------------------------------------------------------------------------------------------
def _build_prompt(conv: dict, messages: list[dict], push_log_line=None) -> str:
    """Build an LLM user prompt from a KoreChat conversation record and its messages."""
    background        = (conv.get("background_context") or "").strip()
    thread_summary    = (conv.get("thread_summary") or "").strip()
    working_data = _coerce_conversation_working_data(conv, push_log_line=push_log_line)
    values_payload = working_data["values"]
    collections_payload = working_data["collections"]

    # Omitted entries remain visible and auditable in KoreChat, but never become model input.
    visible_messages = [message for message in messages if not _is_llm_context_omitted(message)]

    parts: list[str] = []

    if thread_summary and not background:
        parts.append(f"--- Prior conversation summary ---\n{thread_summary}")

    if values_payload:
        kv = "\n".join(f"  {k}: {v}" for k, v in values_payload.items())
        parts.append(f"--- Working Data values ---\n{kv}")

    if collections_payload:
        lines: list[str] = []
        for dataset_name, manifest in sorted(collections_payload.items()):
            count = int(manifest.get("count", 0))
            schema = manifest.get("schema") or []
            fields = ", ".join(str(field) for field in schema[:5])
            suffix = f" fields=[{fields}]" if fields else ""
            lines.append(f"  {dataset_name}: {count} records{suffix}")
        parts.append("--- Working Data collections ---\n" + "\n".join(lines))

    if visible_messages:
        lines: list[str] = []
        for m in visible_messages:
            direction = m.get("direction", "?")
            sender    = (m.get("sender_display") or "").strip()
            content   = (m.get("content") or "").strip()
            ts        = (m.get("created_at") or "")[:16]
            if direction == "inbound":
                label = f"User ({sender})" if sender else "User"
            else:
                label = "Agent"
            lines.append(f"[{ts}] {label}: {content}")
        parts.append("--- Conversation ---\n" + "\n\n".join(lines))

    # The last inbound message is the one to respond to.
    last_inbound = next(
        (m for m in reversed(visible_messages) if m.get("direction") == "inbound"),
        None,
    )
    if last_inbound:
        content = (last_inbound.get("content") or "").strip()
        parts.append(f"--- Respond to this message ---\n{content}")

    return "\n\n".join(parts)


def _build_conversation_history(
    messages: list[dict],
    *,
    current_inbound_id: object | None,
    max_messages: int = 8,
    max_chars_per_message: int = 2_000,
) -> list[dict[str, str]]:
    """Build a small context-only history that cannot become the current task.

    KoreChat owns the durable transcript.  The agent must route and guard tools
    from the newest inbound message only; earlier messages are supplied solely
    as ordinary chat history for follow-up references.
    """
    prior: list[dict[str, str]] = []
    for message in messages:
        if current_inbound_id is not None and message.get("id") == current_inbound_id:
            break
        if _is_llm_context_omitted(message):
            continue
        if int(message.get("summarised") or 0):
            continue
        direction = str(message.get("direction") or "").strip()
        content = str(message.get("content") or "").strip()
        if direction not in {"inbound", "outbound"} or not content:
            continue
        prior.append(
            {
                "role": "user" if direction == "inbound" else "assistant",
                "content": content[:max_chars_per_message],
            }
        )
    return prior[-max_messages:]


# ====================================================================================================
# MARK: COMPRESSION
# ====================================================================================================

def _handle_compress_needed(
    event:        dict,
    config:       OrchestratorConfig,
    push_log_line,
) -> None:
    base     = _get_base_url()
    event_id = event.get("id")
    conv     = event.get("conversation") or {}
    conv_id  = conv.get("id")

    if not conv_id:
        push_log_line(f"[KORECHAT] compress event {event_id} has no conversation - completing as failed")
        _complete_event(base, event_id, "failed", push_log_line, context="compress")
        return

    # Fetch only the source messages that have not already been represented by a
    # durable summary. They remain stored in KoreChat for audit after compaction.
    try:
        raw = _http_get(base, f"/conversations/{conv_id}/messages?summarised=0&limit=500") or []
    except Exception as exc:
        push_log_line(f"[KORECHAT] Conv {conv_id}: could not fetch messages for compression: {exc}")
        _complete_event(base, event_id, "failed", push_log_line, context=f"conv {conv_id}")
        return

    if not raw:
        push_log_line(f"[KORECHAT] Conv {conv_id}: no unsummarised messages - nothing to compress")
        _complete_event(base, event_id, "completed", push_log_line, context=f"conv {conv_id}")
        return

    context_messages = [message for message in raw if not _is_llm_context_omitted(message)]
    archived_messages, retained_messages = split_messages_for_compaction(context_messages)
    if not archived_messages:
        push_log_line(
            f"[KORECHAT] Conv {conv_id}: retaining the latest three user turns; nothing old enough to compact"
        )
        _complete_event(base, event_id, "completed", push_log_line, context=f"conv {conv_id}")
        return

    compaction_ctx      = min(max(int(config.num_ctx or 0), 4_096), 32_768)
    archived_messages   = select_compaction_batch(
        archived_messages,
        max_source_chars = max(4_000, compaction_ctx * 2),
    )
    if not archived_messages:
        push_log_line(f"[KORECHAT] Conv {conv_id}: oldest message exceeds compaction input budget")
        _complete_event(base, event_id, "completed", push_log_line, context=f"conv {conv_id}")
        return
    previous_summary, _summary_metadata = decode_semantic_summary(conv.get("background_context"))
    input_tok_est = sum(len(str(message.get("content") or "")) for message in archived_messages) // 4
    push_log_line(
        f"{_COMPACTION_STATUS_TAG} Context compacting... Conv {conv_id}: "
        f"summarising {len(archived_messages)} message(s), retaining {len(retained_messages)}"
    )

    try:
        result = call_llm_chat(
            model_name = config.resolved_model,
            messages   = build_compaction_messages(previous_summary, archived_messages),
            tools      = None,
            num_ctx    = compaction_ctx,
        )
        summary = compact_summary_text(result.response)
        if not summary:
            raise RuntimeError("compaction model returned an empty summary")
    except Exception as exc:
        push_log_line(f"{_COMPACTION_STATUS_TAG} Context compacting failed for conv {conv_id}: {exc}")
        _complete_event(base, event_id, "failed", push_log_line, context=f"conv {conv_id}")
        return

    archived_ids = [message.get("id") for message in archived_messages if message.get("id")]
    archived_background = encode_semantic_summary(
        conv.get("background_context"),
        summary,
        {
            "kind":               "semantic_context_summary",
            "source_message_ids": archived_ids,
            "retained_user_turns": 3,
        },
    )
    archived_tokens = len(archived_background) // 4
    try:
        _http_patch(base, f"/conversations/{conv_id}", {
            "background_context": archived_background,
            "token_estimate":     archived_tokens,
        })
    except Exception as exc:
        push_log_line(f"[KORECHAT] Conv {conv_id}: failed to patch archived background context: {exc}")
        _complete_event(base, event_id, "failed", push_log_line, context=f"conv {conv_id}")
        return

    # Mark messages as summarised only after durable archived context has been stored.
    for msg_id in archived_ids:
        try:
            _http_patch(base, f"/messages/{msg_id}", {"summarised": 1})
        except Exception as exc:
            push_log_line(f"[KORECHAT] Conv {conv_id}: could not mark message {msg_id} summarised: {exc}")

    reduction_pct = int(100 * (1 - len(summary) / max(1, input_tok_est * 4))) if input_tok_est > 0 else 0
    push_log_line(
        f"{_COMPACTION_STATUS_TAG} Context compacting complete: Conv {conv_id} archived "
        f"{len(archived_ids)} message(s), ~{input_tok_est:,} tok -> ~{archived_tokens:,} tok "
        f"({reduction_pct}% reduction)"
    )

    _complete_event(base, event_id, "completed", push_log_line, context=f"conv {conv_id}")


# ====================================================================================================
# MARK: EVENT HANDLER
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _handle_event(
    event:               dict,
    config:              OrchestratorConfig,
    log_dir:             Path,
    session_logger_cls,
    create_log_file_path,
    set_latest_log_path,
    push_log_line,
) -> None:
    """Dispatch one KoreChat event to the appropriate handler."""
    started_at = time.monotonic()
    base    = _get_base_url()
    if not base:
        return

    event_id   = event.get("id")
    event_type = (event.get("event_type") or "").strip()
    conv       = event.get("conversation") or {}
    conv_id    = conv.get("id")
    raw_payload = event.get("payload")
    if isinstance(raw_payload, str):
        try:
            event_payload = json.loads(raw_payload) if raw_payload.strip() else {}
        except json.JSONDecodeError:
            event_payload = {}
    elif isinstance(raw_payload, dict):
        event_payload = raw_payload
    else:
        event_payload = {}

    if event_type == "compress_needed":
        _handle_compress_needed(event, config, push_log_line)
        return

    if event_type != "response_needed":
        push_log_line(f"[KORECHAT] Skipping unsupported event {event_id} ({event_type or 'unknown'})")
        _complete_event(base, event_id, "completed", push_log_line, context="skip")
        return

    if not conv_id:
        push_log_line(f"[KORECHAT] Event {event_id} has no conversation - completing as failed")
        _complete_event(base, event_id, "failed", push_log_line, context="response_needed")
        return

    external_id     = str(conv.get("external_id") or "").strip()
    session_id      = f"{_SESSION_PREFIX}{conv_id}"
    chat_session_id = external_id[len(_WEBCHAT_PREFIX):] if external_id.startswith(_WEBCHAT_PREFIX) else session_id
    turn_count = conv.get("turn_count", 0)
    push_log_line(f"[KORECHAT] Handling event {event_id} (conv {conv_id}, turn {turn_count + 1})")

    run_log_path = create_log_file_path(log_dir=log_dir)
    set_latest_log_path(run_log_path)
    push_log_line(f"[KORECHAT] Conv {conv_id}: live run log {run_log_path.name}")
    with session_logger_cls(run_log_path) as run_logger:

        # The event payload already includes unsummarised messages (from conversation_get_with_messages).
        # Use those directly; fall back to a separate HTTP call if the field is absent.
        messages = conv.get("messages")
        if messages is None:
            try:
                messages = _http_get(base, f"/conversations/{conv_id}/messages?limit=500") or []
            except Exception as exc:
                push_log_line(f"[KORECHAT] Conv {conv_id}: could not fetch messages: {exc}")
                messages = []

        # Guard against duplicate processing: if the most recent message is already outbound,
        # the web API path already handled this turn (via _kc_save_turn). Mark the event
        # complete and skip orchestration to avoid running the same prompt twice.
        # Fetch fresh messages here (rather than trusting the event payload snapshot) because
        # _kc_save_turn posts the outbound asynchronously - the payload may be stale.
        try:
            fresh_messages = _http_get(base, f"/conversations/{conv_id}/messages?limit=500") or []
        except Exception:
            fresh_messages = messages
        latest_message = _latest_message(fresh_messages)
        if latest_message and (latest_message.get("direction") or "") == "outbound":
            push_log_line(f"[KORECHAT] Conv {conv_id}: event {event_id} skipped - turn already answered by web API path")
            _complete_event(base, event_id, "completed", push_log_line, context=f"conv {conv_id}")
            return

        # Restore persisted scratchpad state into the active session before orchestration
        # so scratchpad tool calls operate on the KC-backed conversation state.
        hydrate_working_data(
            conv.get("working_data") or {},
            session_id,
            legacy_values=conv.get("scratchpad") or {},
            legacy_collections=conv.get("datasets") or {},
            warning_logger=lambda message: push_log_line(f"[KORECHAT] Conv {conv_id}: {message}"),
        )

        messages = fresh_messages
        latest_inbound = next(
            (message for message in reversed(messages) if message.get("direction") == "inbound"),
            None,
        )
        inbound_prompt = str((latest_inbound or {}).get("content") or "").strip()
        user_prompt = str(event_payload.get("prompt_override") or "").strip() or inbound_prompt
        if not user_prompt:
            user_prompt = _build_prompt(conv, messages, push_log_line=push_log_line)
        conversation_history = _build_conversation_history(
            messages,
            current_inbound_id=(latest_inbound or {}).get("id"),
        )

        # KC owns the persisted conversation state. The agent keeps only transient
        # per-run session context in memory for this turn.
        _, session_ctx = make_task_session(
            session_id   = session_id,
            persist_path = None,
            max_turns    = 10,
        )

        if inbound_prompt.startswith("/"):
            inbound_id = (latest_inbound or {}).get("id")
            if inbound_id:
                _http_patch(base, f"/messages/{inbound_id}", {"tags": ["slashcommand"]})

            history_cleared = False

            def _clear_korechat_history() -> None:
                nonlocal history_cleared
                session_ctx.clear()
                working_data_clear(session_id)
                _http_delete(base, f"/conversations/{conv_id}/history")
                history_cleared = True

            def _request_korechat_compaction() -> str:
                return _run_manual_compaction(base, conv, config, push_log_line)

            requested_session_switch: dict[str, str] | None = None

            def _switch_session(new_session_id: str, name: str) -> None:
                nonlocal requested_session_switch
                requested_session_switch = {"session_id": new_session_id, "name": name}

            slash_response = process_slash_prompt(
                inbound_prompt,
                config          = config,
                output          = lambda text, _level="info": push_log_line(f"[slash] {text}"),
                clear_history   = _clear_korechat_history,
                session_context = session_ctx,
                session_id      = chat_session_id,
                chat_name       = str(conv.get("external_id") or "").strip() or None,
                switch_session  = _switch_session,
                compress_history = _request_korechat_compaction,
            )
            persisted_working_data = build_persisted_working_data_payload(session_id)
            try:
                _http_post(base, f"/conversations/{conv_id}/messages", {
                    "direction":         "outbound",
                    "content":           slash_response,
                    "sender_display":    str(event_payload.get("outbound_sender_display") or "agent"),
                    "status":            "sent",
                    "delivery_eligible": False,
                    "metadata": {
                        "telemetry": {
                            "context_tokens": 0,
                            "tokens_per_second": "0",
                            "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                        },
                        "session_switch": requested_session_switch,
                    },
                    "tags":              ["slashcommand_response"],
                })
                _http_patch(base, f"/conversations/{conv_id}", {
                    "status":     "active",
                    "turn_count": 0 if history_cleared else turn_count + 1,
                    "working_data": persisted_working_data,
                })
            except Exception as exc:
                push_log_line(f"[KORECHAT] Conv {conv_id}: failed to persist slash response: {exc}")
                _complete_event(base, event_id, "failed", push_log_line, context=f"conv {conv_id}")
                return
            _complete_event(base, event_id, "completed", push_log_line, context=f"conv {conv_id}")
            return

        # Restore only durable semantic summaries. Tool results remain available while a
        # turn is running, but must never be carried into a later prompt.
        background_ctx = (conv.get("background_context") or "").strip()
        if background_ctx:
            restored_turns, background_warning = decode_background_context(background_ctx)
            if restored_turns:
                with session_ctx._lock:
                    if not session_ctx._turns:
                        session_ctx._turns = restored_turns
                push_log_line(f"[KORECHAT] Conv {conv_id}: restored {len(restored_turns)} turn(s) from background_context")
            if background_warning:
                push_log_line(f"[KORECHAT] Conv {conv_id}: {background_warning}")

        # Item 5: Compute token pressure from the stored estimate vs the model's context window.
        # This is passed to orchestrate_prompt so build_system_message can warn the model when
        # the context window is getting full.
        stored_token_estimate = conv.get("token_estimate") or 0
        token_pressure = (stored_token_estimate / config.num_ctx) if config.num_ctx > 0 else 0.0

        invalid_response_reason = None
        for attempt in range(1, _MAX_MODEL_ATTEMPTS + 1):
            response, prompt_tokens, completion_tokens, ok, tps = orchestrate_prompt(
                user_prompt          = user_prompt,
                config               = config,
                logger               = run_logger,
                conversation_history = conversation_history or None,
                session_context      = session_ctx,
                quiet                = True,
                conversation_entry   = conv,
                token_pressure       = token_pressure,
            )
            reply                   = response.strip()
            normalised_reply        = _normalise_strict_json_response(inbound_prompt, reply)
            if normalised_reply != reply:
                push_log_line(f"[KORECHAT] Conv {conv_id}: removed prose around an explicitly JSON-only response")
                reply = normalised_reply
            invalid_response_reason = _invalid_model_response_reason(reply)
            tps_str                 = f"{tps:.1f}" if tps > 0 else "0"
            push_log_line(
                f"[KORECHAT] Conv {conv_id}: attempt {attempt}/{_MAX_MODEL_ATTEMPTS} "
                f"[{prompt_tokens:,} tok, {tps_str} tok/s, ok={ok}]"
            )
            if invalid_response_reason is None:
                break
            if attempt < _MAX_MODEL_ATTEMPTS:
                push_log_line(
                    f"[KORECHAT] Conv {conv_id}: rejected {invalid_response_reason}; retrying the shared agent path"
                )

        invalid_model_output = invalid_response_reason is not None
        if invalid_model_output:
            reply = "(Agent response unavailable: the local model returned an invalid response. Please retry.)"
            push_log_line(f"[KORECHAT] Conv {conv_id}: persisted controlled agent-error response after retry.")

        current_scratchpad = build_persisted_working_data_payload(session_id)["values"]
        persisted_working_data = {
            "values": current_scratchpad,
            "collections": build_persisted_working_data_payload(session_id)["collections"],
        }

        # Replace legacy tool-output context with the cleaned session context. Semantic
        # summaries remain preserved by encode_background_context().
        sc_turns = session_ctx.get_turns()
        new_background_context = encode_background_context(
            sc_turns,
            background_ctx,
            replace=True,
        )

        # token_estimate reflects what the next turn will start from: prompt consumed
        # this turn plus the completion tokens (which become part of the thread next turn).
        new_token_estimate = estimate_next_turn_tokens(prompt_tokens, completion_tokens)

        # External replies remain drafts until KoreComms has delivered them.
        channel = conv.get("channel_type", "webchat")
        outbound_status = "sent" if channel in {"webchat", "manual"} else "draft"
        delivery_eligible = not user_prompt.lstrip().startswith("/")
        outbound_tags     = ["agent_error", "invalid_model_output"] if invalid_model_output else []

        # Write outbound message first - if this fails the event is not completed.
        try:
            _http_post(base, f"/conversations/{conv_id}/messages", {
                "direction":      "outbound",
                "content":        reply,
                "sender_display": str(event_payload.get("outbound_sender_display") or "agent"),
                "status":         outbound_status,
                "delivery_eligible": delivery_eligible,
                "metadata": {
                        "telemetry": {
                            "context_tokens": prompt_tokens,
                            "context_window": config.num_ctx,
                            "completion_tokens": completion_tokens,
                        "tokens_per_second": tps_str,
                        "elapsed_ms": int((time.monotonic() - started_at) * 1000),
                    },
                },
                "tags":           outbound_tags,
            })
        except Exception as exc:
            push_log_line(f"[KORECHAT] Conv {conv_id}: failed to write outbound message: {exc}")
            _complete_event(base, event_id, "failed", push_log_line, context=f"conv {conv_id}")
            return

        # Patch conversation metadata including scratchpad.
        # This is the durable write - we log failures loudly but still complete the event
        # so the conversation does not stay in agent_processing indefinitely.
        try:
            _http_patch(base, f"/conversations/{conv_id}", {
                "status":             "active",
                "token_estimate":     new_token_estimate,
                "turn_count":         turn_count + 1,
                "working_data":       persisted_working_data,
                "background_context": new_background_context,
            })
        except Exception as exc:
            push_log_line(
                f"[KORECHAT] Conv {conv_id}: WARN - conversation patch failed (scratchpad may be stale): {exc}"
            )
            _complete_event(base, event_id, "failed", push_log_line, context=f"conv {conv_id}")
            return

        _queue_compaction_if_needed(
            base,
            conv_id,
            new_token_estimate,
            config.num_ctx,
            push_log_line,
        )

        # Complete the event.
        _complete_event(base, event_id, "completed", push_log_line, context=f"conv {conv_id}")

        # Raise outbound_ready so KoreChat can signal KoreComms for non-webchat delivery.
        if channel not in {"webchat", "manual"}:
            try:
                _http_post(base, "/events", {
                    "conversation_id": conv_id,
                    "event_type":      "outbound_ready",
                    "priority":        0,
                    "payload":         {},
                })
            except Exception as exc:
                push_log_line(f"[KORECHAT] Conv {conv_id}: outbound_ready event failed: {exc}")

# ====================================================================================================
# MARK: BACKGROUND LOOP
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def start_koreconv_loop(
    config:              OrchestratorConfig,
    push_log_line,
    task_queue,
    create_log_file_path,
    set_latest_log_path,
    log_dir:             Path,
    session_logger_cls,
    shutdown:            threading.Event,
) -> threading.Thread:
    """Start the background KoreChat polling thread and return it.

    Polls GET /events/next?claimed_by=agent every _DEFAULT_POLL_SECS seconds.
    Each claimed event is enqueued into task_queue so LLM work runs serially.
    If korechaturl is not configured, the thread exits immediately.
    """
    def _loop() -> None:
        base = _get_base_url()
        if not base:
            push_log_line("[KORECHAT] korechaturl not configured - KoreChat integration disabled.")
            return

        push_log_line(f"[KORECHAT] Polling {base} every {_DEFAULT_POLL_SECS}s")

        while not shutdown.is_set():
            try:
                event = _http_get(base, "/events/next?claimed_by=agent")
                if event is not None:
                    event_id  = event.get("id")
                    conv_id   = (event.get("conversation") or {}).get("id", "?")
                    task_name = f"kc_event_{event_id}"

                    def _run_event(_ev=event) -> None:
                        try:
                            _handle_event(
                                event                = _ev,
                                config               = config,
                                log_dir              = log_dir,
                                session_logger_cls   = session_logger_cls,
                                create_log_file_path = create_log_file_path,
                                set_latest_log_path  = set_latest_log_path,
                                push_log_line        = push_log_line,
                            )
                        except Exception as exc:
                            failed_event_id = _ev.get("id")
                            failed_conv_id  = (_ev.get("conversation") or {}).get("id")
                            push_log_line(
                                f"[KORECHAT] Event {failed_event_id} crashed: {type(exc).__name__}: {exc}\n"
                                f"{traceback.format_exc()}"
                            )
                            base_url = _get_base_url()
                            if base_url and failed_conv_id:
                                try:
                                    _http_post(base_url, f"/conversations/{failed_conv_id}/messages", {
                                        "direction":      "outbound",
                                        "content":        "(Agent response unavailable due to an internal error. Please retry.)",
                                        "sender_display": "agent",
                                        "status":         "sent",
                                        "delivery_eligible": False,
                                        "tags":           ["agent_error"],
                                    })
                                    _http_patch(base_url, f"/conversations/{failed_conv_id}", {"status": "active"})
                                except Exception as recovery_exc:
                                    push_log_line(
                                        f"[KORECHAT] Event {failed_event_id} recovery failed: {recovery_exc}"
                                    )
                            if base_url and failed_event_id:
                                _complete_event(
                                    base_url,
                                    failed_event_id,
                                    "failed",
                                    push_log_line,
                                    context=f"event {failed_event_id}",
                                )

                    prompt_label = _event_prompt_label(event)
                    queued = task_queue.enqueue(
                        task_name,
                        "koreconv",
                        _run_event,
                        label    = prompt_label or f"KoreChat event {event_id}",
                        metadata = {
                            "conversation_id": conv_id,
                            "event_id":        event_id,
                            "source":          "KoreChat",
                        },
                    )
                    if queued:
                        push_log_line(f"[KORECHAT] Event {event_id} (conv {conv_id}) queued as '{task_name}'")
                    else:
                        push_log_line(f"[KORECHAT] Event {event_id} (conv {conv_id}) already in task queue - skipping")

            except Exception as exc:
                push_log_line(f"[KORECHAT] Poll error: {exc}")

            # Short-burst sleep so shutdown is responsive.
            for _ in range(_DEFAULT_POLL_SECS * 2):
                if shutdown.is_set():
                    break
                time.sleep(0.5)

    thread = threading.Thread(target=_loop, daemon=True, name="koreconv-poller")
    thread.start()
    return thread
