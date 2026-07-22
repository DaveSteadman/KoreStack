# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared helpers for running batched prompt sequences and compressing conversation history.
#
# Provides run_prompt_batch(), called by the /test slash command handler to execute a JSON
# prompt file sequentially and capture pass/fail output, and make_task_session() which
# creates a session context for a scheduled task run.
#
# Related modules:
#   - input_layer/slash_command_handlers_testing.py  -- calls run_prompt_batch for /test
#   - server_startup.py                              -- calls make_task_session for scheduled tasks
#   - orchestration.py                               -- orchestrate_prompt
# ====================================================================================================
from pathlib import Path
from typing import Callable

from agent.orchestration.engine import ConversationHistory
from agent.orchestration.engine import SessionContext
from agent.orchestration.engine import orchestrate_prompt


# ====================================================================================================
# MARK: SESSION FACTORY
# ====================================================================================================

def make_task_session(
    session_id: str,
    persist_path: Path | None,
    max_turns: int = 10,
) -> tuple[ConversationHistory, SessionContext]:
    history = ConversationHistory(max_turns=max_turns)
    ctx = SessionContext(session_id=session_id, persist_path=persist_path)
    return history, ctx


def _seed_history(history: ConversationHistory, seeded_turns: list[dict] | None) -> None:
    if not seeded_turns:
        return

    pending_user: str | None = None
    for msg in seeded_turns:
        if not isinstance(msg, dict):
            continue
        role    = str(msg.get("role") or "").strip()
        content = str(msg.get("content") or "")
        if role == "user":
            pending_user = content
            continue
        if role == "assistant" and pending_user is not None:
            history.add(pending_user, content)
            pending_user = None


def run_prompt_batch(
    prompts: list,
    *,
    session_id: str,
    persist_path: Path | None,
    config,
    logger,
    quiet: bool                         = True,
    max_turns: int                      = 10,
    seeded_turns: list[dict] | None     = None,
    save_turn_fn: Callable[[str, str], None] | None = None,
) -> list[dict]:
    history, session_ctx = make_task_session(
        session_id=session_id,
        persist_path=persist_path,
        max_turns=max_turns,
    )
    _seed_history(history, seeded_turns)
    results: list[dict] = []

    for prompt_text in prompts:
        current = prompt_text.get("prompt", "") if isinstance(prompt_text, dict) else str(prompt_text)
        if not current:
            continue
        response, p_tokens, _c, ok, tps = orchestrate_prompt(
            user_prompt=current,
            config=config,
            logger=logger,
            conversation_history=history.as_list() or None,
            session_context=session_ctx,
            quiet=quiet,
        )
        history.add(current, response)
        if save_turn_fn is not None:
            save_turn_fn(current, response)
        results.append({
            "prompt": current,
            "response": response,
            "prompt_tokens": p_tokens,
            "ok": ok,
            "tps": tps,
        })

    return results
