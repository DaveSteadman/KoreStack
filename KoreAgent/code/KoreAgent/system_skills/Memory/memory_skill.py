# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Memory skill module for the MiniAgentFramework.
#
# Stores environment-specific facts extracted from user prompts in a structured JSON file and
# supports lightweight relevance-based recall for later prompts. Facts are categorized
# (identity, project, environment, preference, general) and newer facts on the same subject
# supersede older ones to prevent stale duplicates. Access counts and last-accessed timestamps
# are tracked per entry for future relevance weighting.
#
# Related modules:
#   - main.py                   -- calls recall/store helpers during orchestration
#   - skills_catalog_builder.py -- reads skill.md to build the catalog entry
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import contextlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from KoreAgent.utils.workspace_utils import get_controldata_dir


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
# Store runtime data under datacontrol/ rather than beside the skill source files, so
# memory_store.json is never accidentally committed alongside version-controlled code.
MEMORY_STORE_PATH        = get_controldata_dir() / "memory_store.json"
MEMORY_STORE_LEGACY_PATH = Path(__file__).resolve().parent / "memory_store.txt"
MEMORY_SCHEMA_VERSION    = "2.0"

# In-process lock for all memory store read-modify-write cycles.
_MEMORY_LOCK      = threading.Lock()
# Advisory lock file used to serialise access across independent subprocesses (e.g. test runs).
_MEMORY_LOCK_FILE = MEMORY_STORE_PATH.with_suffix(".lock")
_LOCK_TIMEOUT_S   = 5.0


@contextlib.contextmanager
def _memory_store_locked():
    """Acquire in-process threading lock then an advisory file lock.

    The file lock serialises concurrent writes from independent subprocesses (e.g. multiple
    test_wrapper.py processes). A stale lock older than _LOCK_TIMEOUT_S is force-released.
    """
    with _MEMORY_LOCK:
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                fd = os.open(str(_MEMORY_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.close(fd)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    # Stale lock (process crashed while holding it) - force-release.
                    try:
                        _MEMORY_LOCK_FILE.unlink(missing_ok=True)
                    except OSError:
                        pass
                else:
                    time.sleep(0.02)
        try:
            yield
        finally:
            try:
                _MEMORY_LOCK_FILE.unlink(missing_ok=True)
            except OSError:
                pass

ENVIRONMENT_HINT_PATTERNS = [
    re.compile(r"[A-Za-z]:\\\\[^\s,;]+"),
    re.compile(r"(?:^|\s)(?:\./|\.\./)[^\s,;]+"),
    re.compile(r"(?:^|\s)/(?:[^\s/]+/)+[^\s/]*"),
    re.compile(r"\b\d+\.\d+(?:\.\d+)?\b"),
    re.compile(r"\b(?:gpt|llama|ollama|python|windows|linux|macos|repo|workspace|project|folder|path|model|version)\b", re.IGNORECASE),
]

ENVIRONMENT_KEYWORDS = {
    "workspace",
    "repo",
    "repository",
    "project",
    "folder",
    "directory",
    "path",
    "machine",
    "local",
    "installed",
    # "running" intentionally omitted - too broad (matches "running total" etc.);
    # covered adequately by the assertion verb pattern \b...runs|running...\b.
    "model",
    "models",
    "version",
    "python",
    "ollama",
    "windows",
    "linux",
    "macos",
    "framework",
}

GENERAL_KNOWLEDGE_HINTS = {
    "capital",
    "planet",
    "photosynthesis",
    "einstein",
    "history",
    "define",
    "explain",
}

# Regex that matches a sentence beginning with an interrogative word - these are questions
# even when they lack a trailing '?' and must not be stored as facts.
_QUESTION_OPENER_RE = re.compile(
    r"^\s*(?:what|which|who|whose|whom|how|when|where|why|"
    r"is|are|was|were|does|do|did|has|have|had|"
    r"can|could|would|should|will|shall|may|might|must)\b",
    re.IGNORECASE,
)

# Imperative/command openers - requests for the agent to do something, not statements of fact.
_COMMAND_OPENER_RE = re.compile(
    r"^\s*(?:show|output|list|tell|give|write|read|append|create|report|return|"
    r"summarize|summarise|display|print|get|fetch|find|search|check|run|execute|"
    r"provide|describe|calculate|explain|analyse|analyze|compare|count|generate|"
    r"format|convert|translate|extract|compute|plot|draw|build|make|open|close|"
    r"delete|remove|rename|move|copy|send|email|post|save|load|import|export)\b",
    re.IGNORECASE,
)

# Patterns that identify a user-stated preference or personal/project context fact.
# These are the primary signal for rich memory: "my X is Y", "we use X", "I prefer X", etc.
_PREFERENCE_STATEMENT_PATTERNS = [
    re.compile(r"\b(?:my|our)\s+\w+(?:\s+\w+)?\s+is\b", re.IGNORECASE),
    re.compile(r"\bthe\s+preferred\b", re.IGNORECASE),
    re.compile(r"\bthe\s+default\b", re.IGNORECASE),
    re.compile(r"\b(?:i|we)\s+(?:prefer|use|like|am\s+using|are\s+using)\b", re.IGNORECASE),
    re.compile(r"\bmy\s+name\s+is\b", re.IGNORECASE),
    re.compile(r"\bthis\s+project\s+(?:is|uses|called)\b", re.IGNORECASE),
    re.compile(r"\bwe\s+are\s+(?:building|working\s+on|using|running|called)\b", re.IGNORECASE),
    re.compile(r"\b(?:i(?:'m| am)|we(?:'re| are))\s+(?:a\s+)?\w+", re.IGNORECASE),
    re.compile(r"\bin\s+this\s+(?:environment|project|repo|setup)\b", re.IGNORECASE),
    re.compile(r"\bfor\s+this\s+(?:project|repo|environment|setup)\b", re.IGNORECASE),
]

# Token sets used by subject-key extraction for supersede matching.
_SUBJECT_STOP_WORDS = {
    "the", "a", "an", "for", "this", "our", "my", "in", "on", "at", "to", "of",
    "with", "by", "from", "as", "and", "or", "but", "that", "these", "those",
}

_ASSERTION_VERBS = {
    "is", "are", "uses", "using", "has", "have", "located", "runs", "running",
    "installed", "set", "was", "were", "called", "named",
}


# ====================================================================================================
# MARK: HELPERS
# ====================================================================================================
def _utc_now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----------------------------------------------------------------------------------------------------
def _short_id() -> str:
    return uuid.uuid4().hex[:8]


# ----------------------------------------------------------------------------------------------------
def _normalize_fact(text: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"\s+", " ", lowered)
    lowered = re.sub(r"[^a-z0-9\s:/._\\-]", "", lowered)
    return lowered


# ----------------------------------------------------------------------------------------------------
def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9_\\./:-]+", text.lower()) if len(token) >= 3}


# ----------------------------------------------------------------------------------------------------
def _categorize_fact(fact: str) -> str:
    lowered = fact.lower()
    if re.search(r"\bmy name is\b", lowered):
        return "identity"
    if any(p in lowered for p in ["prefer", "default", "favourite", "favorite", "like to use"]):
        return "preference"
    if any(p in lowered for p in ["project", "framework", "repo", "repository",
                                   "we are building", "this project", "building an", "building a"]):
        return "project"
    if any(p in lowered for p in ["path", "version", "python", "windows", "linux", "macos",
                                   "model", "workspace", "folder", "directory", "running on",
                                   "installed", "ollama"]):
        return "environment"
    return "general"


# ----------------------------------------------------------------------------------------------------
def _extract_subject_tokens(normalized_fact: str) -> set[str]:
    # Take meaningful tokens before the first assertion verb as the subject key.
    tokens  = normalized_fact.split()
    subject = []
    for token in tokens:
        clean = re.sub(r"[^a-z0-9]", "", token)
        if clean in _ASSERTION_VERBS:
            break
        if clean and clean not in _SUBJECT_STOP_WORDS and len(clean) >= 2:
            subject.append(clean)
        if len(subject) >= 6:
            break
    return set(subject)


# ----------------------------------------------------------------------------------------------------
def _facts_supersede(existing_normalized: str, new_normalized: str,
                     existing_category: str,  new_category: str) -> bool:
    # A new fact supersedes an existing one when they share the same category and their
    # subject-key tokens overlap by >= 60%, indicating they describe the same thing.
    if existing_category != new_category:
        return False
    existing_subj = _extract_subject_tokens(existing_normalized)
    new_subj      = _extract_subject_tokens(new_normalized)
    if not existing_subj or not new_subj:
        return False
    overlap = existing_subj & new_subj
    ratio   = len(overlap) / max(len(existing_subj), len(new_subj))
    return ratio >= 0.6


# ----------------------------------------------------------------------------------------------------
def _migrate_legacy_if_needed() -> None:
    # One-time migration: read memory_store.txt and write memory_store.json.
    # The .txt file is left in place as a historical artifact.
    if not MEMORY_STORE_LEGACY_PATH.exists():
        return
    entries = []
    for raw_line in MEMORY_STORE_LEGACY_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        timestamp, fact = line.split("|", maxsplit=1)
        fact = fact.strip()
        entries.append({
            "id":            _short_id(),
            "stored":        timestamp.strip(),
            "updated":       None,
            "category":      _categorize_fact(fact),
            "fact":          fact,
            "access_count":  0,
            "last_accessed": None,
        })
    _write_store({"schema_version": MEMORY_SCHEMA_VERSION, "entries": entries})


# ----------------------------------------------------------------------------------------------------
def _read_store() -> dict:
    if not MEMORY_STORE_PATH.exists():
        _migrate_legacy_if_needed()
    if not MEMORY_STORE_PATH.exists():
        return {"schema_version": MEMORY_SCHEMA_VERSION, "entries": []}
    try:
        return json.loads(MEMORY_STORE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"schema_version": MEMORY_SCHEMA_VERSION, "entries": []}


# ----------------------------------------------------------------------------------------------------
def _write_store(store: dict) -> None:
    MEMORY_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temp file then atomically replace, so readers never see a partially-written JSON.
    tmp = MEMORY_STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(str(tmp), str(MEMORY_STORE_PATH))


# ----------------------------------------------------------------------------------------------------
def _is_memorable_fact(candidate: str) -> bool:
    # Return True when the candidate sentence is a user-stated fact or preference worth storing.
    # Stores preference/identity statements, project/domain context, and durable environment facts.
    # Does NOT store questions, imperative commands, general knowledge, or ephemeral requests.
    normalized = candidate.strip()
    if not normalized or len(normalized) < 8:
        return False
    lowered = normalized.lower()
    if "?" in lowered:
        return False
    if _QUESTION_OPENER_RE.match(lowered):
        return False
    if _COMMAND_OPENER_RE.match(lowered):
        return False
    if any(token in lowered for token in GENERAL_KNOWLEDGE_HINTS):
        return False
    if any(pattern.search(normalized) for pattern in _PREFERENCE_STATEMENT_PATTERNS):
        return True
    has_env_keyword    = any(keyword in lowered for keyword in ENVIRONMENT_KEYWORDS)
    has_env_pattern    = any(pattern.search(normalized) for pattern in ENVIRONMENT_HINT_PATTERNS)
    has_assertion_verb = bool(re.search(
        r"\b(is|are|uses|using|has|have|located|runs|running|installed|set to)\b", lowered
    ))
    return (has_env_keyword or has_env_pattern) and has_assertion_verb


# ----------------------------------------------------------------------------------------------------
def _extract_candidate_segments(user_prompt: str) -> list[str]:
    # Split on hard separators but avoid breaking decimal/version tokens and relative paths (./, ../).
    split_segments      = re.split(r"[\n\r;]+|(?<![\d./])\.(?![\d./])", user_prompt)
    normalized_segments = []
    for segment in split_segments:
        cleaned = segment.strip(" -\t")
        if not cleaned:
            continue
        cleaned = re.sub(r"\s+", " ", cleaned)
        normalized_segments.append(cleaned)
    return normalized_segments


# ====================================================================================================
# MARK: PUBLIC SKILL API
# ====================================================================================================
def extract_environment_facts(user_prompt: str) -> list[str]:
    candidates = _extract_candidate_segments(user_prompt=user_prompt)
    facts      = []
    seen       = set()
    for candidate in candidates:
        if not _is_memorable_fact(candidate):
            continue
        normalized = _normalize_fact(candidate)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        facts.append(candidate)
    return facts


# ----------------------------------------------------------------------------------------------------
def store_prompt_memories(user_prompt: str) -> str:
    facts = extract_environment_facts(user_prompt=user_prompt)
    if not facts:
        _read_store()  # ensure store is initialized / migrated
        return "No new environment-specific facts detected in prompt."

    with _memory_store_locked():
        store     = _read_store()
        entries   = store.setdefault("entries", [])
        timestamp = _utc_now_iso()

        added_count   = 0
        updated_count = 0

        for fact in facts:
            normalized = _normalize_fact(fact)
            category   = _categorize_fact(fact)

            # Exact duplicate - skip
            if any(_normalize_fact(e["fact"]) == normalized for e in entries):
                continue

            # Supersede match - update the existing entry in place
            superseded = next(
                (e for e in entries
                 if _facts_supersede(_normalize_fact(e["fact"]), normalized, e["category"], category)),
                None,
            )
            if superseded:
                superseded["fact"]     = fact
                superseded["updated"]  = timestamp
                superseded["category"] = category
                updated_count += 1
                continue

            entries.append({
                "id":            _short_id(),
                "stored":        timestamp,
                "updated":       None,
                "category":      category,
                "fact":          fact,
                "access_count":  0,
                "last_accessed": None,
            })
            added_count += 1

        if added_count == 0 and updated_count == 0:
            return "No new memories were stored (all detected facts already existed)."

        store["schema_version"] = MEMORY_SCHEMA_VERSION
        _write_store(store)

    parts = []
    if added_count:
        parts.append(f"Stored {added_count} new memory fact(s).")
    if updated_count:
        parts.append(f"Updated {updated_count} existing memory fact(s).")
    return " ".join(parts)


# ----------------------------------------------------------------------------------------------------
def recall_relevant_memories(user_prompt: str, limit: int = 5, min_score: float = 0.25) -> str:
    try:
        limit     = int(limit)
        min_score = float(min_score)
    except (TypeError, ValueError):
        limit     = 5
        min_score = 0.25

    query_tokens = _tokenize(user_prompt)
    if not query_tokens:
        return "No memories recalled (query had no meaningful tokens)."

    # Hold the lock for the entire read-score-write cycle so access stats are consistent
    # even when multiple processes or threads run recall concurrently.
    with _memory_store_locked():
        store   = _read_store()
        entries = store.get("entries", [])
        if not entries:
            return "No memories stored yet."

        scored = []
        for entry in entries:
            fact        = entry["fact"]
            fact_tokens = _tokenize(fact)
            if not fact_tokens:
                continue
            overlap = query_tokens & fact_tokens
            if not overlap:
                continue
            score = len(overlap) / max(len(query_tokens), 1)
            if score < min_score and user_prompt.lower() not in fact.lower() and fact.lower() not in user_prompt.lower():
                continue
            scored.append((score, entry))

        if not scored:
            return "No relevant memories matched this prompt."

        scored.sort(key=lambda item: (-item[0], item[1]["stored"]))
        selected = scored[: max(limit, 1)]

        # Update access stats inside the lock so they are written atomically.
        timestamp = _utc_now_iso()
        for _, entry in selected:
            entry["access_count"]  = entry.get("access_count", 0) + 1
            entry["last_accessed"] = timestamp
        _write_store(store)

    lines = ["Relevant memories:"]
    for score, entry in selected:
        category = entry.get("category", "general")
        stored   = entry.get("stored", "")
        updated  = entry.get("updated")
        date_ref = f"updated: {updated}" if updated else f"stored: {stored}"
        lines.append(f"- [{category}] ({score:.2f}) {entry['fact']} [{date_ref}]")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------
def store_exchange_memories(user_prompt: str, assistant_response: str) -> str:
    # Extract and store facts from the assistant response side of a completed exchange.
    # The user-side facts are already handled by store_prompt_memories() before the tool loop runs.
    # This call captures agent-confirmed actions and facts stated in the response (e.g. file paths,
    # computed values, entities the agent looked up and confirmed).
    if not assistant_response or len(assistant_response.split()) < 4:
        return "Exchange memory storage skipped (response too short)."
    return store_prompt_memories(user_prompt=assistant_response)


# ----------------------------------------------------------------------------------------------------
def get_top_facts(limit: int = 6) -> str:
    # Return the most frequently accessed facts as a compact always-present block.
    # Sorted by access_count descending, then by stored date descending as a tiebreaker.
    # Returns empty string when no facts are stored.
    store   = _read_store()
    entries = store.get("entries", [])
    if not entries:
        return ""
    sorted_entries = sorted(
        entries,
        key=lambda e: (-e.get("access_count", 0), -(len(e.get("stored", "")))),
    )
    top = sorted_entries[:max(limit, 1)]
    lines = []
    for entry in top:
        category = entry.get("category", "general")
        lines.append(f"- [{category}] {entry['fact']}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------------------------------
def get_memory_store_text() -> str:
    store = _read_store()
    return json.dumps(store, indent=2, ensure_ascii=False)
