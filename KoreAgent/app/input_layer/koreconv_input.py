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
#   2. Build prompt from thread_summary + unsummarised messages + scratchpad
#   3. Run orchestrate_prompt
#   4. POST /conversations/{id}/messages  (outbound reply)
#   5. PATCH /conversations/{id}          (updated thread_summary, scratchpad, token_estimate, turn_count)
#   6. POST /events/{event_id}/complete   {status: "completed"}
#   7. POST /events                       {event_type: "outbound_ready"}  (for KoreComms if needed)
#
# Each conversation maps to a stable session_id "kc_conv_{id}" for orchestration history.
#
# Configuration:
#   "korechaturl" in default.json (repo root), e.g. "http://localhost:8630".
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

from datasets import merge_persisted_session_payload
from orchestration import OrchestratorConfig
from orchestration import orchestrate_prompt
from run_helpers import make_task_session
from scratchpad import get_store
from scratchpad import scratch_clear
from scratchpad import scratch_save
from utils.runtime_logger import SessionLogger
from utils.workspace_utils import load_runtime_config


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_CONFIG_KEY        = "korechaturl"
_DEFAULT_BASE_URL  = "http://localhost:8630"
_DEFAULT_POLL_SECS = 3
_DEFAULT_TIMEOUT   = 8
_SESSION_PREFIX    = "kc_conv_"
# Fraction of config.num_ctx at which a compress_needed event is raised.
# Scales automatically when the user changes context size via /ctx size.
_COMPRESS_THRESHOLD = 0.70


# ====================================================================================================
# MARK: CONFIG
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _get_base_url() -> str | None:
    try:
        cfg = load_runtime_config()
        url = cfg.get(_CONFIG_KEY, "").strip().rstrip("/")
        return url if url else _DEFAULT_BASE_URL
    except Exception:
        return _DEFAULT_BASE_URL


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


# ----------------------------------------------------------------------------------------------------
def _build_prompt(conv: dict, messages: list[dict], push_log_line=None) -> str:
    """Build an LLM user prompt from a KoreChat conversation record and its messages."""
    thread_summary    = (conv.get("thread_summary") or "").strip()
    background        = (conv.get("background_context") or "").strip()
    scratchpad        = conv.get("scratchpad") or {}
    if isinstance(scratchpad, str):
        try:
            scratchpad = json.loads(scratchpad)
        except Exception as exc:
            if push_log_line:
                push_log_line(f"[KORECHAT] Conv {conv.get('id', '?')}: scratchpad JSON decode failed - prompt built without scratchpad: {exc}")
            scratchpad = {}

    # Unsummarised messages only - summarised ones are already in thread_summary.
    unsummarised = [m for m in messages if not m.get("summarised")]

    parts: list[str] = []

    if background:
        parts.append(f"--- Background context ---\n{background}")

    if thread_summary:
        parts.append(f"--- Prior conversation summary ---\n{thread_summary}")

    if scratchpad:
        kv = "\n".join(f"  {k}: {v}" for k, v in scratchpad.items())
        parts.append(f"--- Scratchpad ---\n{kv}")

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

_COMPRESS_PROMPT_TEMPLATE = """\
Summarise the following conversation thread into a concise paragraph that preserves
all facts, decisions, and context needed to continue the conversation. Keep it under
200 words. Output only the summary - no preamble or explanation.

--- Messages to summarise ---
{messages}
"""

# ----------------------------------------------------------------------------------------------------
def _handle_compress_needed(
    event:        dict,
    config:       OrchestratorConfig,
    log_dir:      Path,
    session_logger_cls,
    create_log_file_path,
    push_log_line,
) -> None:
    base     = _get_base_url()
    event_id = event.get("id")
    conv     = event.get("conversation") or {}
    conv_id  = conv.get("id")

    if not conv_id:
        push_log_line(f"[KORECHAT] compress event {event_id} has no conversation - completing as failed")
        try:
            _http_post(base, f"/events/{event_id}/complete", {"status": "failed"})
        except Exception as exc:
            push_log_line(f"[KORECHAT] Event {event_id}: complete call failed: {exc}")
        return

    # Fetch all unsummarised messages.
    try:
        raw = _http_get(base, f"/conversations/{conv_id}/messages?summarised=0&limit=500") or []
    except Exception as exc:
        push_log_line(f"[KORECHAT] Conv {conv_id}: could not fetch messages for compression: {exc}")
        try:
            _http_post(base, f"/events/{event_id}/complete", {"status": "failed"})
        except Exception as exc:
            push_log_line(f"[KORECHAT] Event {event_id}: complete call failed: {exc}")
        return

    if not raw:
        push_log_line(f"[KORECHAT] Conv {conv_id}: no unsummarised messages - nothing to compress")
        try:
            _http_post(base, f"/events/{event_id}/complete", {"status": "completed"})
        except Exception as exc:
            push_log_line(f"[KORECHAT] Event {event_id}: complete call failed: {exc}")
        return

    lines = []
    for m in raw:
        ts        = (m.get("created_at") or "")[:16]
        direction = m.get("direction", "?")
        content   = (m.get("content") or "").strip()
        label     = "User" if direction == "inbound" else "Agent"
        lines.append(f"[{ts}] {label}: {content}")
    messages_text = "\n\n".join(lines)
    input_chars    = len(messages_text)
    input_tok_est  = input_chars // 4

    push_log_line(f"[KORECHAT] Compressing conv {conv_id}: {len(raw)} messages, ~{input_tok_est:,} tok input")

    compress_prompt = _COMPRESS_PROMPT_TEMPLATE.format(messages=messages_text)

    run_log_path = create_log_file_path(log_dir=log_dir)
    with session_logger_cls(run_log_path) as run_logger:
        session_id = f"{_SESSION_PREFIX}{conv_id}_compress"
        _, session_ctx = make_task_session(
            session_id   = session_id,
            persist_path = None,
            max_turns    = 3,
        )

        summary, prompt_tokens, _ct, ok, _ = orchestrate_prompt(
            user_prompt          = compress_prompt,
            config               = config,
            logger               = run_logger,
            conversation_history = None,
            session_context      = session_ctx,
            quiet                = True,
        )

    if not ok or not summary.strip():
        push_log_line(f"[KORECHAT] Conv {conv_id}: compression run failed - leaving messages unsummarised")
        try:
            _http_post(base, f"/events/{event_id}/complete", {"status": "failed"})
        except Exception as exc:
            push_log_line(f"[KORECHAT] Event {event_id}: complete call failed: {exc}")
        return

    # Append new summary to existing thread_summary.
    existing_summary = (conv.get("thread_summary") or "").strip()
    new_summary = (existing_summary + "\n\n" + summary.strip()).strip() if existing_summary else summary.strip()

    # Mark messages as summarised.
    message_ids = [m["id"] for m in raw if m.get("id")]
    for msg_id in message_ids:
        try:
            _http_patch(base, f"/messages/{msg_id}", {"summarised": 1})
        except Exception as exc:
            push_log_line(f"[KORECHAT] Conv {conv_id}: could not mark message {msg_id} summarised: {exc}")

    # Patch conversation - reset token_estimate to rough summary size only.
    summary_tokens = len(new_summary) // 4
    try:
        _http_patch(base, f"/conversations/{conv_id}", {
            "thread_summary": new_summary,
            "token_estimate": summary_tokens,
        })
    except Exception as exc:
        push_log_line(f"[KORECHAT] Conv {conv_id}: failed to patch summary after compression: {exc}")

    reduction_pct = int(100 * (1 - summary_tokens / input_tok_est)) if input_tok_est > 0 else 0
    push_log_line(
        f"[KORECHAT] Conv {conv_id}: compressed {len(message_ids)} message(s) "
        f"~{input_tok_est:,} tok -> ~{summary_tokens:,} tok ({reduction_pct}% reduction)"
    )

    try:
        _http_post(base, f"/events/{event_id}/complete", {"status": "completed"})
    except Exception as exc:
        push_log_line(f"[KORECHAT] Event {event_id}: complete failed: {exc}")


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

    if event_type == "compress_needed":
        _handle_compress_needed(
            event                = event,
            config               = config,
            log_dir              = log_dir,
            session_logger_cls   = session_logger_cls,
            create_log_file_path = create_log_file_path,
            push_log_line        = push_log_line,
        )
        return

    if event_type != "response_needed":
        push_log_line(f"[KORECHAT] Skipping unsupported event {event_id} ({event_type or 'unknown'})")
        try:
            _http_post(base, f"/events/{event_id}/complete", {"status": "completed"})
        except Exception as exc:
            push_log_line(f"[KORECHAT] Event {event_id}: skip-complete failed: {exc}")
        return

    if not conv_id:
        push_log_line(f"[KORECHAT] Event {event_id} has no conversation - completing as failed")
        try:
            _http_post(base, f"/events/{event_id}/complete", {"status": "failed"})
        except Exception as exc:
            push_log_line(f"[KORECHAT] Event {event_id}: complete call failed: {exc}")
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
            fresh_messages = _http_get(base, f"/conversations/{conv_id}/messages?limit=10") or []
        except Exception:
            fresh_messages = messages
        if fresh_messages and (fresh_messages[-1].get("direction") or "") == "outbound":
            push_log_line(f"[KORECHAT] Conv {conv_id}: event {event_id} skipped - turn already answered by web API path")
            try:
                _http_post(base, f"/events/{event_id}/complete", {"status": "completed"})
            except Exception as exc:
                push_log_line(f"[KORECHAT] Event {event_id}: complete call failed: {exc}")
            return

        # Restore persisted scratchpad state into the active session before orchestration
        # so scratchpad tool calls operate on the KC-backed conversation state.
        conv_scratchpad = conv.get("scratchpad") or {}
        if isinstance(conv_scratchpad, str):
            try:
                conv_scratchpad = json.loads(conv_scratchpad)
            except Exception:
                conv_scratchpad = {}
        scratch_clear(session_id=session_id)
        for scratch_key, scratch_value in conv_scratchpad.items():
            try:
                scratch_save(scratch_key, str(scratch_value), session_id=session_id)
            except Exception as exc:
                push_log_line(f"[KORECHAT] Conv {conv_id}: could not restore scratchpad key {scratch_key!r}: {exc}")

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
            try:
                restored_turns = json.loads(background_ctx)
                if isinstance(restored_turns, list):
                    valid = [
                        t for t in restored_turns
                        if isinstance(t, dict)
                        and all(k in t for k in ("turn", "user_prompt", "assistant_response", "skill_outputs"))
                    ]
                    if valid:
                        with session_ctx._lock:
                            if not session_ctx._turns:
                                session_ctx._turns = valid
                        push_log_line(f"[KORECHAT] Conv {conv_id}: restored {len(valid)} turn(s) from background_context")
            except Exception as exc:
                push_log_line(f"[KORECHAT] Conv {conv_id}: could not restore background_context: {exc}")

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
            token_pressure       = token_pressure,
        )

        tps_str = f"{tps:.1f}" if tps > 0 else "0"
        push_log_line(
            f"[KORECHAT] Conv {conv_id}: [{prompt_tokens:,} tok, {tps_str} tok/s, ok={ok}]"
        )

        reply              = response.strip()
        current_scratchpad = get_store(session_id=session_id)
        current_scratchpad = _merge_conv_facts(current_scratchpad, user_prompt, turn_count)
        persisted_session_state = merge_persisted_session_payload(current_scratchpad, session_id)

        # Item 4: Serialize session context turns for persistence in KoreChat background_context.
        # This lets the model reference prior fetched data after restarts or on resume.
        sc_turns = session_ctx.get_turns()
        if sc_turns:
            compact_sc = []
            for t in sc_turns[-3:]:
                compact_sc.append({
                    "turn":               t.get("turn"),
                    "user_prompt":        (t.get("user_prompt") or "")[:150],
                    "assistant_response": (t.get("assistant_response") or "")[:300],
                    "skill_outputs":      [
                        {"skill": o.get("skill", "?"), "summary": (o.get("summary") or "")[:100]}
                        for o in (t.get("skill_outputs") or [])[:4]
                    ],
                })
            new_background_context = json.dumps(compact_sc, ensure_ascii=False)
        else:
            new_background_context = background_ctx  # keep existing if this turn had no tool calls

        # token_estimate reflects what the next turn will start from: prompt consumed
        # this turn plus the completion tokens (which become part of the thread next turn).
        new_token_estimate = prompt_tokens + completion_tokens

        # Write outbound message first - if this fails the event is not completed.
        try:
            _http_post(base, f"/conversations/{conv_id}/messages", {
                "direction":      "outbound",
                "content":        reply,
                "sender_display": "agent",
                "status":         "sent",
            })
        except Exception as exc:
            push_log_line(f"[KORECHAT] Conv {conv_id}: failed to write outbound message: {exc}")

        # Patch conversation metadata including scratchpad.
        # This is the durable write - we log failures loudly but still complete the event
        # so the conversation does not stay in agent_processing indefinitely.
        try:
            _http_patch(base, f"/conversations/{conv_id}", {
                "status":             "active",
                "token_estimate":     new_token_estimate,
                "turn_count":         turn_count + 1,
                "scratchpad":         persisted_session_state,
                "background_context": new_background_context,
            })
        except Exception as exc:
            push_log_line(
                f"[KORECHAT] Conv {conv_id}: WARN - conversation patch failed (scratchpad may be stale): {exc}"
            )

        # Complete the event.
        try:
            _http_post(base, f"/events/{event_id}/complete", {"status": "completed"})
        except Exception as exc:
            push_log_line(f"[KORECHAT] Event {event_id}: complete failed: {exc}")

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
