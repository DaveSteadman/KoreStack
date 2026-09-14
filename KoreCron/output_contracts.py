# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Deterministic postconditions for CronPrompt output files.  Cron's agent reply is an assertion, not
# evidence: contracts inspect the files in datauser before the next prompt is allowed to run.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from KoreCommon.datauser_fs import DataUserPathError, resolve_datauser_path


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_MARKDOWN_SECTION_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
_WORD_RE             = re.compile(r"\b[\w][\w'-]*\b")
_CONTRACT_TYPES      = frozenset({"markdown_sections", "json_topics"})


# ====================================================================================================
# MARK: TYPES
# ====================================================================================================
@dataclass(frozen=True)
class OutputContractResult:
    path:   Path
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


# ====================================================================================================
# MARK: NORMALISATION
# ====================================================================================================
def normalize_output_contract(value: Any) -> dict[str, Any]:
    """Validate and normalise a serialisable prompt output contract."""
    if not isinstance(value, dict):
        raise ValueError("Output contract must be an object.")

    contract_type = str(value.get("type") or "").strip()
    if contract_type not in _CONTRACT_TYPES:
        raise ValueError(f"Unsupported output contract type: {contract_type or '(missing)'}.")

    path = str(value.get("path") or "").strip()
    if not path:
        raise ValueError("Output contract path is required.")

    normalized: dict[str, Any] = {
        "type":                contract_type,
        "path":                path,
        "min_items":           _bounded_int(value.get("min_items"), "min_items", minimum=1, default=1),
        "max_items":           _bounded_int(value.get("max_items"), "max_items", minimum=1, default=100),
        "min_words_per_item":  _bounded_int(value.get("min_words_per_item"), "min_words_per_item", minimum=0, default=0),
        "max_words_per_item":  _bounded_int(value.get("max_words_per_item"), "max_words_per_item", minimum=0, default=0),
        "max_repair_attempts": _bounded_int(value.get("max_repair_attempts"), "max_repair_attempts", minimum=0, default=0, maximum=3),
    }
    if normalized["min_items"] > normalized["max_items"]:
        raise ValueError("Output contract min_items must not exceed max_items.")
    if normalized["max_words_per_item"] and normalized["min_words_per_item"] > normalized["max_words_per_item"]:
        raise ValueError("Output contract min_words_per_item must not exceed max_words_per_item.")

    required_title = str(value.get("required_title") or "").strip()
    if required_title:
        normalized["required_title"] = required_title

    if contract_type == "json_topics":
        fields = value.get("required_fields", [])
        if not isinstance(fields, list) or not all(isinstance(field, str) and field.strip() for field in fields):
            raise ValueError("JSON output contract required_fields must be a list of field names.")
        normalized["required_fields"] = [field.strip() for field in fields]
        normalized["min_summary_words"] = _bounded_int(value.get("min_summary_words"), "min_summary_words", minimum=0, default=0)
        normalized["max_summary_words"] = _bounded_int(value.get("max_summary_words"), "max_summary_words", minimum=0, default=0)
        if normalized["max_summary_words"] and normalized["min_summary_words"] > normalized["max_summary_words"]:
            raise ValueError("Output contract min_summary_words must not exceed max_summary_words.")

    return normalized


def _bounded_int(value: Any, name: str, *, minimum: int, default: int, maximum: int = 100_000) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Output contract {name} must be an integer.") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"Output contract {name} must be between {minimum} and {maximum}.")
    return parsed


# ====================================================================================================
# MARK: VALIDATION
# ====================================================================================================
def validate_output_contract(contract: dict[str, Any], *, run_date: date | None = None) -> OutputContractResult:
    """Validate one resolved output file and return exact repairable failures."""
    normalized = normalize_output_contract(contract)
    today      = run_date or date.today()
    template   = normalized["path"].replace("{date}", today.isoformat())
    try:
        path = resolve_datauser_path(template)
    except DataUserPathError as exc:
        return OutputContractResult(path=Path(template), errors=(str(exc),))

    if not path.is_file():
        return OutputContractResult(path=path, errors=(f"Required output file does not exist: {template}",))
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        return OutputContractResult(path=path, errors=(f"Could not read output file: {exc}",))

    if normalized["type"] == "markdown_sections":
        errors = _validate_markdown_sections(content, normalized)
    else:
        errors = _validate_json_topics(content, normalized)
    return OutputContractResult(path=path, errors=tuple(errors))


def _validate_markdown_sections(content: str, contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required_title = contract.get("required_title")
    if required_title and not content.lstrip().startswith(f"# {required_title}"):
        errors.append(f"Document must begin with '# {required_title}'.")

    matches = list(_MARKDOWN_SECTION_RE.finditer(content))
    _validate_item_count(len(matches), contract, errors, label="Markdown topics")
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        words      = _word_count(content[match.end():next_start])
        _validate_word_count(f"Topic '{match.group(1).strip()}'", words, contract, errors)
    return errors


def _validate_json_topics(content: str, contract: dict[str, Any]) -> list[str]:
    try:
        items = json.loads(content)
    except json.JSONDecodeError as exc:
        return [f"JSON is invalid: {exc.msg} at line {exc.lineno}, column {exc.colno}."]
    if not isinstance(items, list):
        return ["JSON root must be an array of topic objects."]

    errors: list[str] = []
    _validate_item_count(len(items), contract, errors, label="JSON topics")
    required_fields = set(contract.get("required_fields") or [])
    for index, item in enumerate(items, start=1):
        label = f"JSON topic {index}"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object.")
            continue
        actual_fields = set(item)
        if actual_fields != required_fields:
            errors.append(f"{label} fields must be exactly {sorted(required_fields)}; found {sorted(actual_fields)}.")
        if not isinstance(item.get("title"), str) or not item["title"].strip():
            errors.append(f"{label} title must be a non-empty string.")
        if not isinstance(item.get("summary"), str):
            errors.append(f"{label} summary must be a string.")
        if not isinstance(item.get("content"), str):
            errors.append(f"{label} content must be a string.")
        _validate_word_count(f"{label} content", _word_count(item.get("content", "")), contract, errors)
        _validate_summary_word_count(label, _word_count(item.get("summary", "")), contract, errors)
        if not isinstance(item.get("tags"), list) or not item["tags"] or not all(isinstance(tag, str) and tag.strip() for tag in item["tags"]):
            errors.append(f"{label} tags must be an array of non-empty strings.")
    return errors


def _validate_item_count(count: int, contract: dict[str, Any], errors: list[str], *, label: str) -> None:
    minimum = contract["min_items"]
    maximum = contract["max_items"]
    if not minimum <= count <= maximum:
        errors.append(f"{label} count is {count}; expected {minimum} to {maximum}.")


def _validate_word_count(label: str, count: int, contract: dict[str, Any], errors: list[str]) -> None:
    minimum = contract["min_words_per_item"]
    maximum = contract["max_words_per_item"]
    if minimum and count < minimum:
        errors.append(f"{label} has {_word_quantity(count)}; requires at least {minimum}.")
    if maximum and count > maximum:
        errors.append(f"{label} has {_word_quantity(count)}; allows at most {maximum}.")


def _validate_summary_word_count(label: str, count: int, contract: dict[str, Any], errors: list[str]) -> None:
    minimum = contract.get("min_summary_words", 0)
    maximum = contract.get("max_summary_words", 0)
    if minimum and count < minimum:
        errors.append(f"{label} summary has {_word_quantity(count)}; requires at least {minimum}.")
    if maximum and count > maximum:
        errors.append(f"{label} summary has {_word_quantity(count)}; allows at most {maximum}.")


def _word_count(value: Any) -> int:
    return len(_WORD_RE.findall(str(value or "")))


def _word_quantity(count: int) -> str:
    return f"{count} word{'s' if count != 1 else ''}"
