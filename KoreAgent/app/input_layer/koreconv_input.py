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
#   - server_startup.py  -- calls start_koreconv_loop alongside _scheduler_loop
#   - scheduler.py         -- task_queue singleton used for serialisation
#   - orchestration.py     -- orchestrate_prompt, OrchestratorConfig
#   - run_helpers.py       -- make_task_session
#   - koreconv_client.py   -- KoreChat URL accessor
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from conversation_state import decode_background_context
from conversation_state import encode_background_context
from conversation_state import build_background_turn
from conversation_state import estimate_next_turn_tokens
from conversation_state import merge_background_turns
from datasets import build_persisted_scratchpad_payload
from datasets import coerce_persisted_datasets_payload
from datasets import coerce_persisted_scratchpad_payload
from datasets import get_persisted_datasets_payload
from datasets import hydrate_session_state
from orchestration import OrchestratorConfig
from orchestration import orchestrate_prompt
from run_helpers import make_task_session
from scratchpad import get_store
from scratchpad import scratchpad_clear
from scratchpad import scratchpad_save
from utils.runtime_logger import SessionLogger
from utils.workspace_utils import load_runtime_config


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_CONFIG_KEY        = "korechaturl"
_DEFAULT_POLL_SECS = 3
_DEFAULT_TIMEOUT   = 8
_SESSION_PREFIX    = "kc_conv_"
# Fraction of config.num_ctx at which a compress_needed event is raised.
# Scales automatically when the user changes context size via /ctx size.
_COMPRESS_THRESHOLD = 0.70


def _latest_message(messages: list[dict]) -> dict | None:
    """Return the newest message regardless of API ordering or list truncation."""
    if not messages:
        return None
    return max(
        (item for item in messages if isinstance(item, dict)),
        key=lambda item: (str(item.get("created_at") or ""), int(item.get("id") or 0)),
        default=None,
    )


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


# ====================================================================================================
# MARK: PROMPT BUILDER
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _merge_conv_facts(scratchpad: dict, user_prompt: str, turn_count: int) -> dict:
    """Merge auto-extracted conversation facts into the scratchpad before persisting to KoreChat.

    These reserved _kc_* keys give the model durable per-channel memory about the
    conversation state without requiring explicit scratchpad tool calls.

    Keys written:
      _kc_last_asked  — the most recent user message (truncated to 200 chars)
      _kc_turn        — current turn number (post-completion)
    """
    # Extract the most recent user message from the built prompt text.
    last_asked = ""
    for marker in ("--- Respond to this message ---\n", "--- Conversation ---\n"):
        idx = user_prompt.rfind(marker)
        if idx >= 0:
            last_asked = user_prompt[idx + len(marker):].strip()[:200]
            break
    if not last_asked:
        last_asked = user_prompt.strip()[:200]

    updated = dict(scratchpad)
    updated["_kc_last_asked"] = last_asked
    updated["_kc_turn"] = str(turn_count + 1)
    return updated


def _coerce_conversation_scratchpad(conv: dict, push_log_line=None) -> dict[str, object]:
    scratchpad = conv.get("scratchpad") or {}
    if isinstance(scratchpad, str):
        try:
            scratchpad = json.loads(scratchpad)
        except Exception as exc:
            if push_log_line:
                push_log_line(f"[KORECHAT] Conv {conv.get('id', '?')}: scratchpad JSON decode failed - prompt built without scratchpad: {exc}")
            scratchpad = {}
    return coerce_persisted_scratchpad_payload(scratchpad)


def _coerce_conversation_datasets(conv: dict) -> dict[str, dict]:
    return coerce_persisted_datasets_payload(conv.get("datasets") or {})


# ----------------------------------------------------------------------------------------------------
def _build_prompt(conv: dict, messages: list[dict], push_log_line=None) -> str:
    """Build an LLM user prompt from a KoreChat conversation record and its messages."""
    background        = (conv.get("background_context") or "").strip()
    thread_summary    = (conv.get("thread_summary") or "").strip()
    scratchpad = _coerce_conversation_scratchpad(conv, push_log_line=push_log_line)
    datasets_payload = _coerce_conversation_datasets(conv)

    # Unsummarised messages only - summarised ones are already in thread_summary.
    unsummarised = [m for m in messages if not m.get("summarised")]

    parts: list[str] = []

    if thread_summary and not background:
        parts.append(f"--- Prior conversation summary ---\n{thread_summary}")

    if scratchpad:
        kv = "\n".join(f"  {k}: {v}" for k, v in scratchpad.items())
        parts.append(f"--- Scratchpad ---\n{kv}")

    if datasets_payload:
        lines: list[str] = []
        for dataset_name, manifest in sorted(datasets_payload.items()):
            count = int(manifest.get("count", 0))
            schema = manifest.get("schema") or []
            fields = ", ".join(str(field) for field in schema[:5])
            suffix = f" fields=[{fields}]" if fields else ""
            lines.append(f"  {dataset_name}: {count} records{suffix}")
        parts.append("--- Datasets ---\n" + "\n".join(lines))

    if unsummarised:
        lines: list[str] = []
        for m in unsummarised:
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
        (m for m in reversed(unsummarised) if m.get("direction") == "inbound"),
        None,
    )
    if last_inbound:
        content = (last_inbound.get("content") or "").strip()
        parts.append(f"--- Respond to this message ---\n{content}")

    return "\n\n".join(parts)


# ====================================================================================================
# MARK: COMPRESSION
# ====================================================================================================

def _handle_compress_needed(
    event:        dict,
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

    # Fetch all unsummarised messages.
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

    archived_turns: list[dict] = []
    pending_prompt: str | None = None
    input_chars                = 0
    for message in raw:
        content     = (message.get("content") or "").strip()
        direction   = message.get("direction")
        input_chars += len(content)
        if not content:
            continue
        if direction == "inbound":
            pending_prompt = content
            continue
        if direction == "outbound" and pending_prompt is not None:
            archived_turns.append(
                build_background_turn(
                    turn               = None,
                    user_prompt        = pending_prompt,
                    assistant_response = content,
                    skill_outputs      = [],
                )
            )
            pending_prompt = None

    input_tok_est = input_chars // 4
    push_log_line(f"[KORECHAT] Archiving conv {conv_id}: {len(raw)} messages, ~{input_tok_est:,} tok input")

    if not archived_turns:
        push_log_line(f"[KORECHAT] Conv {conv_id}: no complete turn pairs found - nothing to archive")
        _complete_event(base, event_id, "completed", push_log_line, context=f"conv {conv_id}")
        return

    archived_background = merge_background_turns(conv.get("background_context") or "", archived_turns)
    archived_tokens     = len(archived_background) // 4
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
    message_ids = [m["id"] for m in raw if m.get("id")]
    for msg_id in message_ids:
        try:
            _http_patch(base, f"/messages/{msg_id}", {"summarised": 1})
        except Exception as exc:
            push_log_line(f"[KORECHAT] Conv {conv_id}: could not mark message {msg_id} summarised: {exc}")

    reduction_pct = int(100 * (1 - archived_tokens / input_tok_est)) if input_tok_est > 0 else 0
    push_log_line(
        f"[KORECHAT] Conv {conv_id}: archived {len(message_ids)} message(s) into background_context "
        f"~{input_tok_est:,} tok -> ~{archived_tokens:,} tok ({reduction_pct}% reduction)"
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
    push_log_line,
) -> None:
    """Dispatch one KoreChat event to the appropriate handler."""
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
        _handle_compress_needed(
            event         = event,
            push_log_line = push_log_line,
        )
        return

    if event_type != "response_needed":
        push_log_line(f"[KORECHAT] Skipping unsupported event {event_id} ({event_type or 'unknown'})")
        _complete_event(base, event_id, "completed", push_log_line, context="skip")
        return

    if not conv_id:
        push_log_line(f"[KORECHAT] Event {event_id} has no conversation - completing as failed")
        _complete_event(base, event_id, "failed", push_log_line, context="response_needed")
        return

    session_id = f"{_SESSION_PREFIX}{conv_id}"
    turn_count = conv.get("turn_count", 0)
    push_log_line(f"[KORECHAT] Handling event {event_id} (conv {conv_id}, turn {turn_count + 1})")

    run_log_path = create_log_file_path(log_dir=log_dir)
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
        hydrate_session_state(
            conv.get("scratchpad") or {},
            session_id,
            datasets_payload=conv.get("datasets") or {},
            scratchpad_clearer=scratchpad_clear,
            scratchpad_restorer=scratchpad_save,
            warning_logger=lambda message: push_log_line(f"[KORECHAT] Conv {conv_id}: {message}"),
        )

        user_prompt = str(event_payload.get("prompt_override") or "").strip()
        if not user_prompt:
            user_prompt = _build_prompt(conv, messages, push_log_line=push_log_line)

        # KC owns the persisted conversation state. The agent keeps only transient
        # per-run session context in memory for this turn.
        _, session_ctx = make_task_session(
            session_id   = session_id,
            persist_path = None,
            max_turns    = 10,
        )

        # Item 4: Restore SessionContext from KoreChat background_context so the model
        # can reference prior fetched data across restarts and resume turns.
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

        response, prompt_tokens, completion_tokens, ok, tps = orchestrate_prompt(
            user_prompt          = user_prompt,
            config               = config,
            logger               = run_logger,
            conversation_history = None,
            session_context      = session_ctx,
            quiet                = True,
            conversation_entry   = conv,
            token_pressure       = token_pressure,
        )

        tps_str = f"{tps:.1f}" if tps > 0 else "0"
        push_log_line(
            f"[KORECHAT] Conv {conv_id}: [{prompt_tokens:,} tok, {tps_str} tok/s, ok={ok}]"
        )

        reply              = response.strip()
        current_scratchpad = get_store(session_id=session_id)
        fact_source_prompt = str(event_payload.get("visible_text") or "").strip() or user_prompt
        current_scratchpad = _merge_conv_facts(current_scratchpad, fact_source_prompt, turn_count)
        persisted_scratchpad = build_persisted_scratchpad_payload(current_scratchpad)
        persisted_datasets = get_persisted_datasets_payload(session_id)

        # Item 4: Serialize session context turns for persistence in KoreChat background_context.
        # This lets the model reference prior fetched data after restarts or on resume.
        sc_turns = session_ctx.get_turns()
        if sc_turns:
            new_background_context = encode_background_context(sc_turns, background_ctx)
        else:
            new_background_context = background_ctx  # keep existing if this turn had no tool calls

        # token_estimate reflects what the next turn will start from: prompt consumed
        # this turn plus the completion tokens (which become part of the thread next turn).
        new_token_estimate = estimate_next_turn_tokens(prompt_tokens, completion_tokens)

        # Write outbound message first - if this fails the event is not completed.
        try:
            _http_post(base, f"/conversations/{conv_id}/messages", {
                "direction":      "outbound",
                "content":        reply,
                "sender_display": str(event_payload.get("outbound_sender_display") or "agent"),
                "status":         "sent",
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
                "scratchpad":         persisted_scratchpad,
                "datasets":           persisted_datasets,
                "background_context": new_background_context,
            })
        except Exception as exc:
            push_log_line(
                f"[KORECHAT] Conv {conv_id}: WARN - conversation patch failed (scratchpad may be stale): {exc}"
            )
            _complete_event(base, event_id, "failed", push_log_line, context=f"conv {conv_id}")
            return

        # Complete the event.
        _complete_event(base, event_id, "completed", push_log_line, context=f"conv {conv_id}")

        # Raise outbound_ready so KoreChat can signal KoreComms for non-webchat delivery.
        channel = conv.get("channel_type", "webchat")
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

        # Check whether the running token estimate has crossed the compression threshold.
        # Uses config.num_ctx so /ctx size in the UI controls the trigger point directly.
        compress_at = int(config.num_ctx * _COMPRESS_THRESHOLD)
        if new_token_estimate >= compress_at:
            push_log_line(
                f"[KORECHAT] Conv {conv_id}: token_estimate {new_token_estimate:,} >= "
                f"compress threshold {compress_at:,} (ctx {config.num_ctx:,} * {_COMPRESS_THRESHOLD}) "
                f"- queuing compress_needed"
            )
            try:
                _http_post(base, "/events", {
                    "conversation_id": conv_id,
                    "event_type":      "compress_needed",
                    "priority":        10,
                    "payload":         {},
                })
            except Exception as exc:
                push_log_line(f"[KORECHAT] Conv {conv_id}: could not queue compress_needed: {exc}")


# ====================================================================================================
# MARK: BACKGROUND LOOP
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def start_koreconv_loop(
    config:              OrchestratorConfig,
    push_log_line,
    task_queue,
    create_log_file_path,
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
                        _handle_event(
                            event                = _ev,
                            config               = config,
                            log_dir              = log_dir,
                            session_logger_cls   = session_logger_cls,
                            create_log_file_path = create_log_file_path,
                            push_log_line        = push_log_line,
                        )

                    queued = task_queue.enqueue(task_name, "koreconv", _run_event)
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
