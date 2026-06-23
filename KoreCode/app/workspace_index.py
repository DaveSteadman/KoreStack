from __future__ import annotations

import ast
import hashlib
import sqlite3
from contextlib import closing
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


WORKSPACE_INDEX_FILENAME = "KoreCodeWorkspace.sqlite3"
_SKIP_DIRS               = {
    ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv",
    "__pycache__", "node_modules",
}
_MAX_FILE_BYTES          = 1_500_000
_SCHEMA_VERSION          = 1


@dataclass(slots=True)
class IndexedFile:
    path:         str
    size_bytes:   int
    content_hash: str
    module_name:  str
    source:       str
    tree:         ast.AST


def index_path_for(root: Path) -> Path:
    return root / WORKSPACE_INDEX_FILENAME


def build_workspace_index(root: Path) -> dict:
    root       = root.resolve()
    index_path = index_path_for(root)
    now        = datetime.now(timezone.utc).isoformat()
    entries    = list(_iter_indexed_files(root))

    with closing(sqlite3.connect(index_path)) as conn:
        conn.row_factory = sqlite3.Row
        _rebuild_schema(conn)
        _write_metadata(conn, root, now)

        for entry in entries:
            file_id = _insert_file(conn, entry)
            _insert_imports(conn, file_id, entry.tree)
            symbols = _insert_symbols(conn, file_id, entry.tree)
            _insert_calls(conn, file_id, entry.tree, symbols)

        counts = _counts(conn)
        conn.commit()

    return {
        "ok":               True,
        "root":             str(root),
        "index_path":       str(index_path),
        "index_file_name":  WORKSPACE_INDEX_FILENAME,
        "generated_at":     now,
        **counts,
    }


def read_workspace_index_status(root: Path) -> dict | None:
    root       = root.resolve()
    index_path = index_path_for(root)
    if not index_path.exists() or not index_path.is_file():
        return None

    with closing(sqlite3.connect(index_path)) as conn:
        conn.row_factory = sqlite3.Row
        counts = _counts(conn)
        meta   = {
            row["meta_key"]: row["meta_value"]
            for row in conn.execute("SELECT meta_key, meta_value FROM metadata")
        }

    return {
        "root":             str(root),
        "index_path":       str(index_path),
        "index_file_name":  WORKSPACE_INDEX_FILENAME,
        "generated_at":     meta.get("generated_at"),
        "schema_version":   int(meta.get("schema_version") or _SCHEMA_VERSION),
        **counts,
    }


def list_indexed_files(root: Path) -> list[dict]:
    with _connect_index(root) as conn:
        rows = conn.execute(
            """
            SELECT
                path,
                module_name,
                size_bytes,
                content_hash,
                indexed_at
            FROM files
            ORDER BY path
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_indexed_symbols(
    root: Path,
    *,
    path: str | None = None,
    query: str | None = None,
    kind: str | None = None,
    limit: int = 200,
) -> list[dict]:
    sql    = [
        """
        SELECT
            s.id,
            f.path,
            s.qualname,
            s.name,
            s.kind,
            s.container_qualname,
            s.signature,
            s.line_start,
            s.line_end,
            s.docstring
        FROM symbols s
        JOIN files f
          ON f.id = s.file_id
        WHERE 1 = 1
        """
    ]
    params: list[object] = []

    if path:
        sql.append("AND f.path = ?")
        params.append(str(path))
    if kind:
        sql.append("AND s.kind = ?")
        params.append(str(kind))
    if query:
        sql.append("AND (s.qualname LIKE ? OR s.signature LIKE ? OR f.path LIKE ?)")
        like = f"%{query}%"
        params.extend([like, like, like])

    sql.append("ORDER BY f.path, s.line_start LIMIT ?")
    params.append(max(1, min(int(limit), 1000)))

    with _connect_index(root) as conn:
        rows = conn.execute("\n".join(sql), params).fetchall()
    return [dict(row) for row in rows]


def get_symbol_by_qualname(root: Path, qualname: str) -> dict | None:
    with _connect_index(root) as conn:
        row = conn.execute(
            """
            SELECT
                s.id,
                f.path,
                s.qualname,
                s.name,
                s.kind,
                s.container_qualname,
                s.signature,
                s.line_start,
                s.line_end,
                s.docstring
            FROM symbols s
            JOIN files f
              ON f.id = s.file_id
            WHERE s.qualname = ?
            LIMIT 1
            """,
            [str(qualname)],
        ).fetchone()
    return dict(row) if row is not None else None


def list_symbol_callees(root: Path, qualname: str) -> list[dict]:
    with _connect_index(root) as conn:
        rows = conn.execute(
            """
            SELECT
                caller.qualname                AS caller_qualname,
                caller_file.path               AS caller_path,
                calls.call_name                AS call_name,
                calls.call_qualname            AS call_qualname,
                calls.line                     AS line,
                target.qualname                AS target_qualname,
                target_file.path               AS target_path,
                target.signature               AS target_signature
            FROM calls
            JOIN symbols caller
              ON caller.id = calls.caller_symbol_id
            JOIN files caller_file
              ON caller_file.id = caller.file_id
            LEFT JOIN symbols target
              ON target.qualname = calls.call_qualname
            LEFT JOIN files target_file
              ON target_file.id = target.file_id
            WHERE caller.qualname = ?
            ORDER BY calls.line, calls.call_qualname, calls.call_name
            """,
            [str(qualname)],
        ).fetchall()
    return [dict(row) for row in rows]


def list_symbol_callers(root: Path, qualname: str) -> list[dict]:
    with _connect_index(root) as conn:
        rows = conn.execute(
            """
            SELECT
                caller.qualname     AS caller_qualname,
                caller_file.path    AS caller_path,
                caller.signature    AS caller_signature,
                calls.line          AS line,
                calls.call_name     AS call_name,
                calls.call_qualname AS call_qualname
            FROM calls
            JOIN symbols caller
              ON caller.id = calls.caller_symbol_id
            JOIN files caller_file
              ON caller_file.id = caller.file_id
            WHERE calls.call_qualname = ?
            ORDER BY caller_file.path, calls.line
            """,
            [str(qualname)],
        ).fetchall()
    return [dict(row) for row in rows]


@contextmanager
def _connect_index(root: Path):
    index_path = index_path_for(root.resolve())
    if not index_path.exists():
        raise FileNotFoundError(str(index_path))
    conn = sqlite3.connect(index_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _rebuild_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS calls;
        DROP TABLE IF EXISTS imports;
        DROP TABLE IF EXISTS symbols;
        DROP TABLE IF EXISTS files;
        DROP TABLE IF EXISTS metadata;

        CREATE TABLE metadata (
            meta_key   TEXT PRIMARY KEY,
            meta_value TEXT NOT NULL
        );

        CREATE TABLE files (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            path         TEXT NOT NULL UNIQUE,
            module_name  TEXT NOT NULL,
            size_bytes   INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            indexed_at   TEXT NOT NULL
        );

        CREATE TABLE symbols (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id            INTEGER NOT NULL,
            qualname           TEXT NOT NULL UNIQUE,
            name               TEXT NOT NULL,
            kind               TEXT NOT NULL,
            container_qualname TEXT,
            signature          TEXT NOT NULL,
            line_start         INTEGER NOT NULL,
            line_end           INTEGER NOT NULL,
            docstring          TEXT,
            FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
        );

        CREATE TABLE imports (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id     INTEGER NOT NULL,
            module_name TEXT,
            imported_as TEXT NOT NULL,
            import_type TEXT NOT NULL,
            line        INTEGER NOT NULL,
            FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
        );

        CREATE TABLE calls (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id          INTEGER NOT NULL,
            caller_symbol_id INTEGER NOT NULL,
            call_name        TEXT NOT NULL,
            call_qualname    TEXT NOT NULL,
            line             INTEGER NOT NULL,
            FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE,
            FOREIGN KEY(caller_symbol_id) REFERENCES symbols(id) ON DELETE CASCADE
        );

        CREATE INDEX idx_symbols_file_id        ON symbols(file_id);
        CREATE INDEX idx_symbols_name           ON symbols(name);
        CREATE INDEX idx_imports_file_id        ON imports(file_id);
        CREATE INDEX idx_calls_caller_symbol_id ON calls(caller_symbol_id);
        CREATE INDEX idx_calls_call_qualname    ON calls(call_qualname);
        """
    )


def _write_metadata(conn: sqlite3.Connection, root: Path, generated_at: str) -> None:
    rows = [
        ("schema_version", str(_SCHEMA_VERSION)),
        ("root",           str(root)),
        ("generated_at",   generated_at),
    ]
    conn.executemany(
        "INSERT INTO metadata (meta_key, meta_value) VALUES (?, ?)",
        rows,
    )


def _counts(conn: sqlite3.Connection) -> dict:
    return {
        "file_count":    int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]),
        "symbol_count":  int(conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]),
        "import_count":  int(conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]),
        "call_count":    int(conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]),
    }


def _iter_indexed_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if _should_skip(path, root):
            continue
        if path.suffix.lower() not in {".py", ".pyi"}:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) > _MAX_FILE_BYTES:
            continue
        source = _decode_source(raw)
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue
        yield IndexedFile(
            path         = _rel_path(path, root),
            size_bytes   = len(raw),
            content_hash = hashlib.sha256(raw).hexdigest(),
            module_name  = _module_name_for(path, root),
            source       = source,
            tree         = tree,
        )


def _insert_file(conn: sqlite3.Connection, entry: IndexedFile) -> int:
    indexed_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO files (
            path,
            module_name,
            size_bytes,
            content_hash,
            indexed_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [
            entry.path,
            entry.module_name,
            int(entry.size_bytes),
            entry.content_hash,
            indexed_at,
        ],
    )
    return int(cur.lastrowid)


def _insert_symbols(conn: sqlite3.Connection, file_id: int, tree: ast.AST) -> dict[str, int]:
    symbol_ids: dict[str, int] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.ClassDef):
            symbol_ids[node.name] = _insert_symbol_row(
                conn,
                file_id             = file_id,
                qualname            = node.name,
                name                = node.name,
                kind                = "class",
                container_qualname  = None,
                signature           = _format_class_signature(node),
                line_start          = int(getattr(node, "lineno", 1)),
                line_end            = int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
                docstring           = ast.get_docstring(node),
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualname = f"{node.name}.{child.name}"
                    symbol_ids[qualname] = _insert_symbol_row(
                        conn,
                        file_id             = file_id,
                        qualname            = qualname,
                        name                = child.name,
                        kind                = "async method" if isinstance(child, ast.AsyncFunctionDef) else "method",
                        container_qualname  = node.name,
                        signature           = _format_method_signature(node.name, child, isinstance(child, ast.AsyncFunctionDef)),
                        line_start          = int(getattr(child, "lineno", 1)),
                        line_end            = int(getattr(child, "end_lineno", getattr(child, "lineno", 1))),
                        docstring           = ast.get_docstring(child),
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbol_ids[node.name] = _insert_symbol_row(
                conn,
                file_id             = file_id,
                qualname            = node.name,
                name                = node.name,
                kind                = "async function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                container_qualname  = None,
                signature           = _format_function_signature(node, isinstance(node, ast.AsyncFunctionDef)),
                line_start          = int(getattr(node, "lineno", 1)),
                line_end            = int(getattr(node, "end_lineno", getattr(node, "lineno", 1))),
                docstring           = ast.get_docstring(node),
            )
    return symbol_ids


def _insert_symbol_row(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    qualname: str,
    name: str,
    kind: str,
    container_qualname: str | None,
    signature: str,
    line_start: int,
    line_end: int,
    docstring: str | None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO symbols (
            file_id,
            qualname,
            name,
            kind,
            container_qualname,
            signature,
            line_start,
            line_end,
            docstring
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            file_id,
            qualname,
            name,
            kind,
            container_qualname,
            signature,
            int(line_start),
            int(line_end),
            docstring,
        ],
    )
    return int(cur.lastrowid)


def _insert_imports(conn: sqlite3.Connection, file_id: int, tree: ast.AST) -> None:
    rows: list[tuple] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                rows.append((
                    file_id,
                    alias.name,
                    alias.asname or alias.name,
                    "import",
                    int(getattr(node, "lineno", 1)),
                ))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_as = alias.asname or alias.name
                module_name = f"{node.module}.{alias.name}" if node.module else alias.name
                rows.append((
                    file_id,
                    module_name,
                    imported_as,
                    "from",
                    int(getattr(node, "lineno", 1)),
                ))
    conn.executemany(
        """
        INSERT INTO imports (
            file_id,
            module_name,
            imported_as,
            import_type,
            line
        ) VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )


def _insert_calls(conn: sqlite3.Connection, file_id: int, tree: ast.AST, symbol_ids: dict[str, int]) -> None:
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _insert_call_rows_for_symbol(
                        conn,
                        file_id          = file_id,
                        caller_symbol_id = symbol_ids.get(f"{node.name}.{child.name}"),
                        container_name   = node.name,
                        func_node        = child,
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _insert_call_rows_for_symbol(
                conn,
                file_id          = file_id,
                caller_symbol_id = symbol_ids.get(node.name),
                container_name   = None,
                func_node        = node,
            )


def _insert_call_rows_for_symbol(
    conn: sqlite3.Connection,
    *,
    file_id: int,
    caller_symbol_id: int | None,
    container_name: str | None,
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> None:
    if caller_symbol_id is None:
        return
    rows: list[tuple] = []
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        call_name, call_qualname = _resolve_call_target(node.func, container_name)
        if not call_qualname:
            continue
        rows.append((
            file_id,
            caller_symbol_id,
            call_name,
            call_qualname,
            int(getattr(node, "lineno", getattr(func_node, "lineno", 1))),
        ))
    conn.executemany(
        """
        INSERT INTO calls (
            file_id,
            caller_symbol_id,
            call_name,
            call_qualname,
            line
        ) VALUES (?, ?, ?, ?, ?)
        """,
        rows,
    )


def _resolve_call_target(node: ast.AST, container_name: str | None) -> tuple[str, str]:
    if isinstance(node, ast.Name):
        return node.id, node.id
    if isinstance(node, ast.Attribute):
        chain = _attribute_chain(node)
        if not chain:
            return "", ""
        if chain[0] == "self" and container_name and len(chain) >= 2:
            method_name = chain[-1]
            return method_name, f"{container_name}.{method_name}"
        return chain[-1], ".".join(chain)
    return "", ""


def _attribute_chain(node: ast.Attribute) -> list[str]:
    parts: list[str] = [node.attr]
    value = node.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name):
        parts.append(value.id)
        return list(reversed(parts))
    return []


def _module_name_for(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    if rel.suffix in {".py", ".pyi"}:
        rel = rel.with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _decode_source(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _should_skip(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in _SKIP_DIRS for part in rel_parts)


def _rel_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _format_class_signature(node: ast.ClassDef) -> str:
    if not node.bases:
        return f"class {node.name}"
    bases = ", ".join(_safe_unparse(base) for base in node.bases)
    return f"class {node.name}({bases})"


def _format_function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool = False) -> str:
    args          = []
    positional    = list(node.args.posonlyargs) + list(node.args.args)
    defaults      = list(node.args.defaults)
    default_start = len(positional) - len(defaults)

    for index, arg in enumerate(positional):
        formatted = _format_arg(arg)
        if index >= default_start and defaults:
            formatted = f"{formatted}={_safe_unparse(defaults[index - default_start])}"
        args.append(formatted)

    if node.args.vararg:
        args.append(f"*{_format_arg(node.args.vararg)}")
    elif node.args.kwonlyargs:
        args.append("*")

    for kwarg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        formatted = _format_arg(kwarg)
        if default is not None:
            formatted = f"{formatted}={_safe_unparse(default)}"
        args.append(formatted)

    if node.args.kwarg:
        args.append(f"**{_format_arg(node.args.kwarg)}")

    prefix  = "async def " if is_async else "def "
    returns = f" -> {_safe_unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix}{node.name}({', '.join(args)}){returns}"


def _format_method_signature(class_name: str, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool = False) -> str:
    signature = _format_function_signature(node, is_async=is_async)
    prefix    = "async def " if is_async else "def "
    if signature.startswith(prefix):
        signature = signature[len(prefix):]
    return f"{class_name}.{signature}"


def _format_arg(arg: ast.arg) -> str:
    if arg.annotation is None:
        return arg.arg
    return f"{arg.arg}: {_safe_unparse(arg.annotation)}"


def _safe_unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return "..."
