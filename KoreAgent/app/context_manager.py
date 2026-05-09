import re
import threading

from utils.workspace_utils import trunc


_last_context_map: list[dict] = []
_last_messages: list[dict] = []
_last_run_lock: threading.Lock = threading.Lock()

COMPACT_THRESHOLD: float = 0.40


def get_last_context_map() -> list[dict]:
    with _last_run_lock:
        return list(_last_context_map)


def get_last_messages() -> list[dict]:
    with _last_run_lock:
        return list(_last_messages)


def store_last_run_state(context_map: list[dict], messages: list[dict]) -> None:
    global _last_context_map, _last_messages
    with _last_run_lock:
        _last_context_map = context_map
        _last_messages = messages


def estimate_thread_chars(messages: list[dict]) -> int:
    return sum(len(message.get("content") or "") for message in messages)


def compact_context(context_map: list[dict], messages: list[dict], idx: int, save_fn=None) -> bool:
    """Replace a context entry's message content with a compact placeholder.

    When *save_fn* is provided and the entry has no existing scratchpad key, the
    original content is saved to a generated '_cx_' key before being replaced so
    the model can still retrieve it via scratch_load or scratch_query.
    """
    if idx < 0 or idx >= len(context_map):
        return False
    entry = context_map[idx]
    msg_idx = entry.get("msg_idx")
    if msg_idx is None or entry.get("compacted"):
        return False

    orig_chars = entry["chars"]
    auto_key = entry.get("auto_key")
    label = entry.get("label") or entry.get("role", "?")
    round_n = entry.get("round", 0)

    # Preserve content to scratchpad before discarding it, so the model can
    # recover it later.  Only applies when there is no existing auto_key reference
    # and the message holds real content (not already a placeholder or empty).
    if not auto_key and save_fn is not None and msg_idx < len(messages):
        orig_content = (messages[msg_idx].get("content") or "").strip()
        if len(orig_content) > 100 and not orig_content.startswith("[compacted:"):
            safe_label = re.sub(r"[^a-z0-9]+", "_", label.lower())[:20].strip("_") or "msg"
            generated_key = f"_cx_r{round_n}_{safe_label}"
            try:
                save_fn(generated_key, orig_content)
                auto_key = generated_key
                entry["auto_key"] = generated_key
            except Exception:
                pass  # non-fatal; compaction proceeds without the key

    ref = f" -> scratchpad: {auto_key}" if auto_key else ""
    placeholder = f"[compacted: rnd {round_n} {label} ({orig_chars:,} chars{ref})]"

    msg_idx_end = entry.get("msg_idx_end")
    messages[msg_idx]["content"] = placeholder
    if msg_idx_end is not None and msg_idx_end > msg_idx:
        for i in range(msg_idx + 1, msg_idx_end + 1):
            if i < len(messages):
                messages[i]["content"] = ""

    entry["chars"] = len(placeholder)
    entry["compacted"] = True
    return True


def assess_compact(context_map: list[dict], messages: list[dict], round_num: int, num_ctx: int, save_fn=None) -> tuple[int, int]:
    """Check whether the thread exceeds COMPACT_THRESHOLD and evict oldest content if so.

    *save_fn*, when provided, is called as save_fn(key, content) for any message whose
    content would otherwise be silently dropped during compaction.
    """
    # Guard against context_map/messages index drift.  Since both lists grow in lock-step
    # the last context_map entry's msg_idx must equal the last messages index.  Checking
    # only the tail is O(1) and catches the same bugs as the previous O(n) max() scan.
    if context_map and messages:
        last_recorded = context_map[-1].get("msg_idx")
        if last_recorded is not None and last_recorded != len(messages) - 1:
            raise RuntimeError(
                f"[assess_compact] context_map/messages index misalignment: "
                f"last msg_idx={last_recorded} but len(messages)={len(messages)} - "
                "this indicates a message was added without a matching context_map entry"
            )
    thread_chars = estimate_thread_chars(messages)
    budget_chars = num_ctx * 4
    usage_fraction = thread_chars / budget_chars if budget_chars else 0.0
    if usage_fraction <= COMPACT_THRESHOLD:
        return thread_chars, 0

    candidates = [
        (cm_idx, entry)
        for cm_idx, entry in enumerate(context_map)
        if 0 < entry.get("round", 0) <= round_num - 2
        and entry.get("msg_idx") is not None
        and not entry.get("compacted")
    ]
    # Prefer large auto-key entries first (content already in scratchpad, cheap to drop),
    # then large non-auto entries (content will be saved to scratchpad via save_fn).
    candidates.sort(key=lambda item: (0 if item[1].get("auto_key") else 1, -item[1].get("chars", 0)))

    history_candidates = [
        (cm_idx, entry)
        for cm_idx, entry in enumerate(context_map)
        if entry.get("role") == "hist"
        and entry.get("msg_idx") is not None
        and not entry.get("compacted")
    ]

    compacted_count = 0
    for cm_idx, _entry in candidates + history_candidates:
        if compact_context(context_map, messages, cm_idx, save_fn=save_fn):
            compacted_count += 1
        thread_chars = estimate_thread_chars(messages)
        if thread_chars / budget_chars <= COMPACT_THRESHOLD:
            break

    return thread_chars, compacted_count


def format_context_map(context_map: list[dict], num_ctx: int) -> str:
    header = f"  {'#':>3}  {'rnd':>3}  {'role':<6}  {'label':<50}  {'chars':>7}  {'~tok':>6}"
    separator = "  ---  ---  ------  " + "-" * 50 + "  -------  ------"
    lines = [header, separator]
    total_chars = 0
    for idx, entry in enumerate(context_map):
        role = entry.get("role", "?")
        label = entry.get("label", "")
        chars = entry.get("chars", 0)
        auto_key = entry.get("auto_key")
        round_n = entry.get("round", 0)
        is_compacted = entry.get("compacted", False)
        total_chars += chars
        if auto_key and not is_compacted:
            label = f"{label} -> {auto_key}"
        if is_compacted:
            label = f"* {label}"
        lines.append(f"  {idx:>3}  {round_n:>3}  {role:<6}  {trunc(label, 50):<50}  {chars:>7,}  {chars // 4:>6,}")

    total_tokens = total_chars // 4
    remaining = num_ctx - total_tokens
    lines.append("")
    lines.append(f"  total: {total_chars:,} chars | ~{total_tokens:,} tokens used | ~{remaining:,} tokens remaining (budget: {num_ctx:,})")
    return "\n".join(lines)
