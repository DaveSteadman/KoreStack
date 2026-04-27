import threading

from KoreAgent.utils.workspace_utils import trunc


_last_context_map: list[dict] = []
_last_messages: list[dict] = []
_last_run_lock: threading.Lock = threading.Lock()

COMPACT_THRESHOLD: float = 0.50


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


def compact_context(context_map: list[dict], messages: list[dict], idx: int) -> bool:
    if idx < 0 or idx >= len(context_map):
        return False
    entry = context_map[idx]
    msg_idx = entry.get("msg_idx")
    if msg_idx is None or entry.get("compacted"):
        return False

    orig_chars = entry["chars"]
    auto_key = entry.get("auto_key")
    label = entry.get("label") or entry.get("role", "?")
    ref = f" -> scratchpad: {auto_key}" if auto_key else ""
    round_n = entry.get("round", 0)
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


def assess_compact(context_map: list[dict], messages: list[dict], round_num: int, num_ctx: int) -> tuple[int, int]:
    # Guard against context_map/messages index drift caused by callers adding one without the other.
    if context_map and messages:
        max_idx = max((e["msg_idx"] for e in context_map if e.get("msg_idx") is not None), default=-1)
        if max_idx != len(messages) - 1:
            raise RuntimeError(
                f"[assess_compact] context_map/messages index misalignment: "
                f"max msg_idx={max_idx} but len(messages)={len(messages)} - "
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
        if compact_context(context_map, messages, cm_idx):
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
