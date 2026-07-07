from __future__ import annotations

import sqlite3
from typing import Callable


def sentence_schema_columns(owner_column: str) -> tuple[str, ...]:
    return (
        "id",
        owner_column,
        "sentence_index",
        "source_field",
        "char_start",
        "char_end",
        "chroma_indexed_at",
        "deleted",
    )


def split_sentences(text: str) -> list[tuple[int, int, str]]:
    """Split text into sentence-like spans with stable offsets into the original text."""
    value = str(text or "")
    if not value:
        return []

    sentences: list[tuple[int, int, str]] = []
    start = 0
    i     = 0
    n     = len(value)

    while start < n and value[start].isspace():
        start += 1
    i = start

    while i < n:
        if value[i] in ".!?":
            end = i + 1
            while end < n and value[end] in "\"')]":
                end += 1
            if end == n or value[end].isspace():
                sentence = value[start:end].strip()
                if sentence:
                    sentences.append((start, end, sentence))
                while end < n and value[end].isspace():
                    end += 1
                start = end
                i     = end
                continue
        i += 1

    if start < n:
        sentence = value[start:].strip()
        if sentence:
            sentences.append((start, n, sentence))
    return sentences


def sentence_index_needs_rebuild(conn: sqlite3.Connection) -> bool:
    sentence_cols = {row[1] for row in conn.execute("PRAGMA table_info(sentences)").fetchall()}
    if "sentence_text" not in sentence_cols:
        return False
    row = conn.execute(
        "SELECT 1 FROM sentences WHERE sentence_text IS NOT NULL AND sentence_text != '' LIMIT 1"
    ).fetchone()
    return bool(row)


def sentence_schema_needs_normalization(
    conn: sqlite3.Connection,
    expected_cols: tuple[str, ...],
) -> bool:
    current_cols = tuple(row[1] for row in conn.execute("PRAGMA table_info(sentences)").fetchall())
    if not current_cols:
        return False
    return current_cols != expected_cols


def normalize_sentence_schema(
    conn: sqlite3.Connection,
    *,
    owner_column: str,
    expected_cols: tuple[str, ...],
    backfill_callback: Callable[[sqlite3.Connection], None],
) -> None:
    current_cols = tuple(row[1] for row in conn.execute("PRAGMA table_info(sentences)").fetchall())
    if not current_cols or current_cols == expected_cols:
        return

    current_set  = set(current_cols)
    required_set = set(expected_cols)
    compatible   = required_set.issubset(current_set)

    conn.execute("DROP TABLE IF EXISTS sentences_new")
    conn.execute(
        f"""
        CREATE TABLE sentences_new (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            {owner_column}    INTEGER NOT NULL,
            sentence_index    INTEGER NOT NULL,
            source_field      TEXT NOT NULL,
            char_start        INTEGER NOT NULL,
            char_end          INTEGER NOT NULL,
            chroma_indexed_at TEXT,
            deleted           INTEGER NOT NULL DEFAULT 0,
            UNIQUE({owner_column}, sentence_index)
        )
        """
    )

    if compatible:
        conn.execute(
            f"""
            INSERT INTO sentences_new
                (id, {owner_column}, sentence_index, source_field, char_start, char_end, chroma_indexed_at, deleted)
            SELECT
                id,
                {owner_column},
                sentence_index,
                source_field,
                char_start,
                char_end,
                chroma_indexed_at,
                deleted
            FROM sentences
            """
        )

    conn.execute("DROP TABLE sentences")
    conn.execute("ALTER TABLE sentences_new RENAME TO sentences")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_sentences_{owner_column} ON sentences({owner_column})")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sentences_chroma_indexed_at ON sentences(chroma_indexed_at)")

    if not compatible:
        backfill_callback(conn)


def extract_sentence_text(
    source_row: sqlite3.Row | dict,
    sentence_row: sqlite3.Row | dict,
    *,
    value_transform: Callable[[str, str], str] | None = None,
) -> str:
    source_field = str(sentence_row["source_field"] or "")
    if isinstance(source_row, dict):
        source_value = source_row.get(source_field, "")
    else:
        source_value = source_row[source_field]
    source_text = str(source_value or "")
    if value_transform is not None:
        source_text = value_transform(source_field, source_text)
    char_start = max(0, int(sentence_row["char_start"]))
    char_end   = max(char_start, int(sentence_row["char_end"]))
    return source_text[char_start:char_end].strip()


def mark_sentences_indexed(
    conn: sqlite3.Connection,
    *,
    sentence_ids: list[int],
    indexed_at: str,
    deleted_filter: bool,
) -> int:
    if not sentence_ids:
        return 0
    validated    = [int(item) for item in sentence_ids]
    placeholders = ",".join("?" for _ in validated)
    where_clause = f"WHERE {'deleted = 0 AND ' if deleted_filter else ''}id IN ({placeholders})"
    cur = conn.execute(
        f"UPDATE sentences SET chroma_indexed_at = ? {where_clause}",
        [indexed_at, *validated],
    )
    return int(cur.rowcount or 0)


def reset_sentence_indexed_at(
    conn: sqlite3.Connection,
    *,
    owner_column: str,
    owner_id: int | None,
    deleted_filter: bool,
) -> int:
    clauses: list[str] = []
    params: list[object] = []
    if deleted_filter:
        clauses.append("deleted = 0")
    if owner_id is not None:
        clauses.append(f"{owner_column} = ?")
        params.append(int(owner_id))
    where_sql = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cur = conn.execute(
        f"UPDATE sentences SET chroma_indexed_at = NULL{where_sql}",
        params,
    )
    return int(cur.rowcount or 0)
