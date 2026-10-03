# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Built-in agent tool for typed Ollama System One decisions. It intentionally exposes a compact
# boundary: the chat model supplies a state and explicit question schema, while Clef returns the
# scored answers without producing conversational text.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from __future__ import annotations

from typing import Any

from llm_client import call_system_one


# ====================================================================================================
# MARK: PUBLIC SKILL API
# ====================================================================================================
def system_one_decide(
    state: str | dict | list,
    questions: dict[str, dict[str, Any]],
    images: list[str] | None = None,
) -> dict[str, Any]:
    """Run typed decisions against the configured System One model.

    Question definitions use ``choice``, ``noul`` (yes/no probability), or
    ``score``. The return value preserves the model's answer objects, including
    probabilities and confidence where available, for the chat agent to explain.
    """
    return call_system_one(
        state     = state,
        questions = questions,
        images    = images,
    ).as_dict()
