# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreComms input source for MiniAgentFramework.
#
# Runs as a background polling thread (started by api_mode.py) that checks the KoreComms
# service for pending inbound messages. Each message is enqueued into the framework's
# shared task_queue so it is processed sequentially alongside scheduled tasks and other
# agent work. The LLM drives the reply - it decides what to say; the framework handles
# the KoreComms lifecycle (reply, complete) so the LLM never manages protocol state.
#
# Conversation continuity:
#   - Each KoreComms conversation maps to a stable session_id: "korecomms_conv_{id}"
#   - ConversationHistory and scratchpad are loaded from disk at the start of each message
#     and saved back when the run completes.
#   - The KoreComms thread (prior messages in the chain) is injected into the prompt so
#     the LLM has full context without relying on framework conversation history for it.
#
# Configuration:
#   Set "korecommsurl" in default.json (repo root), e.g. "http://localhost:8900".
#   If absent or unreachable, the poller logs a warning and retries on the next cycle.
#   Set "korecomms_poll_secs" to override the default poll interval (default: 15).
#
# Public entry point:
#   start_korecomms_loop(config, push_log_line, task_queue, create_log_file_path,
#                        LOG_DIR, load_session, save_session, shutdown_event)
#
# Related modules:
#   - api_mode.py          -- calls start_korecomms_loop alongside _scheduler_loop
#   - scheduler.py         -- task_queue singleton used for serialisation
#   - api.py               -- _load_session / _save_session passed in as callables
#   - orchestration.py     -- orchestrate_prompt, OrchestratorConfig
#   - run_helpers.py       -- make_task_session (ConversationHistory + SessionContext)
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from KoreAgent.orchestration import OrchestratorConfig
from KoreAgent.orchestration import orchestrate_prompt
from KoreAgent.run_helpers import make_task_session
from KoreAgent.utils.runtime_logger import SessionLogger
from KoreAgent.utils.workspace_utils import load_runtime_config


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_CONFIG_KEY          = "korecommsurl"
_POLL_KEY            = "korecomms_poll_secs"
_DEFAULT_POLL_SECS   = 15
_DEFAULT_TIMEOUT     = 8
_SESSION_PREFIX      = "korecomms_conv_"


# ====================================================================================================
# MARK: CONFIG
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _get_base_url() -> str | None:
    """Read korecommsurl from default.json on every call so live edits take effect immediately."""
    try:
        cfg = load_runtime_config()
        url = cfg.get(_CONFIG_KEY, "").strip().rstrip("/")
        return url if url else None
    except Exception:
        return None


# ----------------------------------------------------------------------------------------------------
def _get_poll_secs() -> int:
    """Read korecomms_poll_secs from default.json on every call so live edits take effect immediately."""
    try:
        cfg = load_runtime_config()
        val = int(cfg.get(_POLL_KEY, _DEFAULT_POLL_SECS))
        return max(5, val)
    except Exception:
        return _DEFAULT_POLL_SECS


# ====================================================================================================
# MARK: HTTP HELPERS
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _parse_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8")
        data = json.loads(body)
        return str(data.get("detail", exc.code))
    except Exception:
        return str(exc.code)


# ----------------------------------------------------------------------------------------------------
def _http_get(path: str, timeout: int = _DEFAULT_TIMEOUT) -> dict | None:
    base = _get_base_url()
    if not base:
        return None
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
        raise RuntimeError(f"KoreComms HTTP {exc.code}: {_parse_error_detail(exc)}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KoreComms unreachable: {exc.reason}") from exc


# ----------------------------------------------------------------------------------------------------
def _http_post(path: str, payload: dict, timeout: int = _DEFAULT_TIMEOUT) -> dict | None:
    base = _get_base_url()
    if not base:
        return None
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
        raise RuntimeError(f"KoreComms HTTP {exc.code}: {_parse_error_detail(exc)}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KoreComms unreachable: {exc.reason}") from exc


# ====================================================================================================
# MARK: KORECOMMS API CALLS
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _poll_next_message() -> dict | None:
    """GET /api/next-message - returns message + conversation + thread, or None if queue empty."""
    return _http_get("/api/next-message")


# ----------------------------------------------------------------------------------------------------
def _send_reply(message_id: int, content: str) -> None:
    """POST /api/reply - send the agent response back through the originating channel."""
    _http_post("/api/reply", {"message_id": message_id, "content": content})


# ----------------------------------------------------------------------------------------------------
def _complete(message_id: int, status: str) -> None:
    """POST /api/complete - mark current message as 'replied' or 'ignored'."""
    _http_post("/api/complete", {"message_id": message_id, "status": status})


# ====================================================================================================
# MARK: PROMPT BUILDER
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _format_thread(thread: list[dict]) -> str:
    """Format a KoreComms conversation thread into readable context for the LLM."""
    if not thread:
        return "(no prior messages in this conversation)"
    lines: list[str] = []
    for msg in thread:
        direction = msg.get("direction", "unknown")
        content   = str(msg.get("content", "")).strip()
        received  = msg.get("received_at", "")
        sender    = msg.get("sender", "")
        if direction == "inbound":
            label = f"Them ({sender})" if sender else "Them"
        else:
            label = "You (prior reply)"
        timestamp = f" [{received[:16]}]" if received else ""
        lines.append(f"{label}{timestamp}: {content}")
    return "\n\n".join(lines)


# ----------------------------------------------------------------------------------------------------
def _build_prompt(result: dict) -> str:
    """Build the LLM user prompt from a KoreComms next-message result."""
    message  = result.get("message", {})
    conv     = result.get("conversation", {})
    thread   = result.get("thread", [])
    iface    = result.get("interface") or {}

    content      = str(message.get("content", "")).strip()
    conv_id      = conv.get("id", "?")
    received_at  = message.get("received_at", "")
    timestamp    = f" (received {received_at[:16]})" if received_at else ""
    sender       = message.get("sender") or "Unknown"
    subject      = message.get("subject") or conv.get("subject") or "(no subject)"
    iface_name   = iface.get("name") or "Unknown"
    iface_type   = iface.get("type") or "unknown"
    conv_id      = conv.get("id", "?")
    received_at  = message.get("received_at", "")
    timestamp    = f" (received {received_at[:16]})" if received_at else ""

    # Exclude the current inbound message from the thread context - it is shown separately below.
    prior_thread = [m for m in thread if m.get("id") != message.get("id")]
    thread_block = _format_thread(prior_thread)

    return (
        f"You have received an inbound message via '{iface_name}' ({iface_type}), "
        f"conversation {conv_id}{timestamp}.\n\n"
        f"From:    {sender}\n"
        f"Subject: {subject}\n\n"
        f"--- Prior conversation thread ---\n{thread_block}\n"
        f"--- New inbound message ---\n{content}\n\n"
        "Respond to this message. Your response will be sent back through the same channel. "
        "If the message requires no reply (spam, automated notification, irrelevant), "
        "respond with exactly: NO_REPLY"
    )


# ====================================================================================================
# MARK: MESSAGE HANDLER
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def _handle_message(
    result:          dict,
    config:          OrchestratorConfig,
    log_dir:         Path,
    load_session,
    save_session,
    push_log_line,
    session_logger_cls,
    create_log_file_path,
) -> None:
    """Full lifecycle for one KoreComms message: orchestrate, reply or ignore, complete."""
    message    = result.get("message", {})
    conv       = result.get("conversation", {})
    message_id = message.get("id")
    conv_id    = conv.get("id")
    session_id = f"{_SESSION_PREFIX}{conv_id}"

    push_log_line(f"[KORECOMMS] Handling message {message_id} (conv {conv_id}, session {session_id})")

    run_log_path = create_log_file_path(log_dir=log_dir)
    with session_logger_cls(run_log_path) as run_logger:

        # Legacy chatsessions persistence is retired; this path now runs transiently.
        history, summaries = load_session(session_id)

        # Build the context objects for orchestration.
        _, session_ctx = make_task_session(
            session_id   = session_id,
            persist_path = None,
            max_turns    = 10,
        )

        user_prompt = _build_prompt(result)

        response, prompt_tokens, _ct, ok, tps = orchestrate_prompt(
            user_prompt          = user_prompt,
            config               = config,
            logger               = run_logger,
            conversation_history = history.as_list() or None,
            session_context      = session_ctx,
            quiet                = True,
        )

        tps_str = f"{tps:.1f}" if tps > 0 else "0"
        push_log_line(
            f"[KORECOMMS] Conv {conv_id}: "
            f"[{prompt_tokens:,} tok, {tps_str} tok/s, ok={ok}]"
        )

        # Update conversation history and persist.
        history.add(user_prompt, response)
        summaries = save_session(
            session_id,
            history,
            summaries,
            prompt_tokens,
            config.num_ctx,
        )

        # Determine reply vs. ignore.
        response_stripped = response.strip()
        if not ok or response_stripped.upper() == "NO_REPLY":
            push_log_line(f"[KORECOMMS] Conv {conv_id}: ignoring message {message_id}")
            try:
                _complete(message_id, "ignored")
            except Exception as exc:
                push_log_line(f"[KORECOMMS] Complete(ignored) failed for message {message_id}: {exc}")
        else:
            push_log_line(f"[KORECOMMS] Conv {conv_id}: replying to message {message_id}")
            try:
                _send_reply(message_id, response_stripped)
                _complete(message_id, "replied")
            except Exception as exc:
                push_log_line(f"[KORECOMMS] Reply/complete failed for message {message_id}: {exc}")
                # Still attempt complete so the queue can advance.
                try:
                    _complete(message_id, "ignored")
                except Exception:
                    pass


# ====================================================================================================
# MARK: BACKGROUND LOOP
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def start_korecomms_loop(
    config:               OrchestratorConfig,
    push_log_line,
    task_queue,
    create_log_file_path,
    log_dir:              Path,
    load_session,
    save_session,
    session_logger_cls,
    shutdown:             threading.Event,
) -> threading.Thread:
    """Start the background KoreComms polling thread and return it.

    The thread polls GET /api/next-message every korecomms_poll_secs seconds.
    When a message is ready it enqueues a task into task_queue so it is serialised
    with all other agent work. If korecommsurl is not configured, the thread exits
    immediately after logging a single notice.
    """
    def _loop() -> None:
        base = _get_base_url()
        if not base:
            push_log_line("[KORECOMMS] korecommsurl not configured - KoreComms integration disabled.")
            return

        poll_secs = _get_poll_secs()
        push_log_line(f"[KORECOMMS] Polling {base} every {poll_secs}s")

        while not shutdown.is_set():
            try:
                result = _poll_next_message()
                if result is not None:
                    message    = result.get("message", {})
                    conv       = result.get("conversation", {})
                    message_id = message.get("id")
                    conv_id    = conv.get("id")
                    task_name  = f"{_SESSION_PREFIX}{conv_id}"

                    def _run_message(_r=result, _name=task_name) -> None:
                        _handle_message(
                            result               = _r,
                            config               = config,
                            log_dir              = log_dir,
                            load_session         = load_session,
                            save_session         = save_session,
                            push_log_line        = push_log_line,
                            session_logger_cls   = session_logger_cls,
                            create_log_file_path = create_log_file_path,
                        )

                    queued = task_queue.enqueue(task_name, "korecomms", _run_message)
                    if queued:
                        push_log_line(
                            f"[KORECOMMS] Message {message_id} (conv {conv_id}) queued as '{task_name}'"
                        )
                    else:
                        push_log_line(
                            f"[KORECOMMS] Message {message_id} (conv {conv_id}) skipped - already queued or active"
                        )

            except Exception as exc:
                push_log_line(f"[KORECOMMS] Poll error: {exc}")

            # Sleep in short bursts so shutdown is responsive.
            for _ in range(poll_secs * 2):
                if shutdown.is_set():
                    break
                time.sleep(0.5)

    thread = threading.Thread(target=_loop, daemon=True, name="korecomms-poller")
    thread.start()
    return thread
