# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# MCP tools for KoreDocs — assembler module.
#
# Defines the shared mcp instance (re-exported from _mcp_instance), cross-type public
# helpers, and cross-type MCP tools, then imports the per-document-type sub-modules
# (koredoc_mcp, koresheet_mcp, kodiag_mcp) to register their tools.
#
# Cross-type MCP tools:
#   list_supported_types()      -- list all document types KoreDocs can create
#   search_files(query, ...)    -- full-text search across all document types
#
# server.py imports mcp, FORMAT_INFO, and the sheet functions from this module.
#
# Related modules:
#   - app/_mcp_instance.py  -- FastMCP singleton
#   - app/_mcp_shared.py    -- shared folder/file helpers
#   - app/koredoc_mcp.py    -- .koredoc document tools
#   - app/koresheet_mcp.py  -- .koresheet spreadsheet tools
#   - app/kodiag_mcp.py     -- .kodiag diagram tools
#   - app/server.py         -- mounts mcp into the FastAPI app
# ====================================================================================================

from __future__ import annotations

from typing import Any, Optional
from typing import Annotated, Literal

from . import korefile
from ._mcp_instance import mcp  # noqa: F401 – re-exported for server.py
from ._mcp_shared import (
    ALLOWED_EXTENSIONS,
    _normalise_folder_path,
    _folder_tree,
    _folder_tree_with_files,
    _folder_id_for_path,
    _file_summary,
    _create_serialized_file,
    _ensure_extension,
)

KoreFileType = Literal['koredoc', 'koresheet', 'kodiag']


# ── Cross-type public functions ────────────────────────────────────────────

def list_supported_types() -> list[dict]:
    """Return all supported KoreDocs file types with extensions, schema summaries, and examples."""
    return [FORMAT_INFO[key] for key in sorted(FORMAT_INFO)]


def search_files(
    query: Annotated[str, 'Search query. Supports words and quoted phrases.'],
    type: Annotated[Optional[KoreFileType], 'Optional document type filter.'] = None,
    folder_path: Annotated[Optional[str], 'Optional folder path such as "/" or "/01-misc".'] = None,
    limit: Annotated[int, 'Maximum number of results, between 1 and 200.'] = 20,
) -> list[dict]:
    """Full-text search across KoreFile documents."""
    ext = type.lstrip('.') if type else None
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f'Unsupported type: {type}')
    if limit < 1 or limit > 200:
        raise ValueError('limit must be between 1 and 200')
    return korefile.search(
        query,
        ext=ext,
        folder_path=_normalise_folder_path(folder_path) if folder_path else None,
        limit=limit,
    )


def get_file(id: int) -> dict:
    """Retrieve a KoreFile document by id, including full content."""
    file = korefile.get_file(id, include_content=True)
    if file is None:
        raise ValueError(f'File not found: {id}')
    return file


def list_files(
    folder_path: Optional[str] = None,
    type: Annotated[Optional[KoreFileType], 'Optional document type filter.'] = None,
) -> list[dict]:
    """List KoreFile documents, returning metadata only.

    Omit folder_path or pass an empty string to list all documents. Pass an
    explicit path such as "/" or "/01-misc" to list only that folder.
    """
    ext = type.lstrip('.') if type else None
    if ext and ext not in ALLOWED_EXTENSIONS:
        raise ValueError(f'Unsupported type: {type}')
    if folder_path is None or not folder_path.strip():
        return korefile.list_files(ext=ext)
    return korefile.list_files(folder_path=_normalise_folder_path(folder_path), ext=ext)


def list_folders() -> list[dict]:
    """Return the KoreFile folder tree."""
    return _folder_tree()


def get_folder_structure() -> list[dict]:
    """Navigation starting point: return the folder tree with document summaries."""
    return _folder_tree_with_files()


def get_file_format_info(
    type: Annotated[KoreFileType, 'Document type: koredoc, koresheet, or kodiag.'],
) -> dict:
    """Return canonical schema, example content, and authoring notes for a KoreDocs file type."""
    return FORMAT_INFO[type]


def create_file(
    folder_path: Annotated[str, 'Folder path in KoreFile, such as "/" or "/Projects". Missing folders are created.'],
    name: Annotated[str, 'Filename ending in .koredoc, .koresheet, or .kodiag.'],
    content: Annotated[str, 'Complete serialized file content. For .koredoc use Markdown. For .koresheet and .kodiag use JSON serialized as a string.'],
    metadata: Annotated[Optional[dict], 'Optional metadata object. If omitted, KoreDocs extracts metadata from content where possible.'] = None,
) -> dict:
    """Create a KoreFile document.

    Use:
    - .koredoc: Markdown text, optionally with YAML frontmatter.
    - .koresheet: JSON object {version, meta, cols, rows, cells}, serialized as a string.
    - .kodiag: JSON object {koreDiag, id, title, settings, nodes, edges}, serialized as a string.

    The content argument must be the complete serialized file content.
    """
    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
    return _create_serialized_file(folder_path, name, ext, content, metadata)


def update_file(
    id: Annotated[int, 'KoreFile document id.'],
    content: Annotated[str, 'Complete replacement file content. For .koredoc use Markdown. For .koresheet and .kodiag use JSON serialized as a string.'],
    metadata: Annotated[Optional[dict], 'Optional replacement metadata object. If omitted, KoreDocs extracts metadata from content where possible.'] = None,
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check. When provided, the file must still be at this revision.'] = None,
) -> dict:
    """Overwrite a KoreFile document's complete content.

    Use:
    - .koredoc: Markdown text, optionally with YAML frontmatter.
    - .koresheet: JSON object {version, meta, cols, rows, cells}, serialized as a string.
    - .kodiag: JSON object {koreDiag, id, title, settings, nodes, edges}, serialized as a string.
    """
    updated = korefile.update_file(id, content, metadata, expected_revision=expected_revision)
    if updated is None:
        raise ValueError(f'File not found: {id}')
    return updated


def delete_file(
    id: Annotated[int, 'KoreFile document id.'],
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check. When provided, the file must still be at this revision.'] = None,
) -> dict:
    """Delete a KoreFile document by id."""
    if not korefile.delete_file(id, expected_revision=expected_revision):
        raise ValueError(f'File not found: {id}')
    return {'ok': True, 'id': id}


# ── Cross-type MCP tools ───────────────────────────────────────────────────

@mcp.tool()
def koredocs_list_supported_types() -> list[dict]:
    """Canonical prefixed alias for list_supported_types."""
    return list_supported_types()


@mcp.tool()
def koredocs_search_files(
    query: Annotated[str, 'Search query. Supports words and quoted phrases.'],
    type: Annotated[Optional[KoreFileType], 'Optional document type filter.'] = None,
    folder_path: Annotated[Optional[str], 'Optional folder path such as "/" or "/01-misc".'] = None,
    limit: Annotated[int, 'Maximum number of results, between 1 and 200.'] = 20,
) -> list[dict]:
    """Canonical prefixed alias for search_files."""
    return search_files(query=query, type=type, folder_path=folder_path, limit=limit)


@mcp.tool()
def koredocs_get_file(id: int) -> dict:
    """Canonical prefixed alias for get_file."""
    return get_file(id)


@mcp.tool()
def koredocs_list_files(
    folder_path: Optional[str] = None,
    type: Annotated[Optional[KoreFileType], 'Optional document type filter.'] = None,
) -> list[dict]:
    """Canonical prefixed alias for list_files."""
    return list_files(folder_path=folder_path, type=type)


@mcp.tool()
def koredocs_list_folders() -> list[dict]:
    """Canonical prefixed alias for list_folders."""
    return list_folders()


@mcp.tool()
def koredocs_get_folder_structure() -> list[dict]:
    """Canonical prefixed alias for get_folder_structure."""
    return get_folder_structure()


@mcp.tool()
def koredocs_get_file_format_info(
    type: Annotated[KoreFileType, 'Document type: koredoc, koresheet, or kodiag.'],
) -> dict:
    """Canonical prefixed alias for get_file_format_info."""
    return get_file_format_info(type)


@mcp.tool()
def koredocs_create_folder(
    path: Annotated[str, 'Folder path in KoreFile, such as "/Projects/Calcs". Missing parents are created automatically.'],
) -> dict:
    """Create a folder in KoreFile and return the resulting folder record.

    Use this tool (not filesystem folder_create or file_write) when the user asks to save,
    create, publish, or organise files in KoreDocs, KoreFile, or KoreFiles. The KoreFile
    storage surface is the correct destination for all KoreDocs document operations.
    """
    normalized = _normalise_folder_path(path)
    _folder_id_for_path(normalized, create=True)
    folder = korefile.get_folder_by_path(normalized)
    if folder is None:
        raise ValueError(f'Folder not found after create: {normalized}')
    return folder


@mcp.tool()
def koredocs_create_file(
    folder_path: Annotated[str, 'Folder path in KoreFile, such as "/" or "/Projects". Missing folders are created.'],
    name: Annotated[str, 'Filename ending in .koredoc, .koresheet, or .kodiag.'],
    content: Annotated[str, 'Complete serialized file content.'],
    metadata: Annotated[Optional[dict], 'Optional metadata object.'] = None,
) -> dict:
    """Create a file in KoreFile. Use this instead of filesystem file_write when the
    destination is KoreDocs, KoreFile, or KoreFiles. For documents use koredocs_create_koredoc;
    for spreadsheets prefer the semantic sheet tools (koredocs_create_sheet_table,
    koredocs_create_compounding_schedule) over raw file creation.
    """
    return create_file(folder_path=folder_path, name=name, content=content, metadata=metadata)


@mcp.tool()
def koredocs_update_file(
    id: Annotated[int, 'KoreFile document id.'],
    content: Annotated[str, 'Complete replacement file content.'],
    metadata: Annotated[Optional[dict], 'Optional replacement metadata object.'] = None,
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Canonical prefixed alias for update_file."""
    return update_file(id=id, content=content, metadata=metadata, expected_revision=expected_revision)


@mcp.tool()
def koredocs_delete_file(
    id: Annotated[int, 'KoreFile document id.'],
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Canonical prefixed alias for delete_file."""
    return delete_file(id=id, expected_revision=expected_revision)


# ── Sub-module imports — triggers @mcp.tool() registration for each type ──

from . import koredoc_mcp as _koredoc_mcp  # noqa: E402, F401
from . import koresheet_mcp as _koresheet_mcp  # noqa: E402, F401
from . import kodiag_mcp as _kodiag_mcp  # noqa: E402, F401

# ── FORMAT_INFO — defined after sub-module imports so helpers are available ─

FORMAT_INFO: dict[str, Any] = {
    'koredoc': {
        'type': 'koredoc',
        'extension': '.koredoc',
        'content_type': 'text/markdown',
        'notes': [
            'Content is Markdown text.',
            'YAML frontmatter is optional and may include title and tags.',
            'The content argument for create_file/update_file must be the complete serialized document.',
        ],
        'schema': {
            'type': 'string',
            'description': 'Markdown text, optionally beginning with YAML frontmatter between --- delimiters.',
        },
        'example': '---\ntitle: Project Notes\ntags: notes, project\n---\n\n# Project Notes\n\nWrite Markdown here.\n',
    },
    'koresheet': {
        'type': 'koresheet',
        'extension': '.koresheet',
        'content_type': 'application/json',
        'notes': [
            'Content is a JSON object serialized as a string.',
            'Only non-default cells are stored in cells.',
            'Cell addresses use A1 notation. Cell values can include formula strings beginning with =.',
            'Prefer semantic creation tools over raw JSON: use koredocs_create_compounding_schedule for compound-interest calculators, koredocs_create_sheet_table for table-shaped spreadsheets, and koredocs_create_koresheet only when explicit A1 cells are required.',
        ],
        'recommended_tools': [
            {
                'tool': 'koredocs_create_compounding_schedule',
                'use_when': 'Compound interest calculator, investment growth schedule, savings projection, or yearly compounding model.',
                'required_args': ['folder_path', 'name', 'principal', 'annual_rate', 'years'],
                'verify_with': 'koredocs_read_sheet_range using the returned id and range A1:D14 for a 10-year schedule.',
            },
            {
                'tool': 'koredocs_create_sheet_table',
                'use_when': 'New spreadsheet from named columns plus initial rows.',
                'required_args': ['folder_path', 'name', 'headers'],
                'verify_with': 'koredocs_preview_sheet or koredocs_read_sheet_table.',
            },
            {
                'tool': 'koredocs_write_sheet_cells',
                'use_when': 'Modify specific A1-addressed cells in an existing sheet.',
                'required_args': ['id', 'cells'],
                'verify_with': 'koredocs_read_sheet_range.',
            },
        ],
        'schema': {
            'type': 'object',
            'required': ['version', 'meta', 'cols', 'rows', 'cells'],
            'properties': {
                'version': {'type': 'integer', 'const': 1},
                'meta': {
                    'type': 'object',
                    'properties': {
                        'title': {'type': 'string'},
                        'created': {'type': 'string'},
                    },
                },
                'cols': {'type': 'integer', 'default': 26},
                'rows': {'type': 'integer', 'default': 100},
                'cells': {
                    'type': 'object',
                    'additionalProperties': {
                        'type': 'object',
                        'properties': {
                            'value': {'type': ['string', 'number', 'boolean']},
                            'formula': {'type': 'string'},
                            'computed': {},
                            'style': {'type': 'object'},
                        },
                    },
                },
            },
        },
        'example': _koresheet_mcp._sheet_content('Example Sheet', {'A1': {'value': 'Name'}, 'B1': {'value': 'Score'}}),
    },
    'kodiag': {
        'type': 'kodiag',
        'extension': '.kodiag',
        'content_type': 'application/json',
        'notes': [
            'Content is a JSON object serialized as a string.',
            'Nodes are rectangles, ellipses, or waypoints.',
            'Edges reference node ids through from/to and may use compass ports like n, e, s, w.',
        ],
        'schema': {
            'type': 'object',
            'required': ['koreDiag', 'id', 'title', 'settings', 'nodes', 'edges'],
            'properties': {
                'koreDiag': {'type': 'string', 'const': '1.0'},
                'id': {'type': 'string'},
                'title': {'type': 'string'},
                'created': {'type': 'string'},
                'modified': {'type': 'string'},
                'settings': {'type': 'object'},
                'nodes': {'type': 'array', 'items': {'type': 'object'}},
                'edges': {'type': 'array', 'items': {'type': 'object'}},
            },
        },
        'example': _kodiag_mcp._diag_content('Example Diagram'),
    },
}

# ── Re-exports for server.py compatibility ─────────────────────────────────

from .koresheet_mcp import (  # noqa: E402, F401
    get_sheet,
    read_sheet_range,
    write_sheet_cells,
    read_sheet_table,
    append_sheet_rows,
    upsert_sheet_rows,
    clear_sheet_range,
)
