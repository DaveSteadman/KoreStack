from __future__ import annotations

import csv
from pathlib import Path

from app.database import _get_or_create_vocab_term, db_connection


def export_connections(csv_path: Path) -> int:
    """Write all graph connections to one CSV file. Returns row count written."""
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT vs.term AS start, vp.term AS connection, vo.term AS end
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
        writer.writerow(["start", "connection", "end"])
        for row in rows:
            writer.writerow([row["start"], row["connection"], row["end"]])
    return len(rows)


def import_connections(csv_path: Path) -> dict[str, int | str]:
    """
    Import graph connections from one CSV file.

    Creates missing vocab terms automatically.
    Skips blank rows, incomplete rows, and exact duplicate connections.
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
    skipped  = 0
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

            existing = conn.execute(
                """
                SELECT 1
                FROM relations
                WHERE subject_concept_id   = ?
                  AND predicate_concept_id = ?
                  AND object_concept_id    = ?
                """,
                (start_id, connection_id, end_id),
            ).fetchone()
            if existing:
                skipped += 1
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
                VALUES (?, ?, ?, 0, 1)
                """,
                (start_id, connection_id, end_id),
            )
            imported += 1

    return {
        "imported": imported,
        "skipped":  skipped,
    }
