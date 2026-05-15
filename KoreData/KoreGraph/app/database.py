# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# SQLite database layer for KoreGraph.
#
# Schema (5 tables):
#   entities        -- named entities (person, org, concept, place, event, …)
#   entity_aliases  -- alternative names / synonyms mapping to a canonical entity
#   relation_types  -- labelled vocabulary of relationship kinds
#   relations       -- core adjacency list (12-byte hot rows: 4+2+4+1+1)
#   relation_evidence -- optional citations for relations
#
# Seed data:
#   _seed_relation_types() inserts the built-in vocabulary on first run.
#   RELATION_BLACKLIST is a frozenset of words that must never become entity names
#   or relation type labels (stop words, prepositions, articles).
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

#: Words that must never become entity names or relation type labels.
#: Edit this set to extend it; it is checked before every insert.
RELATION_BLACKLIST: frozenset[str] = frozenset({
    "is", "are", "was", "were", "be", "been", "being",
    "a", "an", "the",
    "in", "on", "at", "to", "of", "for", "by", "as", "with",
    "from", "into", "onto", "upon", "about", "over", "under",
    "and", "or", "but", "not", "nor", "so", "yet",
    "it", "its", "this", "that", "these", "those",
    "he", "she", "they", "we", "you", "i",
})


def _is_blacklisted(word: str) -> bool:
    return word.strip().lower() in RELATION_BLACKLIST


# ---------------------------------------------------------------------------
# MARK: Schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name_vocab_id INTEGER NOT NULL,
                type_vocab_id INTEGER,
                description   TEXT,
                created_at    TEXT DEFAULT (datetime('now','utc')),
                UNIQUE(name_vocab_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entity_aliases (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id   INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                alias       TEXT    NOT NULL,
                UNIQUE (alias)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS relation_types (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                label    TEXT    NOT NULL UNIQUE,
                directed INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS relations (
                source_entity_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                relation_type_id  INTEGER NOT NULL REFERENCES relation_types(id) ON DELETE CASCADE,
                target_entity_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                state             INTEGER NOT NULL DEFAULT 0,
                score             INTEGER NOT NULL DEFAULT 0,
                UNIQUE (source_entity_id, relation_type_id, target_entity_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS relation_evidence (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                source_entity_id INTEGER NOT NULL,
                relation_type_id INTEGER NOT NULL,
                target_entity_id INTEGER NOT NULL,
                evidence         TEXT,
                created_at       TEXT DEFAULT (datetime('now','utc')),
                FOREIGN KEY (source_entity_id, relation_type_id, target_entity_id)
                    REFERENCES relations(source_entity_id, relation_type_id, target_entity_id)
                    ON DELETE CASCADE
            )
        """)
        # Indexes for traversal speed
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_source ON relations(source_entity_id, score DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rel_target ON relations(target_entity_id, score DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alias ON entity_aliases(alias)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS relation_type_aliases (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                relation_type_id INTEGER NOT NULL REFERENCES relation_types(id) ON DELETE CASCADE,
                alias            TEXT    NOT NULL,
                UNIQUE (alias)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rt_alias ON relation_type_aliases(alias)")
        # vocab: flat alias model — each row has a concept_id shared by all its aliases
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
            # migrate old (id, term) schema: assign concept_id = id, fold vocab_aliases in
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
        # entities: migrate from text columns to vocab-linked IDs if needed
        _ent_cols = {r[1] for r in conn.execute("PRAGMA table_info(entities)")}
        if _ent_cols and "name_vocab_id" not in _ent_cols:
            conn.execute("""
                CREATE TABLE entities_new (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name_vocab_id INTEGER NOT NULL,
                    type_vocab_id INTEGER,
                    description   TEXT,
                    created_at    TEXT DEFAULT (datetime('now','utc')),
                    UNIQUE(name_vocab_id)
                )
            """)
            for _er in conn.execute(
                "SELECT id, name, type, description, created_at FROM entities"
            ).fetchall():
                _name_vid = _get_or_create_vocab_term(conn, _er["name"])
                _type_vid = (
                    _get_or_create_vocab_term(conn, _er["type"].strip())
                    if _er["type"] and _er["type"].strip()
                    else None
                )
                conn.execute(
                    "INSERT INTO entities_new"
                    " (id, name_vocab_id, type_vocab_id, description, created_at)"
                    " VALUES (?,?,?,?,?)",
                    (_er["id"], _name_vid, _type_vid, _er["description"], _er["created_at"]),
                )
            conn.execute("DROP TABLE entities")
            conn.execute("ALTER TABLE entities_new RENAME TO entities")
        _seed_relation_types(conn)


def _seed_relation_types(conn: sqlite3.Connection) -> None:
    """Insert the canonical starter vocabulary on first run. Edit this list to change it."""
    SEED = [
        ("works_for",      1),
        ("founded",        1),
        ("part_of",        1),
        ("related_to",     0),
        ("opposed_to",     0),
        ("located_in",     1),
        ("successor_of",   1),
        ("predecessor_of", 1),
        ("alias_of",       0),
        ("owns",           1),
        ("parent_of",      1),
        ("member_of",      1),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO relation_types (label, directed) VALUES (?, ?)",
        SEED,
    )


# ---------------------------------------------------------------------------
# MARK: Status
# ---------------------------------------------------------------------------

def get_status() -> dict:
    with db_connection() as conn:
        entities  = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        relations = conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        proposed  = conn.execute("SELECT COUNT(*) FROM relations WHERE state=0").fetchone()[0]
        active    = conn.execute("SELECT COUNT(*) FROM relations WHERE state=1").fetchone()[0]
        size      = _DB_PATH.stat().st_size if _DB_PATH.exists() else 0
    return {
        "ok": True,
        "entities": entities,
        "relations": relations,
        "relations_proposed": proposed,
        "relations_active": active,
        "db_size_bytes": size,
    }


# ---------------------------------------------------------------------------
# MARK: Entities
# ---------------------------------------------------------------------------

def _get_or_create_vocab_term(conn: sqlite3.Connection, term: str) -> int:
    """Return vocab.id for term, creating a new single-term concept if not found."""
    row = conn.execute("SELECT id FROM vocab WHERE term=?", (term,)).fetchone()
    if row:
        return row["id"]
    concept_id = conn.execute(
        "SELECT COALESCE(MAX(concept_id), 0) + 1 FROM vocab"
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO vocab (concept_id, term) VALUES (?, ?)", (concept_id, term)
    )
    return cur.lastrowid


def list_entities(limit: int = 100, offset: int = 0, q: Optional[str] = None) -> list[dict]:
    with db_connection() as conn:
        if q:
            like = f"%{q}%"
            rows = conn.execute(
                """
                SELECT e.id, vn.term AS name, vt.term AS type,
                       e.name_vocab_id, e.type_vocab_id, e.description, e.created_at,
                       (SELECT COUNT(*) FROM entity_aliases WHERE entity_id = e.id) AS alias_count,
                       (SELECT COUNT(*) FROM relations
                        WHERE source_entity_id = e.id OR target_entity_id = e.id) AS relation_count
                FROM entities e
                JOIN vocab vn ON vn.id = e.name_vocab_id
                LEFT JOIN vocab vt ON vt.id = e.type_vocab_id
                WHERE vn.term LIKE ? OR e.description LIKE ?
                   OR EXISTS (SELECT 1 FROM entity_aliases WHERE entity_id = e.id AND alias LIKE ?)
                ORDER BY vn.term
                LIMIT ? OFFSET ?
                """,
                (like, like, like, limit, offset),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT e.id, vn.term AS name, vt.term AS type,
                       e.name_vocab_id, e.type_vocab_id, e.description, e.created_at,
                       (SELECT COUNT(*) FROM entity_aliases WHERE entity_id = e.id) AS alias_count,
                       (SELECT COUNT(*) FROM relations
                        WHERE source_entity_id = e.id OR target_entity_id = e.id) AS relation_count
                FROM entities e
                JOIN vocab vn ON vn.id = e.name_vocab_id
                LEFT JOIN vocab vt ON vt.id = e.type_vocab_id
                ORDER BY vn.term
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        entities = [dict(r) for r in rows]
        if entities:
            ids = [e["id"] for e in entities]
            placeholders = ",".join("?" * len(ids))
            alias_rows = conn.execute(
                f"SELECT entity_id, alias FROM entity_aliases WHERE entity_id IN ({placeholders}) ORDER BY alias",
                ids,
            ).fetchall()
            alias_map: dict = {}
            for ar in alias_rows:
                alias_map.setdefault(ar["entity_id"], []).append(ar["alias"])
            for e in entities:
                e["aliases"] = alias_map.get(e["id"], [])
        return entities


def count_entities(q: Optional[str] = None) -> int:
    with db_connection() as conn:
        if q:
            like = f"%{q}%"
            return conn.execute(
                """
                SELECT COUNT(*) FROM entities e
                JOIN vocab vn ON vn.id = e.name_vocab_id
                WHERE vn.term LIKE ? OR e.description LIKE ?
                   OR EXISTS (SELECT 1 FROM entity_aliases WHERE entity_id = e.id AND alias LIKE ?)
                """,
                (like, like, like),
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]


def get_entity(entity_id: int) -> Optional[dict]:
    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT e.id, vn.term AS name, vt.term AS type,
                   e.name_vocab_id, e.type_vocab_id, e.description, e.created_at
            FROM entities e
            JOIN vocab vn ON vn.id = e.name_vocab_id
            LEFT JOIN vocab vt ON vt.id = e.type_vocab_id
            WHERE e.id=?
            """,
            (entity_id,),
        ).fetchone()
        if row is None:
            return None
        entity = dict(row)
        entity["aliases"] = [
            dict(r) for r in conn.execute(
                "SELECT id, alias FROM entity_aliases WHERE entity_id=? ORDER BY alias",
                (entity_id,),
            ).fetchall()
        ]
        entity["relations"] = _get_entity_relations(conn, entity_id)
        return entity


def _get_entity_relations(conn: sqlite3.Connection, entity_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT r.source_entity_id, vs.term AS source_name,
               rt.label AS relation_type,
               r.target_entity_id, vt2.term AS target_name,
               r.state, r.score
        FROM relations r
        JOIN entities es ON es.id = r.source_entity_id
        JOIN vocab vs ON vs.id = es.name_vocab_id
        JOIN entities et ON et.id = r.target_entity_id
        JOIN vocab vt2 ON vt2.id = et.name_vocab_id
        JOIN relation_types rt ON rt.id = r.relation_type_id
        WHERE r.source_entity_id=? OR r.target_entity_id=?
        ORDER BY r.score DESC
        LIMIT 200
        """,
        (entity_id, entity_id),
    ).fetchall()
    return [dict(r) for r in rows]


def create_entity(name: str, type_: Optional[str] = None, description: Optional[str] = None) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("Name must not be empty")
    with db_connection() as conn:
        name_vocab_id = _get_or_create_vocab_term(conn, name)
        type_vocab_id = (
            _get_or_create_vocab_term(conn, type_.strip())
            if type_ and type_.strip()
            else None
        )
        cur = conn.execute(
            "INSERT INTO entities (name_vocab_id, type_vocab_id, description) VALUES (?, ?, ?)",
            (name_vocab_id, type_vocab_id, description),
        )
        return {
            "id": cur.lastrowid,
            "name": name,
            "name_vocab_id": name_vocab_id,
            "type": type_,
            "type_vocab_id": type_vocab_id,
            "description": description,
        }


def update_entity(entity_id: int, name: Optional[str] = None,
                  type_: Optional[str] = None, description: Optional[str] = None) -> Optional[dict]:
    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT e.id, vn.term AS name, vt.term AS type,
                   e.name_vocab_id, e.type_vocab_id, e.description
            FROM entities e
            JOIN vocab vn ON vn.id = e.name_vocab_id
            LEFT JOIN vocab vt ON vt.id = e.type_vocab_id
            WHERE e.id=?
            """,
            (entity_id,),
        ).fetchone()
        if row is None:
            return None
        new_name_vid = (
            _get_or_create_vocab_term(conn, name.strip())
            if name and name.strip()
            else row["name_vocab_id"]
        )
        new_type_vid = (
            _get_or_create_vocab_term(conn, type_.strip())
            if type_ is not None and type_.strip()
            else (None if type_ is not None else row["type_vocab_id"])
        )
        new_desc = description if description is not None else row["description"]
        conn.execute(
            "UPDATE entities SET name_vocab_id=?, type_vocab_id=?, description=? WHERE id=?",
            (new_name_vid, new_type_vid, new_desc, entity_id),
        )
        vn_row = conn.execute("SELECT term FROM vocab WHERE id=?", (new_name_vid,)).fetchone()
        vt_row = conn.execute("SELECT term FROM vocab WHERE id=?", (new_type_vid,)).fetchone() if new_type_vid else None
        return {
            "id": entity_id,
            "name": vn_row["term"] if vn_row else name,
            "name_vocab_id": new_name_vid,
            "type": vt_row["term"] if vt_row else None,
            "type_vocab_id": new_type_vid,
            "description": new_desc,
        }


def delete_entity(entity_id: int) -> bool:
    with db_connection() as conn:
        cur = conn.execute("DELETE FROM entities WHERE id=?", (entity_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# MARK: Aliases
# ---------------------------------------------------------------------------

def add_alias(entity_id: int, alias: str) -> dict:
    with db_connection() as conn:
        cur = conn.execute(
            "INSERT INTO entity_aliases (entity_id, alias) VALUES (?, ?)",
            (entity_id, alias.strip()),
        )
        return {"id": cur.lastrowid, "entity_id": entity_id, "alias": alias.strip()}


def delete_alias(alias_id: int) -> bool:
    with db_connection() as conn:
        cur = conn.execute("DELETE FROM entity_aliases WHERE id=?", (alias_id,))
        return cur.rowcount > 0


def resolve_alias(term: str) -> Optional[dict]:
    """Return entity matching name or alias, or None."""
    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT e.id, vn.term AS name, vt.term AS type,
                   e.name_vocab_id, e.type_vocab_id, e.description
            FROM entities e
            JOIN vocab vn ON vn.id = e.name_vocab_id
            LEFT JOIN vocab vt ON vt.id = e.type_vocab_id
            WHERE vn.term=? COLLATE NOCASE
            """,
            (term,),
        ).fetchone()
        if row:
            return dict(row)
        row = conn.execute(
            """
            SELECT e.id, vn.term AS name, vt.term AS type,
                   e.name_vocab_id, e.type_vocab_id, e.description
            FROM entities e
            JOIN vocab vn ON vn.id = e.name_vocab_id
            LEFT JOIN vocab vt ON vt.id = e.type_vocab_id
            JOIN entity_aliases a ON a.entity_id = e.id
            WHERE a.alias=? COLLATE NOCASE
            """,
            (term,),
        ).fetchone()
        return dict(row) if row else None


def search_entities(q: str, limit: int = 20) -> list[dict]:
    like = f"%{q}%"
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT e.id, vn.term AS name, vt.term AS type, e.description
            FROM entities e
            JOIN vocab vn ON vn.id = e.name_vocab_id
            LEFT JOIN vocab vt ON vt.id = e.type_vocab_id
            LEFT JOIN entity_aliases a ON a.entity_id = e.id
            WHERE vn.term LIKE ? OR a.alias LIKE ?
            ORDER BY vn.term
            LIMIT ?
            """,
            (like, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# MARK: Relation types
# ---------------------------------------------------------------------------

def list_relation_types() -> list[dict]:
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT rt.id, rt.label, rt.directed,
                   COUNT(r.relation_type_id) AS relation_count
            FROM relation_types rt
            LEFT JOIN relations r ON r.relation_type_id = rt.id
            GROUP BY rt.id
            ORDER BY rt.label
            """
        ).fetchall()
        rts = [dict(r) for r in rows]
        if rts:
            ids = [rt["id"] for rt in rts]
            placeholders = ",".join("?" * len(ids))
            alias_rows = conn.execute(
                f"SELECT id, relation_type_id, alias FROM relation_type_aliases"
                f" WHERE relation_type_id IN ({placeholders}) ORDER BY alias",
                ids,
            ).fetchall()
            alias_map: dict = {}
            for ar in alias_rows:
                alias_map.setdefault(ar["relation_type_id"], []).append(
                    {"id": ar["id"], "alias": ar["alias"]}
                )
            for rt in rts:
                rt["aliases"] = alias_map.get(rt["id"], [])
        return rts


def create_relation_type(label: str, directed: bool = True) -> dict:
    label = label.strip().lower().replace(" ", "_")
    if _is_blacklisted(label):
        raise ValueError(f"Label {label!r} is blacklisted")
    with db_connection() as conn:
        cur = conn.execute(
            "INSERT INTO relation_types (label, directed) VALUES (?, ?)",
            (label, int(directed)),
        )
        return {"id": cur.lastrowid, "label": label, "directed": int(directed)}


def delete_relation_type(rt_id: int) -> bool:
    with db_connection() as conn:
        cur = conn.execute("DELETE FROM relation_types WHERE id=?", (rt_id,))
        return cur.rowcount > 0


def add_relation_type_alias(rt_id: int, alias: str) -> dict:
    with db_connection() as conn:
        cur = conn.execute(
            "INSERT INTO relation_type_aliases (relation_type_id, alias) VALUES (?, ?)",
            (rt_id, alias.strip()),
        )
        return {"id": cur.lastrowid, "relation_type_id": rt_id, "alias": alias.strip()}


def delete_relation_type_alias(alias_id: int) -> bool:
    with db_connection() as conn:
        cur = conn.execute("DELETE FROM relation_type_aliases WHERE id=?", (alias_id,))
        return cur.rowcount > 0


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


def get_vocab_detail(vocab_id: int) -> Optional[dict]:
    with db_connection() as conn:
        row = conn.execute(
            "SELECT id, concept_id, term FROM vocab WHERE id=?", (vocab_id,)
        ).fetchone()
        if row is None:
            return None
        siblings = conn.execute(
            "SELECT id, term FROM vocab WHERE concept_id=? ORDER BY term",
            (row["concept_id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "concept_id": row["concept_id"],
            "term": row["term"],
            "siblings": [dict(s) for s in siblings],
        }


def add_vocab_alias(canonical_id: int, alias: str) -> dict:
    alias = alias.strip()
    if not alias:
        raise ValueError("Alias must not be empty")
    with db_connection() as conn:
        row = conn.execute(
            "SELECT concept_id FROM vocab WHERE id=?", (canonical_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Vocab term {canonical_id} not found")
        concept_id = row["concept_id"]
        cur = conn.execute(
            "INSERT INTO vocab (concept_id, term) VALUES (?, ?)", (concept_id, alias)
        )
        return {"id": cur.lastrowid, "concept_id": concept_id, "term": alias}


def delete_vocab_alias(alias_id: int) -> bool:
    return delete_vocab_term(alias_id)


def merge_vocab_terms(canonical_id: int, merge_id: int) -> dict:
    if canonical_id == merge_id:
        raise ValueError("Cannot merge a term with itself")
    with db_connection() as conn:
        canonical_row = conn.execute(
            "SELECT concept_id FROM vocab WHERE id=?", (canonical_id,)
        ).fetchone()
        merge_row = conn.execute(
            "SELECT concept_id FROM vocab WHERE id=?", (merge_id,)
        ).fetchone()
        if canonical_row is None:
            raise ValueError(f"Canonical vocab term {canonical_id} not found")
        if merge_row is None:
            raise ValueError(f"Merge vocab term {merge_id} not found")
        canonical_concept = canonical_row["concept_id"]
        merge_concept = merge_row["concept_id"]
        if canonical_concept == merge_concept:
            raise ValueError("Terms are already in the same concept group")
        conn.execute(
            "UPDATE vocab SET concept_id=? WHERE concept_id=?",
            (canonical_concept, merge_concept),
        )
        return {"ok": True, "concept_id": canonical_concept}


# ---------------------------------------------------------------------------
# MARK: Relations
# ---------------------------------------------------------------------------

_STATE_LABELS = {0: "proposed", 1: "active", 2: "deprecated", 3: "rejected"}


def list_relations(
    limit: int = 100,
    offset: int = 0,
    state: Optional[int] = None,
    entity_id: Optional[int] = None,
) -> list[dict]:
    with db_connection() as conn:
        wheres = []
        params: list = []
        if state is not None:
            wheres.append("r.state=?")
            params.append(state)
        if entity_id is not None:
            wheres.append("(r.source_entity_id=? OR r.target_entity_id=?)")
            params.extend([entity_id, entity_id])
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        rows = conn.execute(
            f"""
            SELECT r.source_entity_id, vs.term AS source_name,
                   r.relation_type_id, rt.label AS relation_type,
                   r.target_entity_id, vt.term AS target_name,
                   r.state, r.score
            FROM relations r
            JOIN entities es ON es.id = r.source_entity_id
            JOIN vocab vs ON vs.id = es.name_vocab_id
            JOIN entities et ON et.id = r.target_entity_id
            JOIN vocab vt ON vt.id = et.name_vocab_id
            JOIN relation_types rt ON rt.id = r.relation_type_id
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


def count_relations(state: Optional[int] = None, entity_id: Optional[int] = None) -> int:
    with db_connection() as conn:
        wheres = []
        params: list = []
        if state is not None:
            wheres.append("state=?")
            params.append(state)
        if entity_id is not None:
            wheres.append("(source_entity_id=? OR target_entity_id=?)")
            params.extend([entity_id, entity_id])
        where_sql = ("WHERE " + " AND ".join(wheres)) if wheres else ""
        return conn.execute(f"SELECT COUNT(*) FROM relations {where_sql}", params).fetchone()[0]


def upsert_relation(
    source_id: int,
    relation_type_id: int,
    target_id: int,
    state: int = 0,
    score: int = 0,
) -> dict:
    score = max(0, min(255, score))
    state = max(0, min(3, state))
    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO relations (source_entity_id, relation_type_id, target_entity_id, state, score)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_entity_id, relation_type_id, target_entity_id)
            DO UPDATE SET state=excluded.state, score=excluded.score
            """,
            (source_id, relation_type_id, target_id, state, score),
        )
        return {
            "source_entity_id": source_id,
            "relation_type_id": relation_type_id,
            "target_entity_id": target_id,
            "state": state,
            "score": score,
        }


def update_relation_state_score(
    source_id: int, relation_type_id: int, target_id: int,
    state: Optional[int] = None, score: Optional[int] = None,
) -> Optional[dict]:
    with db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM relations WHERE source_entity_id=? AND relation_type_id=? AND target_entity_id=?",
            (source_id, relation_type_id, target_id),
        ).fetchone()
        if row is None:
            return None
        new_state = max(0, min(3, state)) if state is not None else row["state"]
        new_score = max(0, min(255, score)) if score is not None else row["score"]
        conn.execute(
            "UPDATE relations SET state=?, score=? WHERE source_entity_id=? AND relation_type_id=? AND target_entity_id=?",
            (new_state, new_score, source_id, relation_type_id, target_id),
        )
        return {"source_entity_id": source_id, "relation_type_id": relation_type_id,
                "target_entity_id": target_id, "state": new_state, "score": new_score}


def delete_relation(source_id: int, relation_type_id: int, target_id: int) -> bool:
    with db_connection() as conn:
        cur = conn.execute(
            "DELETE FROM relations WHERE source_entity_id=? AND relation_type_id=? AND target_entity_id=?",
            (source_id, relation_type_id, target_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# MARK: Evidence
# ---------------------------------------------------------------------------

def add_evidence(source_id: int, relation_type_id: int, target_id: int, evidence: str) -> dict:
    with db_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO relation_evidence
                (source_entity_id, relation_type_id, target_entity_id, evidence)
            VALUES (?, ?, ?, ?)
            """,
            (source_id, relation_type_id, target_id, evidence.strip()),
        )
        return {"id": cur.lastrowid, "evidence": evidence.strip()}


def list_evidence(source_id: int, relation_type_id: int, target_id: int) -> list[dict]:
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, evidence, created_at FROM relation_evidence
            WHERE source_entity_id=? AND relation_type_id=? AND target_entity_id=?
            ORDER BY created_at
            """,
            (source_id, relation_type_id, target_id),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# MARK: Graph traversal (API expand)
# ---------------------------------------------------------------------------

def expand_entity(entity_id: int, depth: int = 1, min_score: int = 0) -> dict:
    """Return a {nodes, edges} sub-graph within *depth* hops of *entity_id*."""
    visited_nodes: set[int] = set()
    edges: list[dict] = []
    frontier = {entity_id}

    with db_connection() as conn:
        for _ in range(depth):
            if not frontier:
                break
            next_frontier: set[int] = set()
            placeholders = ",".join("?" * len(frontier))
            rows = conn.execute(
                f"""
                SELECT r.source_entity_id, vs.term AS source_name,
                       r.relation_type_id, rt.label AS relation_type,
                       r.target_entity_id, vt.term AS target_name,
                       r.state, r.score
                FROM relations r
                JOIN entities es ON es.id = r.source_entity_id
                JOIN vocab vs ON vs.id = es.name_vocab_id
                JOIN entities et ON et.id = r.target_entity_id
                JOIN vocab vt ON vt.id = et.name_vocab_id
                JOIN relation_types rt ON rt.id = r.relation_type_id
                WHERE (r.source_entity_id IN ({placeholders})
                    OR r.target_entity_id IN ({placeholders}))
                  AND r.score >= ?
                ORDER BY r.score DESC
                """,
                list(frontier) + list(frontier) + [min_score],
            ).fetchall()
            for r in rows:
                edge_key = (r["source_entity_id"], r["relation_type_id"], r["target_entity_id"])
                if not any(
                    e["source_entity_id"] == edge_key[0]
                    and e["relation_type_id"] == edge_key[1]
                    and e["target_entity_id"] == edge_key[2]
                    for e in edges
                ):
                    edges.append(dict(r))
                for nid in (r["source_entity_id"], r["target_entity_id"]):
                    if nid not in visited_nodes:
                        next_frontier.add(nid)
            visited_nodes |= frontier
            frontier = next_frontier - visited_nodes

        all_node_ids = visited_nodes | frontier
        nodes = []
        if all_node_ids:
            placeholders = ",".join("?" * len(all_node_ids))
            node_rows = conn.execute(
                f"""
                SELECT e.id, vn.term AS name, vt.term AS type, e.description
                FROM entities e
                JOIN vocab vn ON vn.id = e.name_vocab_id
                LEFT JOIN vocab vt ON vt.id = e.type_vocab_id
                WHERE e.id IN ({placeholders})
                """,
                list(all_node_ids),
            ).fetchall()
            nodes = [dict(r) for r in node_rows]

    return {"nodes": nodes, "edges": edges}
