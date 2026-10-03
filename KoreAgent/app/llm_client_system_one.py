# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Ollama System One decision client. System One models are not chat models: they score named,
# typed questions against a shared text, JSON, and optional image state using /v1/systemone.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from __future__ import annotations

import json
import urllib.error
from dataclasses import dataclass
from typing import Any

import llm_client_core as _core


# ====================================================================================================
# MARK: TYPES
# ====================================================================================================
@dataclass(frozen=True)
class SystemOneCallResult:
    """Structured response returned by Ollama's ``/v1/systemone`` endpoint."""

    model:         str
    answers:       dict[str, dict[str, Any]]
    input_tokens:  int = 0
    output_tokens: int = 0

    @property
    def usage(self) -> dict[str, int]:
        """Return normalised token usage from the decision request."""
        return {
            "input_tokens":  self.input_tokens,
            "output_tokens": self.output_tokens,
        }

    def as_dict(self) -> dict[str, Any]:
        """Return the complete decision payload in the agent-tool-friendly form."""
        return {
            "model":   self.model,
            "answers": self.answers,
            "usage":   self.usage,
        }


# ====================================================================================================
# MARK: VALIDATION
# ====================================================================================================
_QUESTION_TYPES: frozenset[str] = frozenset({"choice", "noul", "score"})


def _validate_questions(questions: object) -> dict[str, dict[str, Any]]:
    if not isinstance(questions, dict) or not questions:
        raise ValueError("System One requires one to 64 named questions")
    if len(questions) > 64:
        raise ValueError("System One accepts at most 64 questions per request")

    normalised: dict[str, dict[str, Any]] = {}
    for name, definition in questions.items():
        question_name = str(name).strip()
        if not question_name:
            raise ValueError("System One question names cannot be blank")
        if not isinstance(definition, dict):
            raise ValueError(f"System One question '{question_name}' must be an object")
        question_type = str(definition.get("type") or "").strip().lower()
        instructions  = definition.get("instructions")
        if question_type not in _QUESTION_TYPES:
            raise ValueError(f"System One question '{question_name}' has unsupported type '{question_type}'")
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError(f"System One question '{question_name}' requires instructions")
        if question_type in {"choice", "score"} and "criteria" not in definition:
            raise ValueError(f"System One {question_type} question '{question_name}' requires criteria")
        normalised[question_name] = dict(definition)
    return normalised


# ====================================================================================================
# MARK: PUBLIC OPERATIONS
# ====================================================================================================
def call_system_one(
    *,
    state: str | dict | list,
    questions: dict[str, dict[str, Any]],
    images: list[str] | None = None,
    model_name: str | None = None,
    host: str | None = None,
    timeout: int | None = None,
) -> SystemOneCallResult:
    """Submit one typed System One decision request to local Ollama.

    ``images`` must contain base64 PNG, JPEG, or WebP payloads rather than URLs.
    ``keep_alive=-1`` retains the decision model alongside the chat model.
    """
    model     = str(model_name or _core.get_active_system_one_model()).strip()
    target    = (host or _core.get_active_host()).rstrip("/")
    questions = _validate_questions(questions)
    if not model:
        raise ValueError("No System One model is configured")
    if not isinstance(state, (str, dict, list)):
        raise ValueError("System One state must be a string, JSON object, or JSON array")
    if images is not None and (not isinstance(images, list) or not all(isinstance(image, str) and image.strip() for image in images)):
        raise ValueError("System One images must be a list of non-empty base64 strings")

    payload: dict[str, Any] = {
        "model":      model,
        "state":      state,
        "questions":  questions,
        "keep_alive": -1,
    }
    if images:
        payload["images"] = images

    effective_timeout = timeout if timeout is not None else _core.get_llm_timeout()
    _core.log_to_session(f"[System One] {model} | {len(questions)} question(s)")
    try:
        body = _core._request_json(
            url     = f"{target}/v1/systemone",
            method  = "POST",
            payload = payload,
            timeout = effective_timeout,
        )
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"System One HTTP error {error.code}: {error_body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Unable to reach Ollama System One at {target}: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError(f"System One timed out after {effective_timeout}s") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("System One returned a non-JSON response") from error

    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise RuntimeError("System One response did not include answers")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return SystemOneCallResult(
        model         = str(body.get("model") or model),
        answers       = answers,
        input_tokens  = int(usage.get("input_tokens") or 0),
        output_tokens = int(usage.get("output_tokens") or 0),
    )


def preload_system_one_model(
    model_name: str | None = None,
    *,
    host: str | None = None,
    timeout: int | None = None,
) -> None:
    """Warm and retain a System One model without coupling it to the chat model."""
    call_system_one(
        model_name = model_name,
        host       = host,
        timeout    = timeout,
        state      = "System One warmup.",
        questions  = {
            "ready": {
                "type":         "noul",
                "instructions": "Is the System One decision runtime available?",
            }
        },
    )
