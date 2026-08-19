from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Edit store helpers for KoreCode/app.
# Provides the focused helpers and module-level behaviour grouped into this file.
# MARK: FUNCTIONS
# Function inventory:
# - _utc_now: Implements the  utc now operation for this module.
# - _proposals_root: Implements the  proposals root operation for this module.
# - _ensure_proposals_root: Implements the  ensure proposals root operation for this module.
# - _proposal_path: Implements the  proposal path operation for this module.
# - _write_proposal: Implements the  write proposal operation for this module.
# - get_edit_proposal: Returns edit proposal for this module.
# - _content_hash: Implements the  content hash operation for this module.
# - _replace_lines: Implements the  replace lines operation for this module.
# - _line_window: Implements the  line window operation for this module.
# - create_edit_proposal: Creates edit proposal for this module.
# - apply_edit_proposal: Implements the apply edit proposal operation for this module.
# ====================================================================================================

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from KoreCommon.suite_paths import get_suite_datacontrol_dir


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _proposals_root() -> Path:
    explicit = str(os.environ.get("KORECODE_EDIT_PROPOSALS_DIR", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (get_suite_datacontrol_dir() / "korecode" / "edit_proposals").resolve()


def _ensure_proposals_root() -> Path:
    root = _proposals_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _proposal_path(proposal_id: str) -> Path:
    return _ensure_proposals_root() / f"{proposal_id}.json"


def _write_proposal(payload: dict[str, Any]) -> dict[str, Any]:
    _proposal_path(str(payload["proposal_id"])).write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    return payload


def get_edit_proposal(proposal_id: str) -> dict[str, Any]:
    path = _proposal_path(proposal_id)
    if not path.exists():
        raise FileNotFoundError(proposal_id)
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _replace_lines(content: str, from_line: int, to_line: int, replacement: str) -> str:
    lines  = content.split("\n")
    before = lines[: max(0, from_line - 1)]
    after  = lines[max(0, to_line):]
    middle = str(replacement or "").split("\n")
    return "\n".join([*before, *middle, *after])


def _line_window(lines: list[str], start_line: int, end_line: int, pad: int) -> dict[str, Any]:
    total     = len(lines)
    from_line = max(1, start_line - pad)
    to_line   = min(total, end_line + pad)
    segment   = lines[from_line - 1:to_line]
    return {
        "from_line": from_line,
        "to_line":   to_line,
        "content":   "\n".join(segment),
    }


def create_edit_proposal(
    *,
    edits: list[dict[str, Any]],
    workspace_root: Path,
    resolve_relative_path,
    is_probably_text,
    read_text,
    validate_python_content,
    run_id: str | None = None,
    source: str = "assistant",
    summary: str = "",
) -> dict[str, Any]:
    proposal_id = uuid.uuid4().hex
    created_at  = _utc_now()
    normalized: list[dict[str, Any]] = []
    overall_ok  = True

    for index, raw_edit in enumerate(list(edits or [])):
        rel_path     = str(raw_edit.get("file") or "").strip()
        operation    = str(raw_edit.get("operation") or "edit").strip().lower()
        from_line    = int(raw_edit.get("from") or 1)
        to_line      = int(raw_edit.get("to") or from_line)
        replacement  = str(raw_edit.get("replacement") or "")
        explanation  = str(raw_edit.get("explanation") or raw_edit.get("reason") or "").strip()
        expected_hash = str(raw_edit.get("expected_hash") or "").strip() or None

        validation = {"ok": False, "errors": []}
        preview    = None
        file_exists = False
        current_hash = None
        language = "text"

        try:
            if not rel_path:
                raise ValueError("Edit missing file path")
            if operation not in {"edit", "delete"}:
                raise ValueError(f"Unsupported edit operation: {operation}")
            candidate  = resolve_relative_path(rel_path)
            file_exists = candidate.exists()
            if operation == "delete" and not file_exists:
                raise ValueError("Cannot delete a file that does not exist")
            current_content = ""
            if file_exists:
                if not candidate.is_file():
                    raise ValueError("Path is not a file")
                if not is_probably_text(candidate):
                    raise ValueError("Binary files are not supported")
                current_content, _encoding = read_text(candidate)
                current_hash = _content_hash(current_content)
                if expected_hash and current_hash != expected_hash:
                    raise ValueError("File changed on disk (content hash mismatch)")
                language = "python" if candidate.suffix.lower() in {".py", ".pyi"} else "text"
            candidate_content = "" if operation == "delete" else _replace_lines(current_content, from_line, to_line, replacement)
            if language == "python" and operation != "delete":
                validate_python_content(candidate, candidate_content)

            before_lines = current_content.split("\n") if current_content else []
            after_lines  = candidate_content.split("\n") if candidate_content else []
            preview      = {
                "before": _line_window(before_lines, from_line, to_line, 2) if before_lines else None,
                "after":  _line_window(after_lines, from_line, max(from_line, from_line + len(replacement.split("\n")) - 1), 2),
            }
            validation["ok"] = True
        except Exception as exc:
            validation["errors"].append(str(exc))
            overall_ok = False

        normalized.append(
            {
                "edit_id":       f"{proposal_id}:{index}",
                "file":          rel_path,
                "operation":     operation,
                "from":          from_line,
                "to":            to_line,
                "replacement":   replacement,
                "reason":        explanation,
                "expected_hash": expected_hash,
                "file_exists":   file_exists,
                "current_hash":  current_hash,
                "language":      language,
                "validation":    validation,
                "preview":       preview,
            }
        )

    payload = {
        "proposal_id":    proposal_id,
        "run_id":         str(run_id or "").strip() or None,
        "source":         str(source or "").strip() or "assistant",
        "summary":        str(summary or "").strip(),
        "status":         "proposed",
        "created_at":     created_at,
        "updated_at":     created_at,
        "workspace_root": str(workspace_root),
        "validation_ok":  overall_ok,
        "edits":          normalized,
    }
    return _write_proposal(payload)


def apply_edit_proposal(
    proposal_id: str,
    *,
    resolve_relative_path,
    is_probably_text,
    read_text,
    validate_python_content,
) -> dict[str, Any]:
    proposal = get_edit_proposal(proposal_id)
    if str(proposal.get("status") or "").strip() == "applied":
        return proposal

    edits       = list(proposal.get("edits") or [])
    errors      = []
    applied     = 0
    touched     = []
    edits_by_file: dict[str, list[dict[str, Any]]] = {}
    for edit in edits:
        edits_by_file.setdefault(str(edit.get("file") or "").strip(), []).append(edit)

    for rel_path, file_edits in edits_by_file.items():
        try:
            if not rel_path:
                raise ValueError("Edit missing file path")
            if any(not bool((edit.get("validation") or {}).get("ok")) for edit in file_edits):
                raise ValueError("Edit failed validation")
            candidate     = resolve_relative_path(rel_path)
            current_content = ""
            if candidate.exists():
                if not candidate.is_file():
                    raise ValueError("Path is not a file")
                if not is_probably_text(candidate):
                    raise ValueError("Binary files are not supported")
                current_content, _encoding = read_text(candidate)
                current_hash = _content_hash(current_content)
                expected_hashes = {
                    str(edit.get("expected_hash") or "").strip()
                    for edit in file_edits
                    if str(edit.get("expected_hash") or "").strip()
                }
                if any(expected_hash != current_hash for expected_hash in expected_hashes):
                    raise ValueError("File changed on disk (content hash mismatch)")
            else:
                if any(str(edit.get("operation") or "edit").strip().lower() == "delete" for edit in file_edits):
                    raise ValueError("Cannot delete a file that does not exist")
                candidate.parent.mkdir(parents=True, exist_ok=True)
            if any(str(edit.get("operation") or "edit").strip().lower() == "delete" for edit in file_edits):
                if len(file_edits) != 1:
                    raise ValueError("A delete operation cannot be combined with other edits for the same file")
                candidate.unlink()
                applied += 1
                touched.append(rel_path)
                continue

            merged = current_content
            for edit in sorted(file_edits, key=lambda item: int(item.get("from") or 1), reverse=True):
                from_line   = int(edit.get("from") or 1)
                to_line     = int(edit.get("to") or from_line)
                replacement = str(edit.get("replacement") or "")
                merged = _replace_lines(merged, from_line, to_line, replacement)
            if candidate.suffix.lower() in {".py", ".pyi"}:
                validate_python_content(candidate, merged)
            candidate.write_text(merged, encoding="utf-8", newline="")
            applied += len(file_edits)
            touched.append(rel_path)
        except Exception as exc:
            errors.append(f"{rel_path or 'unknown file'}: {exc}")

    proposal["status"]     = "applied" if not errors else "failed"
    proposal["updated_at"] = _utc_now()
    proposal["apply_result"] = {
        "ok":      not errors,
        "applied": applied,
        "errors":  errors,
        "paths":   touched,
    }
    return _write_proposal(proposal)
