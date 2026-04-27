# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Session-scoped scratchpad store for the MiniAgentFramework.
#
# Provides a lightweight named-value store that persists for the lifetime of the process
# (i.e. one interactive session or scheduled run).  The LLM can save intermediate results
# under a short key and retrieve them later without carrying large payloads in context.
#
# Public API (used by scratchpad_skill.py and prompt_tokens.py):
#   scratch_save(key, value)  -- store a named value (overwrites on duplicate key)
#   scratch_load(key)         -- retrieve a stored value as a string
#   scratch_list()            -- return a human-readable list of current keys
#   scratch_delete(key)       -- remove one key
#   scratch_clear()           -- remove all keys (called at session reset)
#   get_store()               -- return a shallow copy of the store dict (for token resolution)
#   get_key_names()           -- return sorted list of active key names (for system prompt)
#
# Key rules:
#   - Keys are lowercased and stripped; alphanumeric plus underscore only.
#   - Values are stored as plain strings.
#   - {scratch:key} tokens in skill arguments are resolved by prompt_tokens.resolve_tokens().
#
# Related modules:
#   - code/skills/Scratchpad/scratchpad_skill.py  -- exposes these functions as tool calls
#   - code/prompt_tokens.py                       -- resolves {scratch:key} in skill args
#   - code/orchestration.py                       -- injects key names into system prompt
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import re
import threading

from KoreAgent.session_runtime import get_active_session_id


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_KEY_RE = re.compile(r"^[a-zA-Z0-9_]+$")

# Maximum number of auto-saved keys per session before the oldest is evicted.
# Auto keys are prefixed with _tc_ (tool call round outputs) or research_page_ (WebResearch pages).
# Named keys set by the user or skills are never evicted.
MAX_AUTO_KEYS:         int            = 40
_AUTO_KEY_PREFIXES:    tuple[str, ...] = ("_tc_", "research_page_")


def _is_auto_key(key: str) -> bool:
    return any(key.startswith(prefix) for prefix in _AUTO_KEY_PREFIXES)


# ====================================================================================================
# MARK: STORE
# ====================================================================================================
_SESSION_STORES: dict[str, dict[str, str]] = {}
_STORE_LOCK: threading.RLock = threading.RLock()

# Keys pinned by the active tool-loop run; pinned keys are skipped during auto-key eviction.
_SESSION_PINNED: dict[str, set[str]] = {}
_PINNED_LOCK:    threading.Lock      = threading.Lock()


def _resolve_session_id(session_id: str | None = None) -> str:
    cleaned = str(session_id or "").strip()
    return cleaned or get_active_session_id()


def _get_session_store(session_id: str | None = None) -> dict[str, str]:
    resolved = _resolve_session_id(session_id)
    with _STORE_LOCK:
        return _SESSION_STORES.setdefault(resolved, {})


# ----------------------------------------------------------------------------------------------------
def _build_scratch_query_system_prompt(instructions: str = "") -> str:
    if instructions:
        return instructions
    return (
        "You are a precise information extractor running in an isolated context. "
        "Use only the supplied content and never use outside knowledge, memory, or inference to fill gaps. "
        "Read the question and the content below, then respond with ONLY the answer:\n"
        "- If filtering a list or table: include every matching row in full, one per line. "
        "  Never group or summarise rows into ranges.\n"
        "- For requests that imply completeness such as 'list all', 'every', or 'full list', "
        "  return a complete answer only when the supplied content explicitly contains the full set.\n"
        "- Search result snippets, headlines, and summaries are not authoritative sources for exhaustive factual lists.\n"
        "- If extracting facts: pull only the directly relevant sentences, concisely.\n"
        "- If the answer is missing, partial, or cannot be proven from the supplied content, "
        "  respond with exactly: Not found in content."
    )


# ----------------------------------------------------------------------------------------------------
def _validate_key(key: str) -> str:
    """Normalise and validate a key; raise ValueError for illegal characters."""
    normalised = key.strip().lower()
    if not normalised:
        raise ValueError("Scratchpad key cannot be empty")
    if not _KEY_RE.match(normalised):
        raise ValueError(
            f"Scratchpad key '{key}' contains invalid characters - "
            "use letters, digits, and underscores only"
        )
    return normalised


# ====================================================================================================
# MARK: PUBLIC API
# ====================================================================================================
def scratch_save(key: str, value: str, session_id: str | None = None) -> str:
    """Store a named value in the scratchpad, overwriting any previous value for that key."""
    validated = _validate_key(key)
    store = _get_session_store(session_id)
    with _STORE_LOCK:
        # Evict oldest auto keys before inserting a new one so the store stays bounded.
        if _is_auto_key(validated) and validated not in store:
            with _PINNED_LOCK:
                _pinned = frozenset(_SESSION_PINNED.get(_resolve_session_id(session_id), set()))
            auto_keys = [k for k in store if _is_auto_key(k) and k not in _pinned]
            while len(auto_keys) >= MAX_AUTO_KEYS:
                del store[auto_keys.pop(0)]
        store[validated] = str(value)
    result = f"Saved to scratchpad key '{validated}' ({len(str(value))} chars)"
    return result


# ----------------------------------------------------------------------------------------------------
def scratch_pin(key: str, session_id: str | None = None) -> None:
    # Mark an auto-saved key as pinned so it is skipped during eviction for the duration
    # of the current tool-loop run.  Call scratch_unpin_all() when the run ends.
    resolved = _resolve_session_id(session_id)
    with _PINNED_LOCK:
        _SESSION_PINNED.setdefault(resolved, set()).add(key)


# ----------------------------------------------------------------------------------------------------
def scratch_unpin_all(session_id: str | None = None) -> None:
    # Remove all pin records for the session after a tool-loop run completes.
    resolved = _resolve_session_id(session_id)
    with _PINNED_LOCK:
        _SESSION_PINNED.pop(resolved, None)


# ----------------------------------------------------------------------------------------------------
def scratch_load(key: str, session_id: str | None = None) -> str:
    """Retrieve a stored value by key.  Returns an error string when the key does not exist."""
    validated = _validate_key(key)
    store = _get_session_store(session_id)
    with _STORE_LOCK:
        if validated not in store:
            return f"Scratchpad key '{validated}' not found.  Use scratch_list() to see available keys."
        return store[validated]


# ----------------------------------------------------------------------------------------------------
def scratch_list(session_id: str | None = None) -> str:
    """Return a formatted list of all current scratchpad keys and their sizes."""
    store = _get_session_store(session_id)
    with _STORE_LOCK:
        if not store:
            return "Scratchpad is empty."
        lines = [f"  {key}  ({len(store[key])} chars)" for key in sorted(store)]
    return "Scratchpad keys:\n" + "\n".join(lines)


# ----------------------------------------------------------------------------------------------------
def scratch_dump(session_id: str | None = None) -> str:
    """Return every key and its full stored value.  Intended for debugging."""
    store = _get_session_store(session_id)
    with _STORE_LOCK:
        if not store:
            return "Scratchpad is empty."
        sections = [f"[{key}]\n{store[key]}" for key in sorted(store)]
    return "Scratchpad dump:\n\n" + "\n\n".join(sections)


# ----------------------------------------------------------------------------------------------------
def scratch_delete(key: str, session_id: str | None = None) -> str:
    """Remove one key from the scratchpad."""
    validated = _validate_key(key)
    store = _get_session_store(session_id)
    with _STORE_LOCK:
        if validated not in store:
            return f"Scratchpad key '{validated}' not found - nothing deleted."
        del store[validated]
    return f"Deleted scratchpad key '{validated}'."


# ----------------------------------------------------------------------------------------------------
def scratch_search(substring: str, session_id: str | None = None) -> str:
    """Return a list of keys whose stored value contains *substring* (case-insensitive)."""
    store = _get_session_store(session_id)
    needle = substring.lower()
    with _STORE_LOCK:
        matches = [(key, len(val)) for key, val in store.items() if needle in val.lower()]
    if not matches:
        return f"No scratchpad keys contain the substring '{substring}'."
    lines = [f"  {key}  ({size} chars)" for key, size in sorted(matches)]
    return f"Keys matching '{substring}':\n" + "\n".join(lines)


# ----------------------------------------------------------------------------------------------------
def scratch_peek(key: str, substring: str, context_chars: int = 250, session_id: str | None = None) -> str:
    """Return the text around the first occurrence of *substring* in the value stored at *key*.

    Returns *context_chars* characters before and after the match, with '...' markers where the
    value was clipped and >>>match<<< highlighting around the hit.  Useful for inspecting a
    specific section of a large stored value without loading the entire content.
    """
    validated = _validate_key(key)
    store = _get_session_store(session_id)
    with _STORE_LOCK:
        if validated not in store:
            return f"Scratchpad key '{validated}' not found. Use scratch_list() to see available keys."
        value = store[validated]
    pos   = value.lower().find(substring.lower())
    if pos == -1:
        return f"Substring '{substring}' not found in scratchpad key '{validated}'."
    context_chars = max(0, int(context_chars))
    start  = max(0, pos - context_chars)
    end    = min(len(value), pos + len(substring) + context_chars)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(value) else ""
    match  = value[pos : pos + len(substring)]
    return (
        f"[Match in '{validated}' at char {pos} / {len(value)} total]\n"
        f"{prefix}{value[start:pos]}>>>{match}<<<{value[pos + len(substring):end]}{suffix}"
    )


# ----------------------------------------------------------------------------------------------------
def scratch_query(
    key: str,
    query: str,
    save_result_key: str = "",
    instructions: str = "",
    session_id: str | None = None,
) -> str:
    """Apply a natural-language query to stored scratchpad content via an isolated LLM call.

    Loads the full value stored at `key`, passes it to a clean-context LLM call together
    with `query`, and returns only the compact extracted answer.  The raw content never
    enters the caller's context window - this acts as a subroutine with its own stack.
    If `save_result_key` is provided the result is also saved under that key.
    """
    try:
        validated = _validate_key(key)
    except ValueError as exc:
        return f"Error: {exc}"
    store = _get_session_store(session_id)
    if not query or not query.strip():
        return "Error: query cannot be empty."
    with _STORE_LOCK:
        if validated not in store:
            return f"Scratchpad key '{validated}' not found.  Use scratch_list() to see available keys."
        content = store[validated]

    # Lazy imports to avoid circular deps at module load time.
    # Must use the fully-qualified package path so we share the same module
    # object (and the same _active_model global) as the rest of the app.
    # A bare 'from llm_client import' would create a second independent
    # module instance (sys.modules["llm_client"] != sys.modules["KoreAgent.llm_client"])
    # because code/KoreAgent/ can end up on sys.path via scratchpad_skill.py.
    try:
        from KoreAgent.llm_client import call_llm_chat as _call_llm_chat
        from KoreAgent.llm_client import get_active_model as _get_active_model
        from KoreAgent.llm_client import get_active_num_ctx as _get_active_num_ctx
    except Exception as exc:
        return f"Error importing LLM client: {exc}"

    model   = _get_active_model()
    num_ctx = _get_active_num_ctx()
    query_lower = query.lower()
    content_lower = content.lower()
    if (
        not model
        and any(token in query_lower for token in ("list all", "every", "full list", "complete list"))
        and ("search results for:" in content_lower or "https://" in content_lower)
    ):
        return "Not found in content."
    if not model:
        return "Error: no active model available.  Run a prompt first."

    inner_messages = [
        {
            "role":    "system",
            "content": _build_scratch_query_system_prompt(instructions),
        },
        {
            "role":    "user",
            "content": f"Question: {query}\n\nContent:\n{content}",
        },
    ]

    try:
        result    = _call_llm_chat(model_name=model, messages=inner_messages, tools=None, num_ctx=num_ctx)
        extracted = (result.response or "").strip()
        if not extracted:
            return f"LLM returned an empty response for query on key '{validated}'."
        if save_result_key:
            try:
                validated_save = _validate_key(save_result_key)
            except ValueError as exc:
                return f"Error in save_result_key: {exc}"
            with _STORE_LOCK:
                store[validated_save] = extracted
            return f"[Result saved to '{validated_save}']\n{extracted}"
        return extracted
    except Exception as exc:
        return f"Error during isolated LLM query: {exc}"


# ----------------------------------------------------------------------------------------------------
def scratch_clear(session_id: str | None = None) -> str:
    """Remove all keys from the scratchpad (called at session reset or /clear)."""
    resolved = _resolve_session_id(session_id)
    _get_session_store(resolved)  # ensure the session entry exists before taking the lock
    with _STORE_LOCK:
        count = len(_SESSION_STORES.get(resolved, {}))
        _SESSION_STORES[resolved] = {}
    return f"Scratchpad cleared ({count} key(s) removed)."


# ====================================================================================================
# MARK: INTERNAL ACCESSORS
# ====================================================================================================
def get_store(session_id: str | None = None) -> dict[str, str]:
    """Return a shallow copy of the store dict.  Used by prompt_tokens for {scratch:key} resolution."""
    store = _get_session_store(session_id)
    with _STORE_LOCK:
        return dict(store)


# ----------------------------------------------------------------------------------------------------
def get_key_names(session_id: str | None = None) -> list[str]:
    """Return a sorted list of active key names.  Used by orchestration to inject into system prompt."""
    store = _get_session_store(session_id)
    with _STORE_LOCK:
        return sorted(store.keys())
