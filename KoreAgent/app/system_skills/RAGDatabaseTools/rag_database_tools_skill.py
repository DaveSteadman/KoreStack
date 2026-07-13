import json
import re
import sqlite3
import zlib
from pathlib import Path
from typing import Any

from utils.workspace_utils import get_controldata_dir
from utils.workspace_utils import get_workspace_root


_DB_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

_WORKSPACE_ROOT      = get_workspace_root()
_SOURCE_DATABASES    = _WORKSPACE_ROOT / "Data" / "datacontrol" / "koredata" / "RAG" / "databases"
_RUNTIME_DATABASES   = get_controldata_dir() / "koredata" / "RAG" / "databases"
_TEMPLATES_ROOT      = _WORKSPACE_ROOT / "KoreUI" / "KoreData" / "KoreRAG" / "templates"
_ARTIFACT_KINDS      = {"descriptor", "ingest", "access", "navigation", "template"}
_SCHEDULE_VALUES     = {"manual", "daily", "weekly", "monthly"}
_MANAGED_BY_VALUES   = {"user", "ingestor"}
_TEMPLATE_CONSTS_RE  = re.compile(r"^\s*([A-Z_]+_TEMPLATE)\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)


def _clean_db_id(db_id: str) -> str:
    cleaned = str(db_id or "").strip().lower()
    if not _DB_ID_RE.match(cleaned):
        raise ValueError("db_id must start with a letter and contain only a-z, 0-9, _.")
    return cleaned


def _compress_text(text: str | None) -> bytes | None:
    if not text:
        return None
    return zlib.compress(str(text).encode("utf-8"), level=6)


def _decompress_text(blob: bytes | str | None) -> str | None:
    if not blob:
        return None
    if isinstance(blob, str):
        return blob
    return zlib.decompress(blob).decode("utf-8")


def _compute_word_count(text: str | None) -> int | None:
    if not text:
        return None
    return len(str(text).split())


def _fts_build_query(q: str) -> str:
    token_re = re.compile(r'"([^"]+)"|(\()|(\))|(\|)|\b(AND|OR|NOT)\b|,|([^\s(),|]+)', re.IGNORECASE)

    def _quote_term(value: str) -> str:
        cleaned = value.strip().replace('"', '""')
        return f'"{cleaned}"' if cleaned else ""

    out: list[str] = []
    open_parens    = 0
    expect_operand = True

    for match in token_re.finditer((q or "").strip()):
        phrase  = match.group(1)
        lparen  = match.group(2)
        rparen  = match.group(3)
        bar     = match.group(4)
        keyword = match.group(5)
        word    = match.group(6)

        if phrase is not None:
            token = _quote_term(phrase)
            if not token:
                continue
            if not expect_operand:
                out.append("AND")
            out.append(token)
            expect_operand = False
            continue

        if lparen:
            if not expect_operand:
                out.append("AND")
            out.append("(")
            open_parens += 1
            expect_operand = True
            continue

        if rparen:
            if open_parens <= 0 or expect_operand:
                continue
            out.append(")")
            open_parens -= 1
            expect_operand = False
            continue

        if bar or (keyword and keyword.upper() == "OR"):
            if expect_operand:
                continue
            out.append("OR")
            expect_operand = True
            continue

        if keyword:
            op = keyword.upper()
            if op == "AND":
                if expect_operand:
                    continue
                out.append("AND")
                expect_operand = True
                continue
            if op == "NOT":
                if expect_operand:
                    continue
                out.append("NOT")
                expect_operand = True
                continue

        if match.group(0) == ",":
            if expect_operand:
                continue
            out.append("AND")
            expect_operand = True
            continue

        if word:
            token = _quote_term(word)
            if not token:
                continue
            if not expect_operand:
                out.append("AND")
            out.append(token)
            expect_operand = False

    while out and out[-1] in {"AND", "OR", "NOT", "("}:
        tail = out.pop()
        if tail == "(":
            open_parens = max(0, open_parens - 1)

    out.extend(")" for _ in range(open_parens) if out)
    return " ".join(out)


def _clean_template_name(template_name: str) -> str:
    cleaned = Path(str(template_name or "").strip()).name
    if not cleaned or cleaned in {".", ".."} or cleaned != str(template_name or "").strip():
        raise ValueError("template_name must be a simple file name.")
    if not cleaned.lower().endswith(".html"):
        raise ValueError("template_name must end with .html.")
    return cleaned


def _source_db_dir(db_id: str) -> Path:
    return _SOURCE_DATABASES / db_id


def _runtime_db_dir(db_id: str) -> Path:
    return _RUNTIME_DATABASES / db_id


def _access_module_name(db_id: str) -> str:
    return f"{db_id}_access.py"


def _artifact_path(
    db_id: str,
    artifact: str,
    *,
    template_name: str = "",
    runtime: bool = False,
) -> Path:
    db_id    = _clean_db_id(db_id)
    artifact = str(artifact or "").strip().lower()
    if artifact not in _ARTIFACT_KINDS:
        raise ValueError(f"artifact must be one of: {', '.join(sorted(_ARTIFACT_KINDS))}")

    if artifact == "template":
        if runtime:
            raise ValueError("runtime templates are not supported; templates live in the repo UI folder.")
        return _TEMPLATES_ROOT / _clean_template_name(template_name)

    root = _runtime_db_dir(db_id) if runtime else _source_db_dir(db_id)
    if artifact == "descriptor":
        return root / f"{db_id}.json"
    if artifact == "ingest":
        return root / "ingest.py"
    if artifact == "access":
        return root / _access_module_name(db_id)
    if artifact == "navigation":
        return root / "navigation_access.py"
    raise ValueError(f"Unsupported artifact: {artifact}")


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_text(path: Path, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8")
    limit = max(1, int(max_chars))
    return text[:limit]


def _descriptor_templates(descriptor: dict[str, Any]) -> list[str]:
    navigation = descriptor.get("navigation")
    if not isinstance(navigation, dict):
        return []
    templates = navigation.get("templates")
    if not isinstance(templates, list):
        return []
    return [str(item).strip() for item in templates if str(item).strip()]


def _navigation_templates(path: Path) -> list[str]:
    if not path.exists():
        return []
    text    = path.read_text(encoding="utf-8")
    matches = _TEMPLATE_CONSTS_RE.findall(text)
    return [match[1] for match in matches if str(match[1]).strip()]


def _collect_template_names(db_id: str) -> list[str]:
    names: list[str] = []

    source_descriptor = _read_json_file(_artifact_path(db_id, "descriptor", runtime=False))
    runtime_descriptor = _read_json_file(_artifact_path(db_id, "descriptor", runtime=True))
    source_navigation  = _artifact_path(db_id, "navigation", runtime=False)
    runtime_navigation = _artifact_path(db_id, "navigation", runtime=True)

    for candidate in (
        *_descriptor_templates(source_descriptor),
        *_descriptor_templates(runtime_descriptor),
        *_navigation_templates(source_navigation),
        *_navigation_templates(runtime_navigation),
    ):
        cleaned = str(candidate or "").strip()
        if cleaned and cleaned not in names:
            names.append(cleaned)
    return names


def _default_descriptor(
    db_id: str,
    *,
    display_name: str,
    description: str,
    navigation_type: str,
    template_names: list[str],
) -> dict[str, Any]:
    navigation: dict[str, Any] = {"type": navigation_type}
    if template_names:
        navigation["templates"] = template_names
    return {
        "id":           db_id,
        "display_name": display_name or db_id.replace("_", " ").title(),
        "description":  description or None,
        "managed_by":   "ingestor",
        "ingestor":     db_id,
        "schedule":     "manual",
        "navigation":   navigation,
        "sync":         {"status": "not_started"},
    }


def _default_runtime_descriptor(
    db_id: str,
    *,
    display_name: str,
    description: str,
) -> dict[str, Any]:
    return {
        "id":           db_id,
        "display_name": display_name or db_id.replace("_", " ").title(),
        "description":  description or None,
        "managed_by":   "user",
    }


def _ensure_runtime_db_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT,
                source      TEXT,
                tags        TEXT,
                content     BLOB,
                word_count  INTEGER,
                created_at  TEXT DEFAULT (datetime('now','utc'))
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                title, source, tags, content,
                tokenize='unicode61 remove_diacritics 1',
                content=''
            )
            """
        )


def _db_connection_for(db_id: str):
    db_path = _runtime_db_dir(db_id) / f"{db_id}.db"
    if not db_path.exists():
        raise FileNotFoundError(f"Runtime database does not exist for {db_id!r}.")
    conn             = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _row_to_chunk_dict(row: sqlite3.Row, *, include_content: bool = False) -> dict:
    item = {
        "id":         row["id"],
        "title":      row["title"],
        "source":     row["source"],
        "tags":       row["tags"],
        "word_count": row["word_count"],
        "created_at": row["created_at"],
    }
    if include_content:
        item["content"] = _decompress_text(row["content"])
    return item


def _scaffold_access_py(db_id: str) -> str:
    return (
        "import json\n"
        "import sqlite3\n"
        "from pathlib import Path\n"
        "from typing import Optional\n"
        "\n"
        "\n"
        "def get_conn(db_path: Path) -> sqlite3.Connection:\n"
        "    conn             = sqlite3.connect(str(db_path))\n"
        "    conn.row_factory = sqlite3.Row\n"
        "    return conn\n"
        "\n"
        "\n"
        "def init_db(conn: sqlite3.Connection) -> None:\n"
        "    conn.execute(\n"
        "        \"\"\"\n"
        "        CREATE TABLE IF NOT EXISTS chunks (\n"
        "            id       INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "            title    TEXT,\n"
        "            source   TEXT,\n"
        "            tags     TEXT,\n"
        "            content  TEXT\n"
        "        )\n"
        "        \"\"\"\n"
        "    )\n"
        "    conn.commit()\n"
        "\n"
        "\n"
        "def count_chunks(conn: sqlite3.Connection) -> int:\n"
        "    return int(conn.execute(\"SELECT COUNT(*) FROM chunks\").fetchone()[0])\n"
        "\n"
        "\n"
        "def write_descriptor(\n"
        "    json_path: Path,\n"
        "    db_id: str,\n"
        "    *,\n"
        "    total_chunks: int,\n"
        "    status: str,\n"
        "    extra_sync: Optional[dict] = None,\n"
        ") -> None:\n"
        "    try:\n"
        "        descriptor = json.loads(json_path.read_text(encoding=\"utf-8\"))\n"
        "    except Exception:\n"
        "        descriptor = {}\n"
        "\n"
        "    descriptor.setdefault(\"id\", db_id)\n"
        "    descriptor.setdefault(\"display_name\", db_id.replace(\"_\", \" \").title())\n"
        "    descriptor.setdefault(\"managed_by\", \"ingestor\")\n"
        "    descriptor.setdefault(\"ingestor\", db_id)\n"
        "    descriptor.setdefault(\"schedule\", \"manual\")\n"
        "    descriptor.setdefault(\"navigation\", {\"type\": \"custom\"})\n"
        "    descriptor[\"sync\"] = {\n"
        "        **(descriptor.get(\"sync\") or {}),\n"
        "        \"status\":       status,\n"
        "        \"total_chunks\": int(total_chunks),\n"
        "    }\n"
        "    if extra_sync:\n"
        "        descriptor[\"sync\"].update(extra_sync)\n"
        "\n"
        "    json_path.write_text(json.dumps(descriptor, indent=2, ensure_ascii=False) + \"\\n\", encoding=\"utf-8\")\n"
    )


def _scaffold_ingest_py(db_id: str) -> str:
    access_module = db_id
    return (
        "#!/usr/bin/env python3\n"
        "import argparse\n"
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        "_HERE = Path(__file__).resolve().parent\n"
        "sys.path.insert(0, str(_HERE))\n"
        "\n"
        f"import {access_module}_access as access  # noqa: E402\n"
        "\n"
        "_DB_PATH   = _HERE / \"{db_id}.db\"\n"
        "_JSON_PATH = _HERE / \"{db_id}.json\"\n"
        "_DB_ID     = \"{db_id}\"\n"
        "\n"
        "\n"
        "def main() -> None:\n"
        "    ap = argparse.ArgumentParser(description=\"Ingest structured data into a KoreRAG database\")\n"
        "    ap.add_argument(\"--reset\",          action=\"store_true\", help=\"Delete and recreate the database before ingesting\")\n"
        "    ap.add_argument(\"--bootstrap-only\", action=\"store_true\", help=\"Create schema and descriptor only\")\n"
        "    args = ap.parse_args()\n"
        "\n"
        "    if args.reset and _DB_PATH.exists():\n"
        "        _DB_PATH.unlink()\n"
        "\n"
        "    conn = access.get_conn(_DB_PATH)\n"
        "    access.init_db(conn)\n"
        "    access.write_descriptor(_JSON_PATH, _DB_ID, total_chunks=access.count_chunks(conn), status=\"running\")\n"
        "\n"
        "    if args.bootstrap_only:\n"
        "        access.write_descriptor(_JSON_PATH, _DB_ID, total_chunks=access.count_chunks(conn), status=\"idle\")\n"
        "        conn.close()\n"
        "        return\n"
        "\n"
        "    # TODO: add source fetch, parse, and chunk-writing logic here.\n"
        "    access.write_descriptor(_JSON_PATH, _DB_ID, total_chunks=access.count_chunks(conn), status=\"complete\")\n"
        "    conn.close()\n"
        "\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    main()\n"
    ).format(db_id=db_id)


def _scaffold_navigation_py(db_id: str, navigation_type: str, template_name: str) -> str:
    return (
        "NAVIGATION_TYPE = \"{navigation_type}\"\n"
        "EXPLORE_TEMPLATE = \"{template_name}\"\n"
        "EXPLORE_CONTEXT = {{\n"
        "    \"sections\":  [],\n"
        "    \"databases\": [],\n"
        "    \"db_info\":   {{}},\n"
        "    \"errors\":    [],\n"
        "    \"timings\":   [],\n"
        "}}\n"
        "\n"
        "\n"
        "def has_navigation(db: str = \"default\") -> bool:\n"
        "    return True\n"
        "\n"
        "\n"
        "def build_explore_payload(db_id: str, *, databases: list[dict], db_info: dict) -> dict:\n"
        "    return {{\n"
        "        \"db_id\":      db_id,\n"
        "        \"sections\":   [],\n"
        "        \"databases\":  databases,\n"
        "        \"db_info\":    db_info,\n"
        "        \"errors\":     [],\n"
        "        \"timings\":    [],\n"
        "    }}\n"
    ).format(
        navigation_type = navigation_type or "custom",
        template_name   = template_name,
    )


def _scaffold_template_html(db_id: str) -> str:
    template = (
        "{% extends \"base.html\" %}\n"
        "{% block title %}Explore __DB_ID__{% endblock %}\n"
        "{% block main_class %}kcui-main{% endblock %}\n"
        "\n"
        "{% block content %}\n"
        "<div class=\"kcui-page kcui-stack\">\n"
        "  <div class=\"panel kcui-panel\">\n"
        "    <div class=\"panel-header kcui-panel-header\">\n"
        "      <span id=\"explore-title\">EXPLORE &mdash; {{ db_id }}</span>\n"
        "      <a class=\"kcui-tag kcui-tag--muted\" href=\"/ui/rag?db={{ db_id }}&view=chunks\" style=\"margin-left:auto;\">BROWSE ALL</a>\n"
        "    </div>\n"
        "    <div class=\"panel-body kcui-panel-body\" id=\"sections-body\">\n"
        "      <p class=\"meta\">Loading navigation data...</p>\n"
        "    </div>\n"
        "  </div>\n"
        "</div>\n"
        "{% endblock %}\n"
        "\n"
        "{% block scripts %}\n"
        "<script>\n"
        "(function () {\n"
        "  const endpoint = '/ui/rag/explore/{{ db_id }}/json';\n"
        "\n"
        "  function escapeHtml(value) {\n"
        "    return String(value ?? '')\n"
        "      .replaceAll('&', '&amp;')\n"
        "      .replaceAll('<', '&lt;')\n"
        "      .replaceAll('>', '&gt;')\n"
        "      .replaceAll('\"', '&quot;');\n"
        "  }\n"
        "\n"
        "  function renderSections(items) {\n"
        "    if (!items || !items.length) return '<p class=\"meta\">No navigation sections defined yet.</p>';\n"
        "    let h = '<div class=\"kcui-stack\">';\n"
        "    for (const item of items) {\n"
        "      h += '<div class=\"kcui-panel\" style=\"padding:0.85rem 1rem; border:1px solid var(--kcui-border-subtle);\">'\n"
        "         + '<div style=\"font-weight:600;\">' + escapeHtml(item.title || '(section)') + '</div>'\n"
        "         + '<div class=\"meta\" style=\"margin-top:0.2rem;\">' + escapeHtml(item.summary || '') + '</div>'\n"
        "         + '</div>';\n"
        "    }\n"
        "    return h + '</div>';\n"
        "  }\n"
        "\n"
        "  fetch(endpoint)\n"
        "    .then(function (r) { return r.json(); })\n"
        "    .then(function (payload) {\n"
        "      const dbInfo = payload.db_info || {};\n"
        "      document.getElementById('explore-title').innerHTML = 'EXPLORE &mdash; ' + escapeHtml(dbInfo.display_name || payload.db_id || '{{ db_id }}');\n"
        "      document.getElementById('sections-body').innerHTML = renderSections(payload.sections || []);\n"
        "    })\n"
        "    .catch(function (err) {\n"
        "      document.getElementById('sections-body').innerHTML = '<p class=\"meta\">Error loading navigation: ' + escapeHtml(err && err.message ? err.message : err) + '</p>';\n"
        "    });\n"
        "})();\n"
        "</script>\n"
        "{% endblock %}\n"
    )
    return template.replace("__DB_ID__", db_id)


def rag_database_list(include_runtime: bool = True) -> list[dict]:
    """List live runtime RAG databases under the configured suite data folder."""
    if not include_runtime:
        return []

    db_ids: set[str] = set()
    if _RUNTIME_DATABASES.exists():
        db_ids.update(path.name for path in _RUNTIME_DATABASES.iterdir() if path.is_dir())

    results: list[dict] = []
    for db_id in sorted(db_ids):
        descriptor_path = _artifact_path(db_id, "descriptor", runtime=True)
        db_path         = _runtime_db_dir(db_id) / f"{db_id}.db"
        descriptor      = _read_json_file(descriptor_path) if descriptor_path.exists() else {}
        results.append({
            "db_id":             db_id,
            "runtime_dir":       str(_runtime_db_dir(db_id)),
            "db_path":           str(db_path),
            "descriptor_path":   str(descriptor_path),
            "db_exists":         db_path.exists(),
            "descriptor_exists": descriptor_path.exists(),
            "display_name":      descriptor.get("display_name") or db_id.replace("_", " ").title(),
            "managed_by":        descriptor.get("managed_by") or "",
        })
    return results


def rag_database_inspect(db_id: str) -> dict:
    """Inspect one live runtime RAG database under the configured suite data folder."""
    db_id                = _clean_db_id(db_id)
    runtime_dir          = _runtime_db_dir(db_id)
    runtime_descriptor_p = _artifact_path(db_id, "descriptor", runtime=True)
    db_path              = runtime_dir / f"{db_id}.db"

    return {
        "db_id":                 db_id,
        "runtime_dir":           str(runtime_dir),
        "db_path":               str(db_path),
        "descriptor_path":       str(runtime_descriptor_p),
        "runtime_exists":        runtime_dir.exists(),
        "db_exists":             db_path.exists(),
        "descriptor_exists":     runtime_descriptor_p.exists(),
        "runtime_descriptor_data": _read_json_file(runtime_descriptor_p) if runtime_descriptor_p.exists() else {},
    }


def rag_database_create(
    db_id: str,
    display_name: str = "",
    description: str = "",
) -> dict:
    """Create a live runtime KoreRAG database under the suite data folder.

    This creates the runtime directory, descriptor, and an empty SQLite database file.
    It does not create repo-side ingestor source files.
    """
    db_id            = _clean_db_id(db_id)
    runtime_dir      = _runtime_db_dir(db_id)
    descriptor_path  = _artifact_path(db_id, "descriptor", runtime=True)
    db_path          = runtime_dir / f"{db_id}.db"
    existed_before   = runtime_dir.exists() or descriptor_path.exists() or db_path.exists()
    descriptor       = _default_runtime_descriptor(
        db_id        = db_id,
        display_name = display_name,
        description  = description,
    )

    _write_json_file(descriptor_path, descriptor)
    _ensure_runtime_db_file(db_path)

    return {
        "db_id":                    db_id,
        "runtime_dir":              str(runtime_dir),
        "descriptor_path":          str(descriptor_path),
        "db_path":                  str(db_path),
        "runtime_database_created": True,
        "already_existed":          existed_before,
        "note": (
            "Runtime database created in suite data. No repo-side ingestor scaffold was created."
        ),
    }


def rag_database_scaffold(
    db_id: str,
    display_name: str = "",
    description: str = "",
    navigation_type: str = "custom",
    template_name: str = "",
) -> dict:
    """Create source scaffold files for a script-backed RAG database.

    This writes repo-side ingestor artifacts only. It does not create a runtime .db file,
    register the database with a live KoreRAG instance, or ingest any content.
    """
    db_id         = _clean_db_id(db_id)
    nav_type      = str(navigation_type or "custom").strip().lower() or "custom"
    template_file = _clean_template_name(template_name) if template_name else f"rag_explore_{db_id}.html"

    source_dir = _source_db_dir(db_id)
    source_dir.mkdir(parents=True, exist_ok=True)

    created: list[str] = []
    updated: list[str] = []

    descriptor_path = _artifact_path(db_id, "descriptor", runtime=False)
    access_path     = _artifact_path(db_id, "access", runtime=False)
    ingest_path     = _artifact_path(db_id, "ingest", runtime=False)
    navigation_path = _artifact_path(db_id, "navigation", runtime=False)
    template_path   = _artifact_path(db_id, "template", template_name=template_file, runtime=False)

    descriptor_payload = _default_descriptor(
        db_id            = db_id,
        display_name     = display_name,
        description      = description,
        navigation_type  = nav_type,
        template_names   = [template_file],
    )

    for path, content, is_json in (
        (descriptor_path, descriptor_payload, True),
        (access_path,     _scaffold_access_py(db_id),                 False),
        (ingest_path,     _scaffold_ingest_py(db_id),                 False),
        (navigation_path, _scaffold_navigation_py(db_id, nav_type, template_file), False),
        (template_path,   _scaffold_template_html(db_id),             False),
    ):
        existed = path.exists()
        if is_json:
            _write_json_file(path, content)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(content), encoding="utf-8")
        (updated if existed else created).append(str(path))

    return {
        "db_id":                    db_id,
        "source_dir":               str(source_dir),
        "runtime_dir":              str(_runtime_db_dir(db_id)),
        "created":                  created,
        "updated":                  updated,
        "template_name":            template_file,
        "source_scaffold_created":  True,
        "runtime_database_created": False,
        "note": (
            "Source scaffold created only. No runtime database file exists until the "
            "database is separately created, synced, or ingested."
        ),
    }


def rag_database_update_descriptor(
    db_id: str,
    display_name: str = "",
    description: str = "",
    source_url: str = "",
    licence: str = "",
    schedule: str = "",
    managed_by: str = "",
    ingestor: str = "",
    navigation_type: str = "",
    navigation_tables: list[str] | None = None,
    template_names: list[str] | None = None,
    runtime: bool = True,
) -> dict:
    """Create or update a runtime RAG database descriptor JSON file."""
    db_id           = _clean_db_id(db_id)
    if not runtime:
        raise ValueError("rag_database_update_descriptor only writes runtime descriptors in the configured suite data path.")
    descriptor_path = _artifact_path(db_id, "descriptor", runtime=runtime)
    descriptor      = _read_json_file(descriptor_path) if descriptor_path.exists() else {"id": db_id}

    if display_name.strip():
        descriptor["display_name"] = display_name.strip()
    if description.strip():
        descriptor["description"] = description.strip()
    if source_url.strip():
        descriptor["source_url"] = source_url.strip()
    if licence.strip():
        descriptor["licence"] = licence.strip()

    if schedule.strip():
        schedule_value = schedule.strip().lower()
        if schedule_value not in _SCHEDULE_VALUES:
            raise ValueError(f"schedule must be one of: {', '.join(sorted(_SCHEDULE_VALUES))}")
        descriptor["schedule"] = schedule_value

    if managed_by.strip():
        managed_by_value = managed_by.strip().lower()
        if managed_by_value not in _MANAGED_BY_VALUES:
            raise ValueError(f"managed_by must be one of: {', '.join(sorted(_MANAGED_BY_VALUES))}")
        descriptor["managed_by"] = managed_by_value

    if ingestor.strip():
        descriptor["ingestor"] = _clean_db_id(ingestor.strip())

    navigation = descriptor.get("navigation")
    if not isinstance(navigation, dict):
        navigation = {}

    if navigation_type.strip():
        navigation["type"] = navigation_type.strip().lower()
    if navigation_tables is not None:
        navigation["tables"] = [str(item).strip() for item in navigation_tables if str(item).strip()]
    if template_names is not None:
        navigation["templates"] = [_clean_template_name(name) for name in template_names]
    if navigation:
        descriptor["navigation"] = navigation

    _write_json_file(descriptor_path, descriptor)
    return {
        "db_id":            db_id,
        "descriptor_path":  str(descriptor_path),
        "descriptor":       descriptor,
    }


def rag_chunk_add(
    db_id: str,
    content: str,
    title: str = "",
    source: str = "",
    tags: str = "",
) -> dict:
    """Add one chunk to a live runtime RAG database."""
    db_id = _clean_db_id(db_id)
    if not str(content or "").strip():
        raise ValueError("content is required.")

    word_count = _compute_word_count(content)
    compressed = _compress_text(content)

    with _db_connection_for(db_id) as conn:
        cur = conn.execute(
            "INSERT INTO chunks (title, source, tags, content, word_count) VALUES (?, ?, ?, ?, ?)",
            (title or None, source or None, tags or None, compressed, word_count),
        )
        chunk_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO chunks_fts(rowid, title, source, tags, content) VALUES (?, ?, ?, ?, ?)",
            (chunk_id, title or "", source or "", tags or "", content),
        )
        row = conn.execute(
            "SELECT id, title, source, tags, word_count, created_at FROM chunks WHERE id = ?",
            (chunk_id,),
        ).fetchone()

    return _row_to_chunk_dict(row, include_content=False)


def rag_chunk_get(db_id: str, chunk_id: int, include_content: bool = True) -> dict:
    """Get one chunk from a live runtime RAG database."""
    db_id = _clean_db_id(db_id)

    with _db_connection_for(db_id) as conn:
        cols = "id, title, source, tags, word_count, created_at, content" if include_content else "id, title, source, tags, word_count, created_at"
        row  = conn.execute(
            f"SELECT {cols} FROM chunks WHERE id = ?",
            (int(chunk_id),),
        ).fetchone()

    if row is None:
        raise ValueError(f"Chunk {chunk_id} not found in {db_id!r}.")
    return _row_to_chunk_dict(row, include_content=include_content)


def rag_chunk_list(db_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
    """List chunk metadata from a live runtime RAG database."""
    db_id   = _clean_db_id(db_id)
    limit   = max(1, min(int(limit), 500))
    offset  = max(0, int(offset))

    with _db_connection_for(db_id) as conn:
        rows = conn.execute(
            "SELECT id, title, source, tags, word_count, created_at FROM chunks ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

    return [_row_to_chunk_dict(row, include_content=False) for row in rows]


def rag_chunk_search(
    db_id: str,
    query: str,
    limit: int = 20,
    source: str = "",
    tags: str = "",
) -> list[dict]:
    """Full-text search chunks in a live runtime RAG database."""
    db_id  = _clean_db_id(db_id)
    limit  = max(1, min(int(limit), 200))
    fts_q  = _fts_build_query(query)
    if not fts_q:
        return []

    sql = """
        SELECT c.id,
               c.title,
               c.source,
               c.tags,
               c.word_count,
               c.created_at,
               snippet(chunks_fts, 3, '[', ']', '...', 32) AS snippet
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        WHERE chunks_fts MATCH ?
    """
    params: list[Any] = [fts_q]
    if source:
        sql += " AND c.source LIKE ?"
        params.append(f"%{source}%")
    if tags:
        sql += " AND c.tags LIKE ?"
        params.append(f"%{tags}%")
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    with _db_connection_for(db_id) as conn:
        rows = conn.execute(sql, params).fetchall()

    return [
        {
            "id":         row["id"],
            "title":      row["title"],
            "source":     row["source"],
            "tags":       row["tags"],
            "word_count": row["word_count"],
            "created_at": row["created_at"],
            "snippet":    row["snippet"],
        }
        for row in rows
    ]


def rag_database_read_artifact(
    db_id: str,
    artifact: str,
    template_name: str = "",
    runtime: bool = False,
    max_chars: int = 12000,
) -> dict:
    """Read a descriptor, ingest, access, navigation, or template artifact for a RAG database."""
    db_id  = _clean_db_id(db_id)
    path   = _artifact_path(db_id, artifact, template_name=template_name, runtime=runtime)
    if not path.exists():
        return {
            "db_id":     db_id,
            "artifact":  artifact,
            "runtime":   runtime,
            "path":      str(path),
            "exists":    False,
            "content":   "",
        }

    return {
        "db_id":     db_id,
        "artifact":  artifact,
        "runtime":   runtime,
        "path":      str(path),
        "exists":    True,
        "content":   _read_text(path, max_chars=max_chars),
    }


def rag_database_write_artifact(
    db_id: str,
    artifact: str,
    content: str,
    template_name: str = "",
    runtime: bool = False,
) -> dict:
    """Write a descriptor, ingest, access, navigation, or template artifact for a RAG database."""
    db_id = _clean_db_id(db_id)
    path  = _artifact_path(db_id, artifact, template_name=template_name, runtime=runtime)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(content), encoding="utf-8")

    return {
        "db_id":     db_id,
        "artifact":  artifact,
        "runtime":   runtime,
        "path":      str(path),
        "bytes":     path.stat().st_size,
    }


__all__ = [
    "rag_database_create",
    "rag_database_inspect",
    "rag_database_list",
    "rag_database_update_descriptor",
    "rag_chunk_add",
    "rag_chunk_get",
    "rag_chunk_list",
    "rag_chunk_search",
]
