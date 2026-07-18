from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Task routing and playbook definitions for KoreCode's constrained coding-agent execution model.
# ====================================================================================================

from dataclasses import dataclass
import re


_READ_TOOLS = (
    "read_file",
    "read_context",
    "list_tree",
    "search_in_file",
    "get_python_function",
)
_PYTHON_EXECUTION_TOOLS = (
    "check_python",
    "run_python",
)
_IMPLEMENTATION_TOOLS = _READ_TOOLS + _PYTHON_EXECUTION_TOOLS


@dataclass(frozen=True)
class PlaybookDefinition:
    identifier:        str
    label:             str
    description:       str
    allowed_tools:     tuple[str, ...]
    required_evidence: tuple[str, ...]
    validation:        tuple[str, ...]
    permits_edits:     bool = False

    def payload(self) -> dict:
        return {
            "id":                self.identifier,
            "label":             self.label,
            "description":       self.description,
            "allowed_tools":     list(self.allowed_tools),
            "required_evidence": list(self.required_evidence),
            "validation":        list(self.validation),
            "permits_edits":     self.permits_edits,
        }


PLAYBOOKS: dict[str, PlaybookDefinition] = {
    "explore": PlaybookDefinition(
        identifier        = "explore",
        label             = "Explore workspace",
        description       = "Build a grounded understanding of code without proposing edits.",
        allowed_tools     = _READ_TOOLS,
        required_evidence = ("files or symbols inspected", "source-backed findings"),
        validation        = ("state unresolved questions",),
    ),
    "diagnose_failing_test": PlaybookDefinition(
        identifier        = "diagnose_failing_test",
        label             = "Diagnose failing test",
        description       = "Trace a failing test to a likely root cause before proposing the smallest correction.",
        allowed_tools     = _IMPLEMENTATION_TOOLS,
        required_evidence = ("failing contract", "production path", "root-cause hypothesis"),
        validation        = ("targeted test required before completion", "adjacent regression tests recommended"),
        permits_edits     = True,
    ),
    "create_file": PlaybookDefinition(
        identifier        = "create_file",
        label             = "Create file",
        description       = "Create one scoped file through a reviewed edit proposal.",
        allowed_tools     = ("list_tree", "read_file", "read_context", "check_python", "run_python"),
        required_evidence = ("target path checked", "content purpose stated"),
        validation        = ("syntax validation for Python", "review proposal before apply"),
        permits_edits     = True,
    ),
    "bounded_change": PlaybookDefinition(
        identifier        = "bounded_change",
        label             = "Bounded implementation",
        description       = "Implement one narrowly scoped code change with evidence and reviewable edits.",
        allowed_tools     = _IMPLEMENTATION_TOOLS,
        required_evidence = ("target behavior", "affected code inspected", "scope stated"),
        validation        = ("syntax validation", "targeted test recommendation"),
        permits_edits     = True,
    ),
    "refactor": PlaybookDefinition(
        identifier        = "refactor",
        label             = "Refactor",
        description       = "Improve structure while preserving the observable behavior.",
        allowed_tools     = _IMPLEMENTATION_TOOLS,
        required_evidence = ("existing behavior", "affected callers", "non-functional intent"),
        validation        = ("targeted regression tests", "review unrelated edits"),
        permits_edits     = True,
    ),
    "run_and_debug_python": PlaybookDefinition(
        identifier        = "run_and_debug_python",
        label             = "Run and debug Python",
        description       = "Run one Python script, inspect its output, and propose the smallest correction when it fails.",
        allowed_tools     = _IMPLEMENTATION_TOOLS,
        required_evidence = ("Python execution output", "failing location or traceback", "root-cause hypothesis"),
        validation        = ("re-run after correction", "report exit code and captured output"),
        permits_edits     = True,
    ),
}


def route_task(*, user_text: str, mode: str) -> PlaybookDefinition:
    text = str(user_text or "").lower()
    selected_mode = str(mode or "").lower()

    if selected_mode == "explain":
        return PLAYBOOKS["explore"]
    if text in {"run", "run it"} or any(phrase in text for phrase in ("run python", "run this", "run the file", "run file", "execute", "debug", "traceback", "syntax error")) or re.search(r"\brun\s+[^\s]+\.py\b", text):
        return PLAYBOOKS["run_and_debug_python"]
    if selected_mode in {"bughunt", "tests"} or any(phrase in text for phrase in ("failing test", "test fails", "test failure", "regression")):
        return PLAYBOOKS["diagnose_failing_test"]
    if selected_mode == "refactor" or "refactor" in text:
        return PLAYBOOKS["refactor"]
    if any(phrase in text for phrase in ("create file", "new file", "add file")) or re.search(r"\bcreate\s+[^\s]+\.[a-z0-9]+\b", text):
        return PLAYBOOKS["create_file"]
    if any(phrase in text for phrase in ("fix ", "implement ", "add ", "change ", "update ")):
        return PLAYBOOKS["bounded_change"]
    return PLAYBOOKS["explore"]


def list_playbooks() -> list[dict]:
    return [playbook.payload() for playbook in PLAYBOOKS.values()]
