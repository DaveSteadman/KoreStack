from pathlib import Path

from KoreAgent.orchestration import ConversationHistory
from KoreAgent.orchestration import SessionContext
from KoreAgent.orchestration import orchestrate_prompt

# ====================================================================================================
# MARK: CONVERSATION COMPRESSION
# ====================================================================================================

def compact_turns(
    messages: list[dict],
    summaries: list[dict],
) -> tuple[list[dict], list[dict]]:
    # Compress all messages in [{role, content}...] format into a summary via an LLM call.
    # Returns (remaining_messages, updated_summaries).
    # Returns inputs unchanged on any failure so the caller is never corrupted.
    from KoreAgent.llm_client import call_llm_chat
    from KoreAgent.llm_client import get_active_model
    from KoreAgent.llm_client import get_active_num_ctx

    model   = get_active_model()
    num_ctx = get_active_num_ctx()
    if not model or not messages:
        return messages, summaries

    # Pair user/assistant messages into readable exchange blocks.
    pairs: list[str] = []
    pending_user: str | None = None
    for msg in messages:
        if msg.get("role") == "user":
            pending_user = msg.get("content") or ""
        elif msg.get("role") == "assistant" and pending_user is not None:
            pairs.append(f"User: {pending_user}\nAssistant: {msg.get('content') or ''}")
            pending_user = None

    if not pairs:
        return messages, summaries

    batch_text  = "\n\n".join(pairs)
    turn_count  = len(pairs)
    llm_messages = [
        {
            "role":    "system",
            "content": (
                "You are a precise conversation summariser. "
                "Compress the following conversation exchanges into one compact paragraph. "
                "Preserve all specific facts, decisions, code, URLs, names, and conclusions reached. "
                "Write in third person (e.g. 'The user asked about X; the assistant explained Y and provided Z.'). "
                "Do not interpret, evaluate, or add information not present in the exchanges."
            ),
        },
        {
            "role":    "user",
            "content": f"Conversation to summarise:\n\n{batch_text}",
        },
    ]

    try:
        result       = call_llm_chat(model_name=model, messages=llm_messages, tools=None, num_ctx=num_ctx)
        summary_text = (result.response or "").strip()
    except Exception as exc:
        print(f"[session] Warning: history compaction LLM call failed: {exc}", flush=True)
        return messages, summaries

    if not summary_text:
        return messages, summaries

    prior_end   = summaries[-1]["turn_range"][1] if summaries else 0
    new_summary = {
        "text":       summary_text,
        "turn_range": [prior_end + 1, prior_end + turn_count],
    }
    return [], summaries + [new_summary]


# ----------------------------------------------------------------------------------------------------
def build_summary_block(summaries: list[dict]) -> str:
    # Format summary dicts into a single string for injection into the system prompt.
    if not summaries:
        return ""
    parts = [
        f"[Turns {s['turn_range'][0]}-{s['turn_range'][1]}] {s['text']}"
        for s in summaries
    ]
    return "\n\n".join(parts)


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


def run_prompt_batch(
    prompts: list,
    *,
    session_id: str,
    persist_path: Path | None,
    config,
    logger,
    quiet: bool = True,
    max_turns: int = 10,
) -> list[dict]:
    history, session_ctx = make_task_session(
        session_id=session_id,
        persist_path=persist_path,
        max_turns=max_turns,
    )
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
        results.append({
            "prompt": current,
            "response": response,
            "prompt_tokens": p_tokens,
            "ok": ok,
            "tps": tps,
        })

    return results
