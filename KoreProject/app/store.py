from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ProjectError(Exception):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


TABLES = {
    "project": "projects", "statement": "statements", "ticket": "tickets",
    "board": "boards", "lane": "lanes", "section": "sections",
}
DEFINITION_KINDS = {"project", "statement", "section"}
DEFAULTS: dict[str, dict[str, Any]] = {
    "project": {"key": ""},
    "statement": {"kind": "requirement", "state": "draft", "owner": "", "section_id": None},
    "section": {"kind": "requirement", "parent_id": None, "position": 0},
    "ticket": {"parent_id": None, "lane_id": None, "priority": "normal", "owner": "", "labels": "", "position": 0},
    "board": {},
    "lane": {"board_id": "", "category": "open", "position": 0},
}
COMMON = """
    id TEXT PRIMARY KEY, ref TEXT NOT NULL UNIQUE, title TEXT NOT NULL, body TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
"""
HISTORY_SCHEMA = """
    CREATE TABLE IF NOT EXISTS activity (
        id INTEGER PRIMARY KEY, record_id TEXT NOT NULL, actor TEXT NOT NULL,
        action TEXT NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS activity_record ON activity(record_id, id);
    CREATE TABLE IF NOT EXISTS comments (
        id TEXT PRIMARY KEY, record_id TEXT NOT NULL, actor TEXT NOT NULL,
        body TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS links (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        source_kind TEXT NOT NULL, source_id TEXT NOT NULL,
        relation TEXT NOT NULL, target_kind TEXT NOT NULL, target_id TEXT NOT NULL,
        note TEXT NOT NULL, created_at TEXT NOT NULL,
        UNIQUE(source_kind, source_id, relation, target_kind, target_id)
    );
    CREATE INDEX IF NOT EXISTS links_project ON links(project_id);
    CREATE TABLE IF NOT EXISTS requests (
        key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL
    );
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectStore:
    """Two independent stores; all service mutations serialize through this owner."""

    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()

    def _path(self, kind: str) -> Path:
        if kind not in TABLES:
            raise ProjectError("Unknown record kind")
        return self.root / ("projects.db" if kind in DEFINITION_KINDS else "tickets.db")

    @contextmanager
    def connection(self, kind: str, *, write: bool = False):
        path = self._path(kind)
        if not path.exists():
            raise ProjectError(f"{path.name} is unavailable; restore it or explicitly reset tickets.", 503)
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=rw", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            version = 7 if kind in DEFINITION_KINDS else 3
            if conn.execute("PRAGMA user_version").fetchone()[0] != version:
                raise ProjectError("Database schema is unsupported", 503)
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            first_install = not (self.root / "projects.db").exists()
            if first_install and (self.root / "tickets.db").exists():
                raise ProjectError("Project database is missing; restore it before starting.", 503)
            self._initialize_projects()
            # Missing operational data after installation is not an empty backlog.
            if first_install or (self.root / "tickets.db").exists():
                try:
                    self._initialize_tickets()
                except (sqlite3.DatabaseError, ProjectError):
                    logging.getLogger("koreproject").exception("Ticket storage unavailable; definitions remain available")
            with self.connection("project") as conn:
                pending = conn.execute("SELECT project_id FROM project_deletions").fetchall()
            for row in pending:
                try:
                    self._finish_project_deletion(row["project_id"])
                except (sqlite3.DatabaseError, ProjectError):
                    logging.getLogger("koreproject").exception("Project deletion pending; retry when storage is available")
            with self.connection("project") as conn:
                pending_sections = conn.execute("SELECT section_id FROM section_deletions").fetchall()
            for row in pending_sections:
                try:
                    self._finish_section_deletion(row["section_id"])
                except (sqlite3.DatabaseError, ProjectError):
                    logging.getLogger("koreproject").exception("Section deletion pending; retry when storage is available")

    def _initialize_projects(self) -> None:
        with closing(sqlite3.connect(self.root / "projects.db")) as conn, conn:
            self._check_schema(conn, latest=7)
            conn.row_factory = sqlite3.Row
            previous_version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS projects ({COMMON}, key TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS sections (
                    {COMMON}, project_id TEXT NOT NULL REFERENCES projects(id),
                    parent_id TEXT REFERENCES sections(id), position INTEGER NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'requirement' CHECK(kind IN ('requirement','design'))
                );
                CREATE INDEX IF NOT EXISTS sections_project ON sections(project_id);
                CREATE TABLE IF NOT EXISTS statements (
                    {COMMON}, project_id TEXT NOT NULL REFERENCES projects(id),
                    kind TEXT NOT NULL CHECK(kind IN ('requirement','design')),
                    state TEXT NOT NULL, owner TEXT NOT NULL,
                    section_id TEXT REFERENCES sections(id)
                );
                CREATE INDEX IF NOT EXISTS statements_project ON statements(project_id);
                CREATE TABLE IF NOT EXISTS project_deletions (
                    project_id TEXT PRIMARY KEY REFERENCES projects(id), created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS section_deletions (
                    section_id TEXT PRIMARY KEY REFERENCES sections(id),
                    project_id TEXT NOT NULL REFERENCES projects(id)
                );
                CREATE TABLE IF NOT EXISTS requirement_counters (
                    project_id TEXT PRIMARY KEY REFERENCES projects(id),
                    next_number INTEGER NOT NULL CHECK(next_number > 0)
                );
                CREATE TABLE IF NOT EXISTS design_counters (
                    project_id TEXT PRIMARY KEY REFERENCES projects(id),
                    next_number INTEGER NOT NULL CHECK(next_number > 0)
                );
                {HISTORY_SCHEMA}
            """)
            conn.execute("BEGIN IMMEDIATE")
            section_columns = {row[1] for row in conn.execute("PRAGMA table_info(sections)")}
            if "kind" not in section_columns:
                conn.execute("ALTER TABLE sections ADD COLUMN kind TEXT NOT NULL DEFAULT 'requirement' "
                             "CHECK(kind IN ('requirement','design'))")
                conn.execute("UPDATE requests SET response=json_set(response,'$.kind','requirement') "
                             "WHERE json_extract(response,'$.id') IN (SELECT id FROM sections)")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(statements)")}
            if "section_id" not in columns:
                conn.execute("ALTER TABLE statements ADD COLUMN section_id TEXT REFERENCES sections(id)")
            conn.execute("CREATE INDEX IF NOT EXISTS statements_section ON statements(section_id)")
            removed_fields = {"rationale", "acceptance"} & columns
            for field in sorted(removed_fields):
                conn.execute(f"ALTER TABLE statements DROP COLUMN {field}")
            if previous_version < 5:
                for project in conn.execute("SELECT id,key FROM projects").fetchall():
                    records = conn.execute(
                        "SELECT * FROM statements WHERE project_id=? ORDER BY created_at,id", (project["id"],),
                    ).fetchall()
                    requirement_number = 0
                    design_number = 0
                    for row in records:
                        record = dict(row)
                        previous_ref = record["ref"]
                        if record["kind"] == "requirement" and previous_version < 4:
                            requirement_number += 1
                            record["ref"] = f"{project['key']}-REQ-{requirement_number}"
                        elif record["kind"] == "design":
                            design_number += 1
                            record["ref"] = f"{project['key']}-DES-{design_number}"
                        if removed_fields or record["ref"] != previous_ref:
                            record["version"] += 1
                            record["updated_at"] = now()
                            conn.execute("UPDATE statements SET ref=?,version=?,updated_at=? WHERE id=?",
                                         (record["ref"], record["version"], record["updated_at"], record["id"]))
                            self._activity(conn, record["id"], "system", "schema_updated",
                                           {**record, "previous_ref": previous_ref})
                            conn.execute("UPDATE requests SET response=? WHERE json_extract(response,'$.id')=?",
                                         (json.dumps(record), record["id"]))
                    if previous_version < 4:
                        conn.execute(
                            "INSERT INTO requirement_counters VALUES (?,?) ON CONFLICT(project_id) DO UPDATE "
                            "SET next_number=MAX(next_number, excluded.next_number)", (project["id"], requirement_number + 1),
                        )
                    conn.execute(
                        "INSERT INTO design_counters VALUES (?,?) ON CONFLICT(project_id) DO UPDATE "
                        "SET next_number=MAX(next_number, excluded.next_number)", (project["id"], design_number + 1),
                    )
            conn.execute("PRAGMA user_version = 7")

    def _initialize_tickets(self) -> None:
        with closing(sqlite3.connect(self.root / "tickets.db")) as conn, conn:
            self._check_schema(conn, latest=3)
            conn.row_factory = sqlite3.Row
            previous_version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.executescript(f"""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS boards ({COMMON}, project_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS lanes (
                    {COMMON}, project_id TEXT NOT NULL,
                    board_id TEXT NOT NULL REFERENCES boards(id),
                    category TEXT NOT NULL CHECK(category IN ('open','active','done','cancelled')),
                    position INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    {COMMON}, project_id TEXT NOT NULL,
                    parent_id TEXT REFERENCES tickets(id), lane_id TEXT REFERENCES lanes(id),
                    priority TEXT NOT NULL, owner TEXT NOT NULL, labels TEXT NOT NULL,
                    position INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tickets_project ON tickets(project_id);
                CREATE INDEX IF NOT EXISTS tickets_parent ON tickets(parent_id);
                CREATE INDEX IF NOT EXISTS lanes_board ON lanes(board_id);
                CREATE TABLE IF NOT EXISTS ticket_counters (
                    project_id TEXT PRIMARY KEY,
                    next_number INTEGER NOT NULL CHECK(next_number > 0)
                );
                {HISTORY_SCHEMA}
            """)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR IGNORE INTO metadata VALUES ('generation', ?)", (uuid.uuid4().hex,))
            if previous_version < 2:
                records = conn.execute("SELECT * FROM tickets ORDER BY project_id,created_at,id").fetchall()
                for row in records:
                    record = dict(row)
                    previous_ref = record["ref"]
                    # Legacy refs embed the project key, including for orphaned backup records.
                    prefix = previous_ref.rsplit("-", 2)[0]
                    record["ref"] = self._numbered_ref(conn, record["project_id"], prefix, "ticket")
                    record["version"] += 1
                    record["updated_at"] = now()
                    conn.execute("UPDATE tickets SET ref=?,version=?,updated_at=? WHERE id=?",
                                 (record["ref"], record["version"], record["updated_at"], record["id"]))
                    self._activity(conn, record["id"], "system", "schema_updated",
                                   {**record, "previous_ref": previous_ref})
                    conn.execute("UPDATE requests SET response=? WHERE json_extract(response,'$.id')=?",
                                 (json.dumps(record), record["id"]))
            if previous_version < 3:
                self._migrate_swimlanes(conn)
            conn.execute("PRAGMA user_version = 3")

    def _migrate_swimlanes(self, conn: sqlite3.Connection) -> None:
        legacy = {"Inbox": "open", "Ready": "open", "In progress": "active", "Review": "active", "Done": "done"}
        for board in conn.execute("SELECT id FROM boards").fetchall():
            rows = conn.execute("SELECT * FROM lanes WHERE board_id=?", (board["id"],)).fetchall()
            defaults = [row for row in rows if row["title"] in legacy]
            if len(defaults) != 5 or {row["title"]: row["category"] for row in defaults} != legacy:
                continue
            lanes = {row["title"]: dict(row) for row in defaults}
            planned_id, ready_id = lanes["Inbox"]["id"], lanes["Ready"]["id"]
            for position, (old, title) in enumerate((
                ("Inbox", "Planned"), ("In progress", "In Progress"), ("Review", "In Review"), ("Done", "Done"),
            )):
                record = lanes[old]
                record.update(title=title, position=position, version=record["version"] + 1, updated_at=now())
                if old == "Inbox":
                    record["archived"] = int(record["archived"] and lanes["Ready"]["archived"])
                conn.execute("UPDATE lanes SET title=?,position=?,archived=?,version=?,updated_at=? WHERE id=?",
                             (title, position, record["archived"], record["version"], record["updated_at"], record["id"]))
                self._activity(conn, record["id"], "system", "schema_updated", record)
                conn.execute("UPDATE requests SET response=? WHERE json_extract(response,'$.id')=?",
                             (json.dumps(record), record["id"]))
            for row in conn.execute("SELECT * FROM tickets WHERE lane_id=?", (ready_id,)).fetchall():
                record = dict(row)
                record.update(lane_id=planned_id, version=record["version"] + 1, updated_at=now())
                conn.execute("UPDATE tickets SET lane_id=?,version=?,updated_at=? WHERE id=?",
                             (planned_id, record["version"], record["updated_at"], record["id"]))
                self._activity(conn, record["id"], "system", "schema_updated", record)
                conn.execute("UPDATE requests SET response=? WHERE json_extract(response,'$.id')=?",
                             (json.dumps(record), record["id"]))
            # Merge the removed lane's discussion, history and retries into Planned.
            for table in ("comments", "activity"):
                conn.execute(f"UPDATE {table} SET record_id=? WHERE record_id=?", (planned_id, ready_id))
            conn.execute("UPDATE requests SET response=? WHERE json_extract(response,'$.id')=?",
                         (json.dumps(lanes["Inbox"]), ready_id))
            conn.execute("DELETE FROM lanes WHERE id=?", (ready_id,))

    @staticmethod
    def _check_schema(conn: sqlite3.Connection, latest: int = 1) -> None:
        if conn.execute("PRAGMA user_version").fetchone()[0] > latest:
            raise ProjectError("Database schema is newer than this service", 503)
        conn.execute("PRAGMA journal_mode = WAL")

    def get(self, kind: str, record_id: str) -> dict:
        with self.lock, self.connection(kind) as conn:
            row = conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE id=?", (record_id,)).fetchone()
            if row is None:
                raise ProjectError(f"{kind.title()} not found", 404)
            result = dict(row)
            if kind == "project":
                result["deletion_pending"] = conn.execute(
                    "SELECT 1 FROM project_deletions WHERE project_id=?", (record_id,)
                ).fetchone() is not None
            return result

    def list(self, kind: str, project_id: str = "", q: str = "", offset: int = 0, limit: int = 100) -> dict:
        if not 1 <= limit <= 500 or offset < 0:
            raise ProjectError("Invalid pagination")
        with self.lock, self.connection(kind) as conn:
            where, args = [], []
            if kind != "project":
                if not project_id:
                    raise ProjectError("project_id is required")
                where.append("project_id=?")
                args.append(project_id)
            if q:
                where.append("(instr(lower(title),lower(?))>0 OR instr(lower(body),lower(?))>0)")
                args.extend([q, q])
            clause = " WHERE " + " AND ".join(where) if where else ""
            total = conn.execute(f"SELECT COUNT(*) FROM {TABLES[kind]}{clause}", args).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM {TABLES[kind]}{clause} ORDER BY created_at,id LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
            items = [dict(row) for row in rows]
            if kind == "project":
                pending = {row[0] for row in conn.execute("SELECT project_id FROM project_deletions")}
                for item in items:
                    item["deletion_pending"] = item["id"] in pending
            return {"items": items, "total": total, "offset": offset}

    def _ensure_not_deleting(self, project_id: str) -> None:
        with self.connection("project") as conn:
            if conn.execute("SELECT 1 FROM project_deletions WHERE project_id=?", (project_id,)).fetchone():
                raise ProjectError("Project deletion is pending. Retry Delete to finish it.", 409)
            if conn.execute("SELECT 1 FROM section_deletions WHERE project_id=?", (project_id,)).fetchone():
                raise ProjectError("Section deletion is pending. Retry Delete to finish it.", 409)

    def delete_section(self, section_id: str, expected_version: int) -> dict:
        with self.lock:
            section = self.get("section", section_id)
            if expected_version != section["version"]:
                raise ProjectError("Section changed. Refresh before deleting.", 409)
            project = self.get("project", section["project_id"])
            if project["archived"]:
                raise ProjectError("Project is archived")
            with self.connection("project") as conn:
                pending = conn.execute(
                    "SELECT 1 FROM section_deletions WHERE section_id=?", (section_id,),
                ).fetchone()
            if project["deletion_pending"] or not pending:
                self._ensure_not_deleting(project["id"])
            self.ticket_generation()
            # Persist only deletion intent so a cross-store interruption can resume.
            with self.connection("section", write=True) as conn:
                conn.execute("INSERT OR IGNORE INTO section_deletions VALUES (?,?)",
                             (section_id, project["id"]))
            try:
                self._finish_section_deletion(section_id)
            except (sqlite3.DatabaseError, ProjectError) as exc:
                logging.getLogger("koreproject").exception("Permanent section deletion is pending")
                raise ProjectError(
                    "Section deletion is pending and project edits are blocked. Restore storage and retry Delete; "
                    "deletion also resumes on service restart.", 503,
                ) from exc
            return {"deleted": section_id}

    def _finish_section_deletion(self, section_id: str) -> None:
        with self.connection("section", write=True) as conn:
            conn.execute("""
                CREATE TEMP TABLE deleted_sections AS
                WITH RECURSIVE subtree(id) AS (
                    SELECT id FROM sections WHERE id=?
                    UNION SELECT s.id FROM sections s JOIN subtree ON s.parent_id=subtree.id
                ) SELECT id FROM subtree
            """, (section_id,))
            ids = [row[0] for row in conn.execute(
                "SELECT id FROM deleted_sections UNION SELECT id FROM statements "
                "WHERE section_id IN (SELECT id FROM deleted_sections)"
            )]
            with self.connection("ticket", write=True) as tickets:
                self._purge_record_context(tickets, ids)
            self._purge_record_context(conn, ids)
            conn.execute("DELETE FROM statements WHERE section_id IN (SELECT id FROM deleted_sections)")
            conn.execute("UPDATE sections SET parent_id=NULL WHERE id IN (SELECT id FROM deleted_sections)")
            conn.execute("DELETE FROM section_deletions WHERE section_id=?", (section_id,))
            conn.execute("DELETE FROM sections WHERE id IN (SELECT id FROM deleted_sections)")

    @staticmethod
    def _purge_record_context(conn: sqlite3.Connection, ids: list[str]) -> None:
        conn.execute("CREATE TEMP TABLE deleted_records (id TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO deleted_records VALUES (?)", ((record_id,) for record_id in ids))
        deleted = "SELECT id FROM deleted_records"
        for table in ("comments", "activity"):
            conn.execute(f"DELETE FROM {table} WHERE record_id IN ({deleted})")
        conn.execute(f"DELETE FROM requests WHERE json_extract(response,'$.id') IN ({deleted})")
        conn.execute(f"DELETE FROM links WHERE source_id IN ({deleted}) OR target_id IN ({deleted})")
        conn.execute(
            f"DELETE FROM activity WHERE action IN ('linked','unlinked') AND "
            f"(json_extract(snapshot,'$.source_id') IN ({deleted}) OR "
            f"json_extract(snapshot,'$.target_id') IN ({deleted}))"
        )

    def statement_links(self, project_id: str, offset: int = 0, limit: int = 100) -> dict:
        if not 1 <= limit <= 500 or offset < 0:
            raise ProjectError("Invalid pagination")
        with self.lock, self.connection("statement") as conn:
            self.get("project", project_id)
            total = conn.execute("SELECT COUNT(*) FROM links WHERE project_id=?", (project_id,)).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM links WHERE project_id=? ORDER BY created_at,id LIMIT ? OFFSET ?",
                (project_id, limit, offset),
            )
            return {"items": [dict(row) for row in rows], "total": total, "offset": offset}

    def delete_project(self, project_id: str, expected_version: int, confirmation: str) -> dict:
        with self.lock:
            project = self.get("project", project_id)
            if confirmation != f"DELETE {project['key']}":
                raise ProjectError(f"Type DELETE {project['key']} to permanently delete this project")
            if expected_version != project["version"]:
                raise ProjectError("Project changed. Load it again before deleting.", 409)
            self.ticket_generation()
            # WAL cannot atomically commit both stores; persist intent before either deletion.
            with self.connection("project", write=True) as conn:
                conn.execute("INSERT OR IGNORE INTO project_deletions VALUES (?,?)", (project_id, now()))
            try:
                self._finish_project_deletion(project_id)
            except (sqlite3.DatabaseError, ProjectError) as exc:
                logging.getLogger("koreproject").exception("Permanent project deletion is pending")
                raise ProjectError(
                    "Project deletion is pending and edits are blocked. Restore storage and retry Delete; "
                    "deletion also resumes on service restart.", 503,
                ) from exc
            return {"deleted": project_id}

    def _finish_project_deletion(self, project_id: str) -> None:
        with self.connection("ticket", write=True) as conn:
            ids = """SELECT id FROM tickets WHERE project_id=?
                     UNION SELECT id FROM lanes WHERE project_id=?
                     UNION SELECT id FROM boards WHERE project_id=?"""
            for table in ("comments", "activity"):
                conn.execute(f"DELETE FROM {table} WHERE record_id IN ({ids})", (project_id,) * 3)
            conn.execute("DELETE FROM links WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM requests WHERE json_extract(response,'$.project_id')=?", (project_id,))
            conn.execute("UPDATE tickets SET parent_id=NULL WHERE project_id=?", (project_id,))
            for table in ("tickets", "lanes", "boards", "ticket_counters"):
                conn.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,))
        with self.connection("project", write=True) as conn:
            for table in ("comments", "activity"):
                conn.execute(
                    f"DELETE FROM {table} WHERE record_id=? OR record_id IN "
                    "(SELECT id FROM statements WHERE project_id=? UNION SELECT id FROM sections WHERE project_id=?)",
                    (project_id, project_id, project_id),
                )
            conn.execute("DELETE FROM links WHERE project_id=?", (project_id,))
            conn.execute(
                "DELETE FROM requests WHERE json_extract(response,'$.project_id')=? "
                "OR json_extract(response,'$.id')=?", (project_id, project_id),
            )
            conn.execute("DELETE FROM statements WHERE project_id=?", (project_id,))
            conn.execute("UPDATE sections SET parent_id=NULL WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM section_deletions WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM sections WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM project_deletions WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM requirement_counters WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM design_counters WHERE project_id=?", (project_id,))
            conn.execute("DELETE FROM projects WHERE id=?", (project_id,))

    @staticmethod
    def _numbered_ref(conn: sqlite3.Connection, project_id: str, prefix: str, kind: str) -> str:
        table, tag = {
            "requirement": ("requirement_counters", "REQ"),
            "design": ("design_counters", "DES"),
            "ticket": ("ticket_counters", "TKT"),
        }[kind]
        conn.execute(f"INSERT OR IGNORE INTO {table} VALUES (?,1)", (project_id,))
        number = conn.execute(
            f"SELECT next_number FROM {table} WHERE project_id=?", (project_id,),
        ).fetchone()[0]
        conn.execute(f"UPDATE {table} SET next_number=next_number+1 WHERE project_id=?", (project_id,))
        return f"{prefix}-{tag}-{number}"

    def _activity(self, conn, record_id: str, actor: str, action: str, snapshot: dict) -> None:
        conn.execute("INSERT INTO activity(record_id,actor,action,snapshot,created_at) VALUES (?,?,?,?,?)",
                     (record_id, actor, action, json.dumps(snapshot), now()))

    def _related(self, kind: str, record_id: str, project_id: str) -> dict:
        row = self.get(kind, record_id)
        if row.get("project_id", row["id"]) != project_id:
            raise ProjectError("Related records must belong to the same project")
        if row["archived"]:
            raise ProjectError("Cannot select an archived related record")
        return row

    def save(
        self, kind: str, data: dict, project_id: str = "", record_id: str = "",
        expected_version: int | None = None, actor: str = "operator",
        request_id: str = "", confirm_children: bool = False,
        generation: str = "",
    ) -> dict:
        if kind not in TABLES:
            raise ProjectError("Unknown record kind")
        allowed = {"title", "body", "archived", *DEFAULTS[kind]}
        if set(data) - allowed:
            raise ProjectError("Unknown fields: " + ", ".join(sorted(set(data) - allowed)))
        fingerprint = json.dumps([kind, project_id, data, actor], sort_keys=True)
        with self.lock:
            with self.connection(kind, write=True) as conn:
                if kind not in DEFINITION_KINDS:
                    self._check_generation(conn, generation)
                if request_id and not record_id:
                    cached = conn.execute("SELECT * FROM requests WHERE key=?", (request_id,)).fetchone()
                    if cached:
                        if cached["fingerprint"] != fingerprint:
                            raise ProjectError("Request ID was already used for different content", 409)
                        return json.loads(cached["response"])
                old = None
                if record_id:
                    row = conn.execute(f"SELECT * FROM {TABLES[kind]} WHERE id=?", (record_id,)).fetchone()
                    if row is None:
                        raise ProjectError("Record not found", 404)
                    old = dict(row)
                    if old["version"] != expected_version:
                        raise ProjectError("Record changed. Reload before saving your edits.", 409)
                    project_id = old.get("project_id", old["id"])
                elif expected_version is not None:
                    raise ProjectError("New records cannot have an expected version")
                if project_id:
                    self._ensure_not_deleting(project_id)
                if kind != "project":
                    project = self.get("project", project_id)
                    if project["archived"]:
                        raise ProjectError("Project is archived")
                    project_key = project["key"]
                else:
                    project_key = ""
                record = old or {
                    "id": uuid.uuid4().hex, "title": "", "body": "", "archived": 0,
                    "created_at": now(), "version": 0, **DEFAULTS[kind],
                }
                record = {**record, **data}
                for field in ("title", "body", *DEFAULTS[kind]):
                    if field in {"parent_id", "lane_id", "section_id", "position"}:
                        continue
                    if not isinstance(record[field], str):
                        raise ProjectError(f"{field} must be text")
                record["title"] = record["title"].strip()
                if not record["title"] or len(record["title"]) > 300:
                    raise ProjectError("Title must contain 1-300 characters")
                if len(record["body"]) > 200_000:
                    raise ProjectError("Body exceeds 200,000 characters")
                if record["archived"] not in (0, 1, False, True):
                    raise ProjectError("archived must be a boolean")
                record["archived"] = int(record["archived"])
                if kind != "project":
                    record["project_id"] = project_id
                self._validate(conn, kind, record, old, actor, confirm_children)
                prefix = record["key"] if kind == "project" else project_key
                if not old:
                    if kind in {"statement", "ticket"}:
                        numbered_kind = record["kind"] if kind == "statement" else "ticket"
                        record["ref"] = self._numbered_ref(conn, project_id, prefix, numbered_kind)
                    else:
                        record["ref"] = f"{prefix}-{kind[:3].upper()}-{record['id'][:12]}"
                record["version"] += 1
                record["updated_at"] = now()
                if old:
                    fields = [key for key in record if key != "id"]
                    conn.execute(
                        f"UPDATE {TABLES[kind]} SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?",
                        [*(record[key] for key in fields), record["id"]],
                    )
                else:
                    fields = list(record)
                    conn.execute(
                        f"INSERT INTO {TABLES[kind]} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
                        [record[key] for key in fields],
                    )
                self._activity(conn, record["id"], actor, "updated" if old else "created", record)
                if kind == "board" and not old:
                    for position, (title, category) in enumerate((
                        ("Planned", "open"), ("In Progress", "active"),
                        ("In Review", "active"), ("Done", "done"),
                    )):
                        lane_id = uuid.uuid4().hex
                        conn.execute(
                            """INSERT INTO lanes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (lane_id, f"{prefix}-LAN-{lane_id[:12]}", title, "", 0, 1, now(), now(),
                             project_id, record["id"], category, position),
                        )
                        lane = dict(conn.execute("SELECT * FROM lanes WHERE id=?", (lane_id,)).fetchone())
                        self._activity(conn, lane_id, actor, "created", lane)
                if request_id and not old:
                    conn.execute("INSERT INTO requests VALUES (?,?,?)",
                                 (request_id, fingerprint, json.dumps(record)))
                return record

    def _validate(self, conn, kind: str, record: dict, old: dict | None, actor: str, confirm: bool):
        if kind == "project":
            record["key"] = record["key"].upper().strip()
            if not re.fullmatch(r"[A-Z][A-Z0-9_-]{1,15}", record["key"]):
                raise ProjectError("Project key must be 2-16 letters, digits, underscores or hyphens")
            if old and record["key"] != old["key"]:
                raise ProjectError("Project keys are permanent")
        if kind == "statement":
            if record["kind"] not in {"requirement", "design"}:
                raise ProjectError("Statement must be requirement or design")
            if old and record["kind"] != old["kind"]:
                raise ProjectError("Statement kind is permanent")
            if record["state"] not in {"draft", "in_review", "approved", "retired"}:
                raise ProjectError("Invalid statement state")
            if old and old["state"] == "approved" and any(
                record[key] != old[key] for key in ("title", "body")
            ):
                record["state"] = "draft"
            if actor.startswith("agent") and record["state"] == "approved":
                raise ProjectError("Agents may propose statements, not approve them", 403)
            section_id = record["section_id"]
            if section_id is not None and not isinstance(section_id, str):
                raise ProjectError("section_id must be an ID or null")
            record["section_id"] = section_id or None
            if record["section_id"]:
                section = self._related("section", record["section_id"], record["project_id"])
                if section["kind"] != record["kind"]:
                    raise ProjectError("Statement and section kinds must match")
        if kind in {"ticket", "lane", "section"}:
            if type(record["position"]) is not int or record["position"] < 0:
                raise ProjectError("position must be a non-negative integer")
        if kind == "section":
            if record["kind"] not in {"requirement", "design"}:
                raise ProjectError("Section must be requirement or design")
            if old and record["kind"] != old["kind"]:
                raise ProjectError("Section kind is permanent")
            parent_id = record["parent_id"]
            if parent_id is not None and not isinstance(parent_id, str):
                raise ProjectError("parent_id must be an ID or null")
            record["parent_id"] = parent_id or None
            seen = {record["id"]}
            while parent_id:
                if parent_id in seen:
                    raise ProjectError("Section hierarchy cannot contain cycles")
                seen.add(parent_id)
                parent = self._related("section", parent_id, record["project_id"])
                if parent["kind"] != record["kind"]:
                    raise ProjectError("Parent and child section kinds must match")
                parent_id = parent["parent_id"]
            if record["archived"]:
                occupied = conn.execute(
                    "SELECT 1 FROM statements WHERE section_id=? "
                    "UNION SELECT 1 FROM sections WHERE parent_id=? AND archived=0",
                    (record["id"], record["id"]),
                ).fetchone()
                if occupied:
                    raise ProjectError("Move statements and child sections before archiving this section", 409)
        if kind == "lane":
            self._related("board", record["board_id"], record["project_id"])
            if old and record["board_id"] != old["board_id"]:
                raise ProjectError("Move tickets rather than moving lanes between boards")
            if record["category"] not in {"open", "active", "done", "cancelled"}:
                raise ProjectError("Invalid lane category")
            if old and (record["archived"] or record["category"] != old["category"]):
                occupied = conn.execute("SELECT 1 FROM tickets WHERE lane_id=? AND archived=0",
                                        (record["id"],)).fetchone()
                if occupied:
                    raise ProjectError("Move active tickets before archiving or recategorising this lane", 409)
        if kind == "ticket":
            if record["priority"] not in {"low", "normal", "high", "urgent"}:
                raise ProjectError("Invalid priority")
            for field in ("parent_id", "lane_id"):
                if record[field] is not None and not isinstance(record[field], str):
                    raise ProjectError(f"{field} must be an ID or null")
                record[field] = record[field] or None
            parent_id = record["parent_id"]
            seen = {record["id"]}
            while parent_id:
                if parent_id in seen:
                    raise ProjectError("Ticket hierarchy cannot contain cycles")
                seen.add(parent_id)
                parent = self._related("ticket", parent_id, record["project_id"])
                parent_id = parent["parent_id"]
            if record["lane_id"]:
                lane = self._related("lane", record["lane_id"], record["project_id"])
                self._related("board", lane["board_id"], record["project_id"])
                if lane["category"] in {"done", "cancelled"} and (not old or old["lane_id"] != record["lane_id"]):
                    children = conn.execute("""
                        SELECT 1 FROM tickets t LEFT JOIN lanes l ON t.lane_id=l.id
                        WHERE t.parent_id=? AND t.archived=0
                          AND (l.category IS NULL OR l.category NOT IN ('done','cancelled'))
                    """, (record["id"],)).fetchone()
                    if children and not confirm:
                        raise ProjectError("Unfinished children remain. Confirm explicitly to close this parent.", 409)

    def detail(self, kind: str, record_id: str) -> dict:
        with self.lock:
            result = self.get(kind, record_id)
            with self.connection(kind) as conn:
                result["comments"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM comments WHERE record_id=? ORDER BY created_at,id", (record_id,))]
                result["history"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM activity WHERE record_id=? ORDER BY id DESC LIMIT 100", (record_id,))]
            result["links"] = []
            result["ticket_store_error"] = None
            stores = ("statement", "ticket") if kind == "statement" else (kind,)
            for store in stores:
                try:
                    with self.connection(store) as conn:
                        result["links"].extend(dict(r) for r in conn.execute(
                            "SELECT * FROM links WHERE (source_kind=? AND source_id=?) OR (target_kind=? AND target_id=?)",
                            (kind, record_id, kind, record_id),
                        ))
                except (sqlite3.DatabaseError, ProjectError) as exc:
                    if store != "ticket" or kind != "statement":
                        raise
                    result["ticket_store_error"] = str(exc)
            return result

    def comment(self, kind: str, record_id: str, body: str, actor: str, generation: str = "") -> dict:
        with self.lock:
            record = self.get(kind, record_id)
            self._ensure_not_deleting(record.get("project_id", record["id"]))
            if not body.strip() or len(body) > 20_000:
                raise ProjectError("Comments must contain 1-20,000 characters")
            result = {"id": uuid.uuid4().hex, "record_id": record_id, "body": body, "actor": actor, "created_at": now()}
            with self.connection(kind, write=True) as conn:
                if kind not in DEFINITION_KINDS:
                    self._check_generation(conn, generation)
                conn.execute("INSERT INTO comments VALUES (:id,:record_id,:actor,:body,:created_at)", result)
                self._activity(conn, record_id, actor, "commented", result)
            return result

    def link(self, source_kind: str, source_id: str, target_kind: str, target_id: str,
             relation: str, note: str = "", actor: str = "operator", generation: str = "") -> dict:
        if source_kind not in {"statement", "ticket"} or target_kind not in {"statement", "ticket"}:
            raise ProjectError("Links connect statements and tickets")
        if source_id == target_id:
            raise ProjectError("Cannot link a record to itself")
        with self.lock:
            source = self.get(source_kind, source_id)
            self._ensure_not_deleting(source["project_id"])
            target = self._related(target_kind, target_id, source["project_id"])
            valid = relation == "relates_to"
            valid |= relation == "addresses" and source_kind == "ticket" and target_kind == "statement"
            valid |= relation == "depends_on" and source_kind == target_kind == "ticket"
            valid |= relation == "refines" and source_kind == target_kind == "statement" and source["kind"] == target["kind"]
            valid |= relation == "satisfies" and source_kind == target_kind == "statement" and source["kind"] == "design" and target["kind"] == "requirement"
            if not valid:
                raise ProjectError("Relationship does not match these record kinds")
            store = "statement" if source_kind == target_kind == "statement" else "ticket"
            with self.connection(store, write=True) as conn:
                if store == "ticket":
                    self._check_generation(conn, generation)
                if relation in {"refines", "depends_on"}:
                    found = conn.execute("""
                        WITH RECURSIVE reach(id) AS (
                            SELECT ? UNION SELECT target_id FROM links JOIN reach ON source_id=reach.id
                            WHERE relation=?
                        ) SELECT 1 FROM reach WHERE id=?
                    """, (target_id, relation, source_id)).fetchone()
                    if found:
                        raise ProjectError("Relationship would create a cycle")
                result = dict(id=uuid.uuid4().hex, project_id=source["project_id"],
                              source_kind=source_kind, source_id=source_id, relation=relation,
                              target_kind=target_kind, target_id=target_id, note=note, created_at=now())
                conn.execute("INSERT INTO links VALUES (:id,:project_id,:source_kind,:source_id,:relation,:target_kind,:target_id,:note,:created_at)", result)
                self._activity(conn, source_id, actor, "linked", result)
                return result

    def unlink(self, store: str, link_id: str, actor: str, generation: str = "") -> None:
        if store not in {"statement", "ticket"}:
            raise ProjectError("Invalid link store")
        with self.lock, self.connection(store, write=True) as conn:
            if store == "ticket":
                self._check_generation(conn, generation)
            row = conn.execute("SELECT * FROM links WHERE id=?", (link_id,)).fetchone()
            if row is None:
                raise ProjectError("Link not found", 404)
            self._ensure_not_deleting(row["project_id"])
            conn.execute("DELETE FROM links WHERE id=?", (link_id,))
            self._activity(conn, row["source_id"], actor, "unlinked", dict(row))

    def ticket_generation(self) -> str:
        with self.lock, self.connection("ticket") as conn:
            return conn.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()[0]

    @staticmethod
    def _check_generation(conn: sqlite3.Connection, generation: str) -> None:
        current = conn.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()[0]
        if not generation or generation != current:
            raise ProjectError("Ticket store changed or generation is missing. Refresh before writing.", 409)

    def reset_tickets(self, confirmation: str, generation: str) -> dict:
        if confirmation != "RESET ALL TICKETS":
            raise ProjectError("Type RESET ALL TICKETS to confirm deletion of all ticket data")
        with self.lock, self.connection("ticket", write=True) as conn:
            current = conn.execute("SELECT value FROM metadata WHERE key='generation'").fetchone()[0]
            if generation != current:
                raise ProjectError("Ticket store changed. Reload before resetting.", 409)
            for table in ("links", "comments", "activity", "requests", "tickets", "lanes", "boards"):
                if table == "tickets":
                    conn.execute("UPDATE tickets SET parent_id=NULL")
                conn.execute(f"DELETE FROM {table}")
            new_generation = uuid.uuid4().hex
            conn.execute("UPDATE metadata SET value=? WHERE key='generation'", (new_generation,))
            self._activity(conn, new_generation, "operator", "reset", {"previous_generation": generation})
            return {"generation": new_generation}
