# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# SQLite database layer for KoreGraph.
#
# Schema (2 tables only):
#   vocab      -- the only place raw strings live; maps terms to integer concept IDs
#   relations  -- directed triples: (subject, predicate, object) stored as concept_id integers
#
# Design principle: vocab is the single string→number gateway.
#   Every other table is pure integers (concept_ids). No raw strings anywhere else.
#
# Seed data:
#   _seed_predicates() ensures common predicate terms exist in vocab on first run.
#   CONCEPT_BLACKLIST is a frozenset of words that must never become concept terms.
#
# Related modules:
#   - app/server.py  -- all DB calls
#   - app/config.py  -- cfg["data_dir"]
# ====================================================================================================
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from app.config import cfg

_DB_PATH = Path(cfg["data_dir"]) / "graph.db"


# ---------------------------------------------------------------------------
# MARK: Connection
# ---------------------------------------------------------------------------

@contextmanager
def db_connection():
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# MARK: Blacklist
# ---------------------------------------------------------------------------

#: Words that must never become concept terms or predicate labels.
#: Edit this set to extend it; it is checked before every insert.
CONCEPT_BLACKLIST: frozenset[str] = frozenset({
    "a", "an", "the", "this", "that", "these", "those",
    "in", "on", "at", "to", "of", "for", "by", "as", "with",
    "is", "are", "was", "were", "be", "been", "being",
    "from", "into", "onto", "upon", "about", "over", "under",
    "and", "or", "but", "not", "nor", "so", "yet", 
    "it", "its", "then", "there", "here",
    "he", "she", "they", "we", "you", "i",
})


def _is_blacklisted(word: str) -> bool:
    return word.strip().lower() in CONCEPT_BLACKLIST


# ---------------------------------------------------------------------------
# MARK: Schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db_connection() as conn:

        # ── Vocab ─────────────────────────────────────────────────────────────
        # Maps raw string terms to integer concept IDs.
        # Multiple rows may share the same concept_id — those are aliases.
        # Every other table stores concept_id integers only; no raw strings.
        #
        #   vocab
        #   ├── id          row PK (used for vocab CRUD operations)
        #   ├── concept_id  the shared number all aliases for a concept resolve to
        #   └── term        the raw string (UNIQUE across the whole table)
        #
        # e.g.  "Boston Red Sox" → concept_id 7
        #        "BoSox"         → concept_id 7  (same concept, different term)
        # ──────────────────────────────────────────────────────────────────────
        _vocab_cols = {r[1] for r in conn.execute("PRAGMA table_info(vocab)")}
        if not _vocab_cols:
            conn.execute("""
                CREATE TABLE vocab (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    concept_id INTEGER NOT NULL,
                    term       TEXT    NOT NULL UNIQUE
                )
            """)
        elif "concept_id" not in _vocab_cols:
            conn.execute("ALTER TABLE vocab ADD COLUMN concept_id INTEGER")
            conn.execute("UPDATE vocab SET concept_id = id WHERE concept_id IS NULL")
            try:
                for _ar in conn.execute(
                    "SELECT canonical_id, alias FROM vocab_aliases"
                ).fetchall():
                    _cr = conn.execute(
                        "SELECT concept_id FROM vocab WHERE id=?", (_ar[0],)
                    ).fetchone()
                    if _cr:
                        conn.execute(
                            "INSERT OR IGNORE INTO vocab (concept_id, term) VALUES (?,?)",
                            (_cr[0], _ar[1]),
                        )
            except Exception:
                pass
            conn.execute("DROP TABLE IF EXISTS vocab_aliases")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_vocab_concept ON vocab(concept_id)")

        # ── Relations ─────────────────────────────────────────────────────────
        # Pure triple store. All three positions are concept_ids from vocab.
        # No entity IDs, no relation-type IDs — just numbers from the vocab table.
        #
        #   relations
        #   ├── subject_concept_id    the "from" concept  (a vocab concept_id)
        #   ├── predicate_concept_id  the relationship kind (also a vocab concept_id)
        #   ├── object_concept_id     the "to" concept    (also a vocab concept_id)
        #   ├── state   0 = proposed  1 = active  2 = deprecated  3 = rejected  4 = pasttense
        #   └── score   0–255 confidence / weight
        #
        # e.g.  subject=7 ("Boston Red Sox")  predicate=12 ("member_of")  object=3 ("MLB")
        # ──────────────────────────────────────────────────────────────────────
        _rel_cols = {r[1] for r in conn.execute("PRAGMA table_info(relations)")}
        if not _rel_cols:
            conn.execute("""
                CREATE TABLE relations (
                    subject_concept_id   INTEGER NOT NULL,
                    predicate_concept_id INTEGER NOT NULL,
                    object_concept_id    INTEGER NOT NULL,
                    state                INTEGER NOT NULL DEFAULT 0,
                    score                INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (subject_concept_id, predicate_concept_id, object_concept_id)
                )
            """)
        elif "subject_concept_id" not in _rel_cols:
            conn.execute("""
                CREATE TABLE relations_new (
                    subject_concept_id   INTEGER NOT NULL,
                    predicate_concept_id INTEGER NOT NULL,
                    object_concept_id    INTEGER NOT NULL,
                    state                INTEGER NOT NULL DEFAULT 0,
                    score                INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (subject_concept_id, predicate_concept_id, object_concept_id)
                )
            """)
            try:
                for _r in conn.execute(
                    "SELECT source_entity_id, relation_type_id, target_entity_id, state, score"
                    " FROM relations"
                ).fetchall():
                    _src = conn.execute(
                        "SELECT name_concept_id FROM entities WHERE id=?",
                        (_r["source_entity_id"],),
                    ).fetchone()
                    _tgt = conn.execute(
                        "SELECT name_concept_id FROM entities WHERE id=?",
                        (_r["target_entity_id"],),
                    ).fetchone()
                    _rt = conn.execute(
                        "SELECT label FROM relation_types WHERE id=?",
                        (_r["relation_type_id"],),
                    ).fetchone()
                    if _src and _tgt and _rt:
                        _pred_cid = _get_or_create_vocab_term(conn, _rt["label"])
                        conn.execute(
                            "INSERT OR IGNORE INTO relations_new"
                            " (subject_concept_id, predicate_concept_id, object_concept_id,"
                            "  state, score)"
                            " VALUES (?,?,?,?,?)",
                            (
                                _src["name_concept_id"],
                                _pred_cid,
                                _tgt["name_concept_id"],
                                _r["state"],
                                _r["score"],
                            ),
                        )
            except Exception:
                pass
            conn.execute("DROP TABLE relations")
            conn.execute("ALTER TABLE relations_new RENAME TO relations")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rel_subject"
            " ON relations(subject_concept_id, score DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rel_object"
            " ON relations(object_concept_id, score DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rel_predicate"
            " ON relations(predicate_concept_id)"
        )

        for _tbl in (
            "relation_evidence",
            "relation_type_aliases",
            "relation_types",
            "entity_aliases",
            "entities",
        ):
            conn.execute(f"DROP TABLE IF EXISTS {_tbl}")

        _seed_predicates(conn)


def _seed_predicates(conn: sqlite3.Connection) -> None:
    """Ensure common predicate terms exist in vocab on first run."""
    PREDICATES = [
        "works_for", "founded", "part_of", "related_to", "opposed_to",
        "located_in", "successor_of", "predecessor_of", "alias_of",
        "owns",      "parent_of", "member_of",
    ]
    for p in PREDICATES:
        if not conn.execute("SELECT 1 FROM vocab WHERE term=?", (p,)).fetchone():
            concept_id = conn.execute(
                "SELECT COALESCE(MAX(concept_id), 0) + 1 FROM vocab"
            ).fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO vocab (concept_id, term) VALUES (?, ?)",
                (concept_id, p),
            )


# ---------------------------------------------------------------------------
# MARK: Status
# ---------------------------------------------------------------------------

def get_status() -> dict:
    with db_connection() as conn:
        concepts  = conn.execute("SELECT COUNT(DISTINCT concept_id) FROM vocab").fetchone()[0]
        terms     = conn.execute("SELECT COUNT(*) FROM vocab").fetchone()[0]
        relations = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        proposed  = conn.execute("SELECT COUNT(*) FROM relations WHERE state=0").fetchone()[0]
        active    = conn.execute("SELECT COUNT(*) FROM relations WHERE state=1").fetchone()[0]
        size      = _DB_PATH.stat().st_size if _DB_PATH.exists() else 0
    return {
        "ok": True,
        "concepts": concepts,
        "vocab_terms": terms,
        "relations": relations,
        "relations_proposed": proposed,
        "relations_active": active,
        "db_size_bytes": size,
    }


# ---------------------------------------------------------------------------
# MARK: Vocab internals
# ---------------------------------------------------------------------------

def _get_or_create_vocab_term(conn: sqlite3.Connection, term: str) -> int:
    """Return concept_id for term, creating a new single-term concept if not found."""
    row = conn.execute("SELECT concept_id FROM vocab WHERE term=?", (term,)).fetchone()
    if row:
        return row["concept_id"]
    concept_id = conn.execute(
        "SELECT COALESCE(MAX(concept_id), 0) + 1 FROM vocab"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO vocab (concept_id, term) VALUES (?, ?)", (concept_id, term)
    )
    return concept_id

# ── Everything below this line that was entity/alias/relation_type code is REMOVED ──
# (entities, entity_aliases, relation_types, relation_evidence tables no longer exist)






# ---------------------------------------------------------------------------
# MARK: Vocab
# ---------------------------------------------------------------------------

def list_vocab(q: Optional[str] = None, limit: int = 500) -> list[dict]:
    like = f"%{q}%" if q else None
    with db_connection() as conn:
        if like:
            rows = conn.execute(
                """
                SELECT id, concept_id, term,
                       (SELECT COUNT(*) FROM vocab v2
                        WHERE v2.concept_id = vocab.concept_id) - 1 AS alias_count
                FROM vocab
                WHERE term LIKE ?
                ORDER BY concept_id, term LIMIT ?
                """,
                (like, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, concept_id, term,
                       (SELECT COUNT(*) FROM vocab v2
                        WHERE v2.concept_id = vocab.concept_id) - 1 AS alias_count
                FROM vocab
                ORDER BY concept_id, term LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def add_vocab_term(term: str) -> dict:
    term = term.strip()
    if not term:
        raise ValueError("Term must not be empty")
    with db_connection() as conn:
        concept_id = conn.execute(
            "SELECT COALESCE(MAX(concept_id), 0) + 1 FROM vocab"
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO vocab (concept_id, term) VALUES (?, ?)", (concept_id, term)
        )
        return {"id": cur.lastrowid, "concept_id": concept_id, "term": term}


def delete_vocab_term(vocab_id: int) -> bool:
    with db_connection() as conn:
        cur = conn.execute("DELETE FROM vocab WHERE id=?", (vocab_id,))
        return cur.rowcount > 0


def delete_concept(concept_id: int) -> dict:
    """Delete a concept plus every relation that references it."""
    with db_connection() as conn:
        vocab_rows = conn.execute(
            "SELECT COUNT(*) FROM vocab WHERE concept_id=?", (concept_id,)
        ).fetchone()[0]
        if vocab_rows == 0:
            return {"deleted": False, "concept_id": concept_id, "deleted_vocab_terms": 0, "deleted_relations": 0}

        rel_cur = conn.execute(
            "DELETE FROM relations"
            " WHERE subject_concept_id=? OR predicate_concept_id=? OR object_concept_id=?",
            (concept_id, concept_id, concept_id),
        )
        vocab_cur = conn.execute("DELETE FROM vocab WHERE concept_id=?", (concept_id,))
        return {
            "deleted": True,
            "concept_id": concept_id,
            "deleted_vocab_terms": vocab_cur.rowcount,
            "deleted_relations": rel_cur.rowcount,
        }


def delete_orphaned_vocab_terms(dry_run: bool = False) -> dict:
    """Delete vocab concepts that are not referenced by any relation."""
    with db_connection() as conn:
        orphan_rows = conn.execute(
            """
            SELECT v.concept_id, COUNT(*) AS vocab_count, MIN(v.term) AS sample_term
            FROM vocab v
            WHERE NOT EXISTS (
                SELECT 1
                FROM relations r
                WHERE r.subject_concept_id = v.concept_id
                   OR r.predicate_concept_id = v.concept_id
                   OR r.object_concept_id = v.concept_id
            )
            GROUP BY v.concept_id
            ORDER BY MIN(v.term)
            """
        ).fetchall()
        orphan_concepts = len(orphan_rows)
        orphan_vocab_terms = sum(int(row["vocab_count"]) for row in orphan_rows)
        sample_terms = [row["sample_term"] for row in orphan_rows[:20]]

        if not orphan_rows or dry_run:
            return {
                "dry_run": dry_run,
                "orphaned_concepts": orphan_concepts,
                "orphaned_vocab_terms": orphan_vocab_terms,
                "deleted_concepts": 0 if dry_run else orphan_concepts,
                "deleted_vocab_terms": 0 if dry_run else orphan_vocab_terms,
                "sample_terms": sample_terms,
            }

        concept_ids = [row["concept_id"] for row in orphan_rows]
        placeholders = ",".join("?" for _ in concept_ids)
        conn.execute(f"DELETE FROM vocab WHERE concept_id IN ({placeholders})", concept_ids)
        return {
            "dry_run": False,
            "orphaned_concepts": orphan_concepts,
            "orphaned_vocab_terms": orphan_vocab_terms,
            "deleted_concepts": orphan_concepts,
            "deleted_vocab_terms": orphan_vocab_terms,
            "sample_terms": sample_terms,
        }


def rename_vocab_term(vocab_id: int, new_term: str) -> Optional[dict]:
    new_term = new_term.strip()
    if not new_term:
        raise ValueError("Term must not be empty")
    with db_connection() as conn:
        row = conn.execute(
            "SELECT id, concept_id, term FROM vocab WHERE id=?", (vocab_id,)
        ).fetchone()
        if row is None:
            return None
        existing = conn.execute(
            "SELECT id FROM vocab WHERE term=? AND id != ?", (new_term, vocab_id)
        ).fetchone()
        if existing:
            raise ValueError(f"Term '{new_term}' already exists")
        conn.execute("UPDATE vocab SET term=? WHERE id=?", (new_term, vocab_id))
        return {"id": vocab_id, "concept_id": row["concept_id"], "term": new_term}


def get_vocab_detail(vocab_id: int) -> Optional[dict]:
    with db_connection() as conn:
        row = conn.execute(
            "SELECT id, concept_id, term FROM vocab WHERE id=?", (vocab_id,)
        ).fetchone()
        if row is None:
            return None
        aliases = conn.execute(
            "SELECT id, term FROM vocab WHERE concept_id=? AND id!=? ORDER BY id",
            (row["concept_id"], vocab_id),
        ).fetchall()
        return {
            "id": row["id"],
            "concept_id": row["concept_id"],
            "term": row["term"],
            "aliases": [{"id": a["id"], "term": a["term"]} for a in aliases],
        }


def merge_vocab_term(vocab_id: int, target_id: int) -> Optional[dict]:
    """Make vocab_id an alias of the same concept as target_id.

    All relations that use the old concept_id are remapped to the target concept_id.
    Duplicate triples (same triple already exists under the target concept) have their
    scores accumulated rather than raising a conflict.
    """
    _UPSERT = """
        INSERT INTO relations
            (subject_concept_id, predicate_concept_id, object_concept_id, state, score)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(subject_concept_id, predicate_concept_id, object_concept_id)
        DO UPDATE SET score = MIN(255, score + excluded.score),
                      state = MAX(state, excluded.state)
    """
    with db_connection() as conn:
        target = conn.execute(
            "SELECT concept_id FROM vocab WHERE id=?", (target_id,)
        ).fetchone()
        if target is None:
            return None
        row = conn.execute(
            "SELECT id, concept_id, term FROM vocab WHERE id=?", (vocab_id,)
        ).fetchone()
        if row is None:
            return None

        old_cid = row["concept_id"]
        new_cid = target["concept_id"]

        if old_cid == new_cid:
            return {"id": vocab_id, "concept_id": new_cid, "term": row["term"],
                    "aliases": []}

        # Remap every vocab row in the old concept group to the new concept_id
        conn.execute(
            "UPDATE vocab SET concept_id=? WHERE concept_id=?", (new_cid, old_cid)
        )

        # Collect all relations touching old_cid (any position), remap, re-insert
        all_rels = conn.execute(
            "SELECT subject_concept_id, predicate_concept_id, object_concept_id, state, score"
            " FROM relations"
            " WHERE subject_concept_id=? OR predicate_concept_id=? OR object_concept_id=?",
            (old_cid, old_cid, old_cid),
        ).fetchall()
        conn.execute(
            "DELETE FROM relations"
            " WHERE subject_concept_id=? OR predicate_concept_id=? OR object_concept_id=?",
            (old_cid, old_cid, old_cid),
        )

        def _r(cid: int) -> int:
            return new_cid if cid == old_cid else cid

        for rel in all_rels:
            conn.execute(
                _UPSERT,
                (
                    _r(rel["subject_concept_id"]),
                    _r(rel["predicate_concept_id"]),
                    _r(rel["object_concept_id"]),
                    rel["state"],
                    rel["score"],
                ),
            )

        return {"id": vocab_id, "concept_id": new_cid, "term": row["term"]}


def unmerge_vocab_term(vocab_id: int) -> Optional[dict]:
    """Give this vocab row its own new concept_id, splitting it from its alias group.

    Relations are NOT moved — the term starts fresh with no connections.
    Raises ValueError if the term has no aliases (nothing to split from).
    """
    with db_connection() as conn:
        row = conn.execute(
            "SELECT id, concept_id, term FROM vocab WHERE id=?", (vocab_id,)
        ).fetchone()
        if row is None:
            return None
        sibling_count = conn.execute(
            "SELECT COUNT(*) FROM vocab WHERE concept_id=? AND id!=?",
            (row["concept_id"], vocab_id),
        ).fetchone()[0]
        if sibling_count == 0:
            raise ValueError("Term has no aliases — nothing to split from")
        new_concept_id = conn.execute(
            "SELECT COALESCE(MAX(concept_id), 0) + 1 FROM vocab"
        ).fetchone()[0]
        conn.execute(
            "UPDATE vocab SET concept_id=? WHERE id=?", (new_concept_id, vocab_id)
        )
        return {"id": vocab_id, "concept_id": new_concept_id, "term": row["term"]}


# ---------------------------------------------------------------------------
# MARK: Relations
# ---------------------------------------------------------------------------

_STATE_LABELS = {0: "proposed", 1: "active", 2: "deprecated", 3: "rejected", 4: "pasttense"}

_REL_JOIN = """
    JOIN vocab vs ON vs.id = (SELECT MIN(id) FROM vocab WHERE concept_id = r.subject_concept_id)
    JOIN vocab vp ON vp.id = (SELECT MIN(id) FROM vocab WHERE concept_id = r.predicate_concept_id)
    JOIN vocab vo ON vo.id = (SELECT MIN(id) FROM vocab WHERE concept_id = r.object_concept_id)
"""


def list_relations(
    limit: int = 100,
    offset: int = 0,
    state: Optional[int] = None,
    concept_id: Optional[int] = None,
) -> list[dict]:
    with db_connection() as conn:
        wheres = []
        params: list = []
        if state is not None:
            wheres.append("r.state=?")
            params.append(state)
        if concept_id is not None:
            wheres.append("(r.subject_concept_id=? OR r.object_concept_id=?)")
            params.extend([concept_id, concept_id])
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        rows = conn.execute(
            f"""
            SELECT r.subject_concept_id AS start_concept_id,   vs.term AS start_name,
                   r.predicate_concept_id AS connection_concept_id, vp.term AS connection_name,
                   r.object_concept_id AS end_concept_id,      vo.term AS end_name,
                   r.state, r.score
            FROM relations r
            {_REL_JOIN}
            {where_sql}
            ORDER BY r.score DESC, vs.term
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
        return [
            {**dict(r), "state_label": _STATE_LABELS.get(r["state"], "?")}
            for r in rows
        ]


def count_relations(state: Optional[int] = None, concept_id: Optional[int] = None) -> int:
    with db_connection() as conn:
        wheres = []
        params: list = []
        if state is not None:
            wheres.append("state=?")
            params.append(state)
        if concept_id is not None:
            wheres.append("(subject_concept_id=? OR object_concept_id=?)")
            params.extend([concept_id, concept_id])
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        return conn.execute(f"SELECT COUNT(*) FROM relations {where_sql}", params).fetchone()[0]


def upsert_relation(
    subject_concept_id: int,
    predicate_concept_id: int,
    object_concept_id: int,
    state: int = 0,
    score: int = 0,
) -> dict:
    score = max(0, min(255, score))
    state = max(0, min(4, state))
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO relations
                (subject_concept_id, predicate_concept_id, object_concept_id, state, score)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(subject_concept_id, predicate_concept_id, object_concept_id)
            DO UPDATE SET state=excluded.state,
                          score=MIN(255, score + excluded.score)
            """,
            (subject_concept_id, predicate_concept_id, object_concept_id, state, score),
        )
        return {
            "start_concept_id": subject_concept_id,
            "connection_concept_id": predicate_concept_id,
            "end_concept_id": object_concept_id,
            "state": state,
            "score": score,
        }


def update_relation_state_score(
    subject_concept_id: int,
    predicate_concept_id: int,
    object_concept_id: int,
    state: Optional[int] = None,
    score: Optional[int] = None,
) -> Optional[dict]:
    with db_connection() as conn:
        row = conn.execute(
            "SELECT state, score FROM relations"
            " WHERE subject_concept_id=? AND predicate_concept_id=? AND object_concept_id=?",
            (subject_concept_id, predicate_concept_id, object_concept_id),
        ).fetchone()
        if row is None:
            return None
        new_state = max(0, min(4, state)) if state is not None else row["state"]
        new_score = max(0, min(255, score)) if score is not None else row["score"]
        conn.execute(
            "UPDATE relations SET state=?, score=?"
            " WHERE subject_concept_id=? AND predicate_concept_id=? AND object_concept_id=?",
            (new_state, new_score, subject_concept_id, predicate_concept_id, object_concept_id),
        )
        return {
            "start_concept_id": subject_concept_id,
            "connection_concept_id": predicate_concept_id,
            "end_concept_id": object_concept_id,
            "state": new_state,
            "score": new_score,
        }


def delete_relation(
    subject_concept_id: int, predicate_concept_id: int, object_concept_id: int
) -> bool:
    with db_connection() as conn:
        cur = conn.execute(
            "DELETE FROM relations"
            " WHERE subject_concept_id=? AND predicate_concept_id=? AND object_concept_id=?",
            (subject_concept_id, predicate_concept_id, object_concept_id),
        )
        return cur.rowcount > 0


def delete_connection_by_name(start: str, connection: str, end: str) -> bool:
    """Delete a relation by string names. Returns False if any term is unknown or relation not found."""
    with db_connection() as conn:
        def _lookup(term: str) -> Optional[int]:
            row = conn.execute(
                "SELECT concept_id FROM vocab WHERE term=?", (term.strip(),)
            ).fetchone()
            return row["concept_id"] if row else None

        start_id = _lookup(start)
        conn_id  = _lookup(connection)
        end_id   = _lookup(end)
        if start_id is None or conn_id is None or end_id is None:
            return False
        cur = conn.execute(
            "DELETE FROM relations"
            " WHERE subject_concept_id=? AND predicate_concept_id=? AND object_concept_id=?",
            (start_id, conn_id, end_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# MARK: Graph traversal (API expand)
# ---------------------------------------------------------------------------

def expand_concept(concept_id: int, depth: int = 1, min_score: int = 0) -> dict:
    """Return a {nodes, edges} sub-graph within *depth* hops of *concept_id*."""
    visited: set[int] = set()
    edges: list[dict] = []
    frontier = {concept_id}

    with db_connection() as conn:
        for _ in range(depth):
            if not frontier:
                break
            next_frontier: set[int] = set()
            placeholders = ",".join("?" * len(frontier))
            rows = conn.execute(
                f"""
                SELECT r.subject_concept_id AS start_concept_id,   vs.term AS start_name,
                       r.predicate_concept_id AS connection_concept_id, vp.term AS connection_name,
                       r.object_concept_id AS end_concept_id,      vo.term AS end_name,
                       r.state, r.score
                FROM relations r
                {_REL_JOIN}
                WHERE (r.subject_concept_id IN ({placeholders})
                    OR r.object_concept_id IN ({placeholders}))
                  AND r.score >= ?
                ORDER BY r.score DESC
                """,
                list(frontier) + list(frontier) + [min_score],
            ).fetchall()
            seen_edges: set[tuple] = {
                (e["start_concept_id"], e["connection_concept_id"], e["end_concept_id"])
                for e in edges
            }
            for r in rows:
                key = (r["start_concept_id"], r["connection_concept_id"], r["end_concept_id"])
                if key not in seen_edges:
                    edges.append(dict(r))
                    seen_edges.add(key)
                for cid in (r["start_concept_id"], r["end_concept_id"]):
                    if cid not in visited:
                        next_frontier.add(cid)
            visited |= frontier
            frontier = next_frontier - visited

        all_concept_ids = visited | frontier
        nodes: list[dict] = []
        if all_concept_ids:
            placeholders = ",".join("?" * len(all_concept_ids))
            node_rows = conn.execute(
                f"""
                SELECT v.concept_id,
                       v.id AS vocab_id,
                       v.term AS name,
                       counts.alias_count AS alias_count
                FROM vocab v
                JOIN (
                    SELECT concept_id, MIN(id) AS vocab_id, COUNT(*) - 1 AS alias_count
                    FROM vocab
                    WHERE concept_id IN ({placeholders})
                    GROUP BY concept_id
                ) counts
                  ON counts.vocab_id = v.id
                ORDER BY v.term
                """,
                list(all_concept_ids),
            ).fetchall()
            nodes = [dict(r) for r in node_rows]

    return {"nodes": nodes, "edges": edges}


def expand_by_term(term: str, depth: int = 1, min_score: int = 0) -> dict:
    """String-based expand. Returns {query, matched, nodes, edges} with all names as strings."""
    term = term.strip()
    with db_connection() as conn:
        row = conn.execute(
            "SELECT DISTINCT concept_id FROM vocab WHERE term=? LIMIT 1", (term,)
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT DISTINCT concept_id FROM vocab WHERE term LIKE ? LIMIT 1",
                (f"%{term}%",),
            ).fetchone()
    if row is None:
        return {"query": term, "matched": False, "nodes": [], "edges": []}
    result = expand_concept(row["concept_id"], depth=depth, min_score=min_score)
    return {"query": term, "matched": True, **result}


def upsert_connection_by_name(
    start: str, connection: str, end: str, state: int = 0, score: int = 0
) -> dict:
    """Create/reinforce a connection using string names. Vocab entries created on demand."""
    start, connection, end = start.strip(), connection.strip(), end.strip()
    if not start:
        raise ValueError("start must not be empty")
    if not connection:
        raise ValueError("connection must not be empty")
    if not end:
        raise ValueError("end must not be empty")
    if _is_blacklisted(connection):
        raise ValueError(f"'{connection}' is a blacklisted term")
    score = max(0, min(255, score))
    state = max(0, min(4, state))
    with db_connection() as conn:
        start_id = _get_or_create_vocab_term(conn, start)
        conn_id = _get_or_create_vocab_term(conn, connection)
        end_id = _get_or_create_vocab_term(conn, end)
        conn.execute(
            """
            INSERT INTO relations
                (subject_concept_id, predicate_concept_id, object_concept_id, state, score)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(subject_concept_id, predicate_concept_id, object_concept_id)
            DO UPDATE SET state=excluded.state,
                          score=MIN(255, score + excluded.score)
            """,
            (start_id, conn_id, end_id, state, score),
        )
    return {"start": start, "connection": connection, "end": end, "state": state, "score": score}
