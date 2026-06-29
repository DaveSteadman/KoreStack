from __future__ import annotations

import csv
from pathlib import Path

from app.database import _get_or_create_vocab_term, db_connection

_STATE_LABELS = {
    0: "proposed",
    1: "active",
    2: "deprecated",
    3: "rejected",
    4: "pasttense",
}
_STATE_VALUES = {label: state for state, label in _STATE_LABELS.items()}


def _parse_optional_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        parsed = int(text)
    except ValueError as exc:
        raise ValueError(f"Invalid integer value: {text!r}") from exc
    return max(minimum, min(maximum, parsed))


def _parse_optional_state(value: object, *, default: int = 0) -> int:
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in _STATE_VALUES:
        return _STATE_VALUES[text]
    try:
        parsed = int(text)
    except ValueError:
        return default
    return parsed if parsed in _STATE_LABELS else default


def export_connections(csv_path: Path, include_state_score: bool = False) -> int:
    """Write all graph connections to one CSV file. Returns row count written."""
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT vs.term AS start,
                   vp.term AS connection,
                   vo.term AS end,
                   r.state AS state,
                   r.score AS score
            FROM relations r
            JOIN vocab vs ON vs.id = (
                SELECT MIN(id) FROM vocab WHERE concept_id = r.subject_concept_id
            )
            JOIN vocab vp ON vp.id = (
                SELECT MIN(id) FROM vocab WHERE concept_id = r.predicate_concept_id
            )
            JOIN vocab vo ON vo.id = (
                SELECT MIN(id) FROM vocab WHERE concept_id = r.object_concept_id
            )
            ORDER BY vs.term COLLATE NOCASE,
                     vp.term COLLATE NOCASE,
                     vo.term COLLATE NOCASE
            """
        ).fetchall()

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        headers = ["start", "connection", "end"]
        if include_state_score:
            headers.extend(["state", "score"])
        writer.writerow(headers)
        for row in rows:
            values = [row["start"], row["connection"], row["end"]]
            if include_state_score:
                values.extend([_STATE_LABELS.get(row["state"], "proposed"), row["score"]])
            writer.writerow(values)
    return len(rows)


def import_connections(csv_path: Path) -> dict[str, int | str]:
    """
    Import graph connections from one CSV file.

    Creates missing vocab terms automatically.
    Skips blank rows, incomplete rows, and exact no-op duplicate connections.
    Existing triples are updated when the CSV carries different state and/or score.
    """
    if not csv_path.exists():
        return {
            "imported": 0,
            "skipped":  0,
            "error":    f"File not found: {csv_path}",
        }

    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"start", "connection", "end"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise ValueError(
                "connections.csv must have 'start', 'connection' and 'end' column headers"
            )
        rows = list(reader)

    imported = 0
    updated  = 0
    skipped  = 0
    imported_with_metadata = 0
    with db_connection() as conn:
        for row in rows:
            start      = str(row.get("start")      or "").strip()
            connection = str(row.get("connection") or "").strip()
            end        = str(row.get("end")        or "").strip()

            if not start or not connection or not end:
                skipped += 1
                continue

            start_id      = _get_or_create_vocab_term(conn, start)
            connection_id = _get_or_create_vocab_term(conn, connection)
            end_id        = _get_or_create_vocab_term(conn, end)
            state         = _parse_optional_state(row.get("state"), default=0)
            score         = _parse_optional_int(row.get("score"), default=1, minimum=0, maximum=255)
            has_metadata  = "state" in row or "score" in row

            existing = conn.execute(
                """
                SELECT state, score
                FROM relations
                WHERE subject_concept_id   = ?
                  AND predicate_concept_id = ?
                  AND object_concept_id    = ?
                """,
                (start_id, connection_id, end_id),
            ).fetchone()
            if existing:
                if existing["state"] == state and existing["score"] == score:
                    skipped += 1
                    continue
                conn.execute(
                    """
                    UPDATE relations
                    SET state = ?, score = ?
                    WHERE subject_concept_id   = ?
                      AND predicate_concept_id = ?
                      AND object_concept_id    = ?
                    """,
                    (state, score, start_id, connection_id, end_id),
                )
                updated += 1
                if has_metadata:
                    imported_with_metadata += 1
                continue

            conn.execute(
                """
                INSERT INTO relations(
                    subject_concept_id,
                    predicate_concept_id,
                    object_concept_id,
                    state,
                    score
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (start_id, connection_id, end_id, state, score),
            )
            imported += 1
            if has_metadata:
                imported_with_metadata += 1

    return {
        "imported":               imported,
        "updated":                updated,
        "skipped":                skipped,
        "imported_with_metadata": imported_with_metadata,
    }
