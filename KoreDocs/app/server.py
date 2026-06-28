# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI monolith for KoreDocs — document management, web UI, and MCP server.
#
# Serves all front ends (document editor, sheet editor, diagram editor, KoreFile browser)
# and exposes the file API used by the MCP tool layer.  Supports two startup modes:
#   web UI + MCP SSE/HTTP  -- default
#   web UI + MCP stdio     -- python main.py --mcp-stdio
#
# Run with:
#   python main.py                 # MCP SSE/HTTP + web UI (port from config)
#   python main.py --mcp-stdio    # MCP stdio + web UI
#   python main.py --port 8080    # override port at runtime
#
# Constants:
#   DATA_DIR   -- KoreFiles data directory
#   DB_PATH    -- SQLite database path (passed to korefile.configure)
#   LOG_PATH   -- log file path
#   API_TOKEN  -- optional bearer token for MCP endpoint authentication
#
# Related modules:
#   - app/korefile.py      -- KoreFile virtual file system (SQLite + FTS5)
#   - app/koredocs_mcp.py  -- MCP tool assembler (registers all tool handlers)
#   - app/config.py        -- cfg (host, port)
#   - static/              -- bundled single-page web application
# ====================================================================================================

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from pydantic import BaseModel

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.endpoint_manifest import build_endpoint_manifest
from . import korefile
from .config import cfg as _cfg
from .koredocs_mcp import (
    FORMAT_INFO,
    append_sheet_rows,
    clear_sheet_range,
    get_sheet,
    mcp,
    read_sheet_range,
    read_sheet_table,
    upsert_sheet_rows,
    write_sheet_cells,
)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; set env vars externally if not installed

# ── Configuration ──────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent
STATIC   = Path(
    os.environ.get(
        'KORE_KOREDOCS_STATIC_DIR',
        str(BASE_DIR.parent / 'KoreUI' / 'KoreDocs' / 'static'),
    )
).resolve()
SUITE_ROOT = Path(os.environ.get('KORE_SUITE_ROOT', str(BASE_DIR.parent))).resolve()
SUITE_DATACONTROL = Path(os.environ.get('KORE_SUITE_DATACONTROL', str(SUITE_ROOT / 'datacontrol'))).resolve()
SUITE_DATAUSER = Path(os.environ.get('KORE_SUITE_DATAUSER', str(SUITE_ROOT / 'datauser'))).resolve()
COMMONUI_ASSETS = Path(os.environ.get('KORE_UIELEMENTS_ASSETS_DIR', str(BASE_DIR.parent / 'UIElements' / 'assets')))
if not COMMONUI_ASSETS.exists():
    COMMONUI_ASSETS = STATIC / 'shared'
DATA_DIR = Path(os.environ.get('KOREDOCS_DATA_DIR', str(SUITE_DATAUSER / 'KoreFiles')))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONTROL_DIR = Path(os.environ.get('KOREDOCS_CONTROL_DIR', str(SUITE_DATACONTROL / 'koredocs')))
CONTROL_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH  = CONTROL_DIR / 'korefile.db'
LOG_PATH = SUITE_DATACONTROL / 'logs' / 'koredocs' / 'koredocs.log'
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

korefile.configure(DB_PATH)

ALLOWED_EXTENSIONS = frozenset({'.koredoc', '.koresheet', '.kodiag'})
API_TOKEN = os.environ.get('KOREDOCS_API_TOKEN')

# Ensure the korefile static folder exists (needed for StaticFiles mount at startup)
(STATIC / 'korefile' / 'js').mkdir(parents=True, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────────────────

class _TailFileHandler(logging.FileHandler):
    """File handler that keeps only the most recent *max_lines* lines.

    Trims in batches (every max_lines + 100 emits) to avoid rewriting the
    file on every log call.  Called from within logging's RLock so no extra
    locking is needed.
    """

    def __init__(self, filename: str, max_lines: int = 1000) -> None:
        self._max_lines = max_lines
        self._line_count = 0
        super().__init__(filename, mode='a', encoding='utf-8', delay=False)
        try:
            with open(self.baseFilename, encoding='utf-8', errors='replace') as fh:
                self._line_count = sum(1 for _ in fh)
        except FileNotFoundError:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self._line_count += 1
        if self._line_count >= self._max_lines + 100:
            try:
                self.flush()
                with open(self.baseFilename, encoding='utf-8', errors='replace') as fh:
                    lines = fh.readlines()
                keep = lines[-self._max_lines:]
                with open(self.baseFilename, 'w', encoding='utf-8') as fh:
                    fh.writelines(keep)
                self._line_count = self._max_lines
            except Exception:
                pass


def _setup_logging() -> None:
    handler = _TailFileHandler(str(LOG_PATH), max_lines=1000)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s  %(levelname)-8s  %(name)-24s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


# ── App ────────────────────────────────────────────────────────────────────

_mcp_http_app = mcp.http_app(path='/', transport='streamable-http')


@asynccontextmanager
async def _lifespan(app: FastAPI):
    async with _mcp_http_app.router.lifespan_context(_mcp_http_app):
        korefile.init_db()
        yield


app = FastAPI(title='KoreDocs', lifespan=_lifespan)


@app.get('/__endpoint_manifest', include_in_schema=False)
def endpoint_manifest() -> dict:
    return build_endpoint_manifest(app, service_key='koredocs', service_label='KoreDocs')


class _NoCacheMiddleware(BaseHTTPMiddleware):
    """Add Cache-Control: no-store to every /static/ response.

    This runs AFTER the response is built, so it works for 200, 304, and
    any other status that StaticFiles may return.  The browser will never
    use a stored copy of a static asset — every request goes to the server.
    """
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if (
            request.url.path.startswith('/static/')
            or request.url.path.startswith('/ui-elements/assets/')
            or request.url.path.startswith('/static/commonui/')
        ):
            response.headers['cache-control'] = 'no-store'
        return response


class _AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not API_TOKEN:
            return await call_next(request)
        path = request.url.path
        protected = (
            path.startswith('/mcp')
            or (path.startswith('/api/') and request.method != 'OPTIONS')
        )
        if not protected:
            return await call_next(request)
        token = request.headers.get('x-koredocs-token', '')
        auth = request.headers.get('authorization', '')
        if auth.lower().startswith('bearer '):
            token = auth[7:].strip()
        if token != API_TOKEN:
            return Response('Unauthorized', status_code=401)
        return await call_next(request)


app.add_middleware(_NoCacheMiddleware)
app.add_middleware(_AuthMiddleware)

# Each editor's static folder is mounted at /static/<name>
app.mount('/static/doc',    StaticFiles(directory=STATIC / 'doc'),    name='doc')
app.mount('/static/sheet',  StaticFiles(directory=STATIC / 'sheet'),  name='sheet')
app.mount('/static/diag',   StaticFiles(directory=STATIC / 'diag'),   name='diag')
app.mount('/static/textedit', StaticFiles(directory=STATIC / 'textedit'), name='textedit')
app.mount('/static/korefile', StaticFiles(directory=STATIC / 'korefile'), name='korefile')
app.mount('/ui-elements/assets', StaticFiles(directory=COMMONUI_ASSETS), name='ui-elements-assets')
app.mount('/static/commonui', StaticFiles(directory=COMMONUI_ASSETS), name='commonui')
app.mount('/static/shared', StaticFiles(directory=STATIC / 'shared'), name='shared')
app.mount('/mcp', _mcp_http_app, name='mcp')

# ── HTML routes ────────────────────────────────────────────────────────────

@app.get('/status', include_in_schema=False)
def health():
    return {'status': 'ok', 'service': 'koredocs'}

@app.get('/suite-config.js', include_in_schema=False)
def suite_config_js():
    urls = os.environ.get('KORE_SUITE_URLS', '{}')
    return Response(content=f'window.__koreSuiteUrls = {urls};', media_type='application/javascript', headers={'Cache-Control': 'no-store'})

@app.get('/ui', include_in_schema=False)
def serve_ui():
    return FileResponse(STATIC / 'korefile' / 'index.html')

@app.get('/', include_in_schema=False)
def root():
    return RedirectResponse('/ui')

@app.get('/doc', include_in_schema=False)
def serve_doc():
    return FileResponse(STATIC / 'doc' / 'index.html')

@app.get('/sheet', include_in_schema=False)
def serve_sheet():
    return FileResponse(STATIC / 'sheet' / 'index.html')

@app.get('/diag', include_in_schema=False)
def serve_diag():
    return FileResponse(STATIC / 'diag' / 'index.html')


@app.get('/textedit', include_in_schema=False)
def serve_textedit():
    return FileResponse(STATIC / 'textedit' / 'index.html')

# ── File API ───────────────────────────────────────────────────────────────

def _resolve(name: str) -> Path:
    """Resolve *name* to a safe absolute path inside DATA_DIR.

    Raises HTTP 400 if the name is empty, contains path separators or dotdot,
    resolves outside DATA_DIR (path-traversal guard), or has an unsupported
    file extension.
    """
    if not name:
        raise HTTPException(status_code=400, detail='Empty filename')
    if any(c in name for c in ('/', '\\', ':')):
        raise HTTPException(status_code=400, detail='Filename must not contain path separators')
    # Reject dotdot regardless of position
    if '..' in name.split('.'):
        raise HTTPException(status_code=400, detail='Invalid filename')

    path = (DATA_DIR / name).resolve()

    # Path-traversal guard: resolved path must still be inside DATA_DIR
    try:
        path.relative_to(DATA_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail='Invalid filename')

    if path.suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{path.suffix}'. "
                   f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    return path


class _WriteBody(BaseModel):
    content: str


class _CreateBody(BaseModel):
    name: str
    content: str


def _validate_flat_content(name: str, content: str) -> None:
    korefile._validate_serialized_content(name, content)


def _file_etag(path: Path) -> str:
    stat = path.stat()
    return f'W/"{stat.st_mtime_ns}-{stat.st_size}"'


def _enforce_file_match(request: Request, path: Path) -> None:
    if not path.exists():
        return
    expected = request.headers.get('if-match')
    if expected and expected != _file_etag(path):
        raise HTTPException(status_code=409, detail='File changed on disk; reload before writing')


def _create_flat_file_atomically(path: Path, content: str) -> None:
    try:
        with path.open('x', encoding='utf-8') as handle:
            handle.write(content)
    except FileExistsError:
        raise HTTPException(status_code=409, detail='File already exists')


@app.get('/api/legacy/files')
def list_files(type: Annotated[str | None, Query()] = None):
    """List files in the data directory.

    Optional ``?type=koredoc`` filter (value is the extension without the dot).
    """
    ext_filter = f'.{type}' if type else None
    result = []
    for p in sorted(DATA_DIR.iterdir()):
        if not p.is_file():
            continue
        if p.suffix not in ALLOWED_EXTENSIONS:
            continue
        if ext_filter and p.suffix != ext_filter:
            continue
        stat = p.stat()
        result.append({
            'name':     p.name,
            'type':     p.suffix.lstrip('.'),
            'size':     stat.st_size,
            'modified': stat.st_mtime,
        })
    return result


@app.get('/api/legacy/files/{name}')
def read_file(name: str):
    """Return the raw text content of a file."""
    path = _resolve(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail='File not found')
    response = PlainTextResponse(path.read_text(encoding='utf-8'))
    response.headers['etag'] = _file_etag(path)
    return response


@app.put('/api/legacy/files/{name}')
def write_file(name: str, body: _WriteBody, request: Request):
    """Overwrite (or create) a file with the given content."""
    path = _resolve(name)
    _enforce_file_match(request, path)
    _validate_flat_content(name, body.content)
    path.write_text(body.content, encoding='utf-8')
    response = JSONResponse({'ok': True, 'name': name})
    response.headers['etag'] = _file_etag(path)
    return response


@app.delete('/api/legacy/files/{name}')
def delete_file(name: str, request: Request):
    """Delete a file."""
    path = _resolve(name)
    if not path.exists():
        raise HTTPException(status_code=404, detail='File not found')
    _enforce_file_match(request, path)
    path.unlink()
    return {'ok': True}


@app.post('/api/legacy/files')
def create_file(body: _CreateBody):
    """Create a new file. Returns 409 if it already exists."""
    path = _resolve(body.name)
    _validate_flat_content(body.name, body.content)
    _create_flat_file_atomically(path, body.content)
    response = JSONResponse({'ok': True, 'name': body.name})
    response.headers['etag'] = _file_etag(path)
    return response


@app.get('/api/schema')
def list_schemas(type: Annotated[str | None, Query()] = None):
    """Return file format schemas and examples for supported KoreDocs types."""
    if type is None:
        return [FORMAT_INFO[key] for key in sorted(FORMAT_INFO)]
    if type not in FORMAT_INFO:
        raise HTTPException(status_code=404, detail=f'Unknown type: {type}')
    return FORMAT_INFO[type]


# ── KoreFile API — virtual file system ────────────────────────────────────

class _KfFolderCreate(BaseModel):
    name: str
    parent_id: int = 1


class _KfFolderPatch(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    expected_revision: Optional[int] = None


class _KfFilePatch(BaseModel):
    name: Optional[str] = None
    folder_id: Optional[int] = None
    expected_revision: Optional[int] = None


class _KfFileCreate(BaseModel):
    folder_id: int
    name: str
    content: str
    metadata: Optional[dict] = None


class _KfFileUpdate(BaseModel):
    content: Optional[str] = None
    metadata: Optional[dict] = None
    expected_revision: Optional[int] = None


class _KfSheetCellsWrite(BaseModel):
    cells: dict[str, Any]
    expected_revision: Optional[int] = None


class _TextEditSaveBody(BaseModel):
    file_id: Optional[int] = None
    path: Optional[str] = None
    content: str
    expected_revision: Optional[int] = None


_TEXTEDIT_MAX_BYTES = 4 * 1024 * 1024


def _resolve_textedit_path(path_value: str) -> Path:
    raw = (path_value or '').strip()
    if not raw:
        raise HTTPException(status_code=400, detail='Path is required')
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (SUITE_ROOT / candidate).resolve()
    else:
        candidate = candidate.resolve()
    try:
        candidate.relative_to(SUITE_ROOT)
    except ValueError:
        raise HTTPException(status_code=400, detail='Path must be inside the suite root')
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail='File not found')
    return candidate


@app.get('/api/textedit/open', summary='Open any file as plain UTF-8 text')
def textedit_open(
    file_id: Annotated[int | None, Query()] = None,
    path: Annotated[str | None, Query()] = None,
):
    if (file_id is None) == (path is None):
        raise HTTPException(status_code=400, detail='Provide exactly one of file_id or path')

    if file_id is not None:
        f = korefile.get_file(file_id, include_content=True)
        if f is None:
            raise HTTPException(status_code=404, detail='File not found')
        content = f.get('content') or ''
        return {
            'source': 'korefile',
            'file_id': f.get('id'),
            'name': f.get('name'),
            'revision': f.get('revision'),
            'content': content,
            'encoding': 'utf-8',
            'byte_length': len(content.encode('utf-8')),
            'truncated': False,
        }

    disk_path = _resolve_textedit_path(path or '')
    raw = disk_path.read_bytes()
    truncated = False
    total_len = len(raw)
    if total_len > _TEXTEDIT_MAX_BYTES:
        raw = raw[:_TEXTEDIT_MAX_BYTES]
        truncated = True
    rel = str(disk_path.relative_to(SUITE_ROOT)).replace('\\', '/')
    return {
        'source': 'filesystem',
        'path': rel,
        'full_path': str(disk_path),
        'content': raw.decode('utf-8', errors='replace'),
        'encoding': 'utf-8 (replacement for invalid bytes)',
        'byte_length': total_len,
        'truncated': truncated,
    }


@app.put('/api/textedit/save', summary='Save plain text back to KoreFile or filesystem')
def textedit_save(body: _TextEditSaveBody):
    if (body.file_id is None) == (body.path is None):
        raise HTTPException(status_code=400, detail='Provide exactly one of file_id or path')

    if body.file_id is not None:
        try:
            updated = korefile.update_file(
                body.file_id,
                body.content,
                metadata=None,
                expected_revision=body.expected_revision,
            )
            if updated is None:
                raise HTTPException(status_code=404, detail='File not found')
            return {
                'ok': True,
                'source': 'korefile',
                'file_id': updated.get('id'),
                'revision': updated.get('revision'),
            }
        except korefile.ConflictError:
            raise HTTPException(status_code=409, detail='File changed in the background; refresh and retry.')

    disk_path = _resolve_textedit_path(body.path or '')
    disk_path.write_text(body.content, encoding='utf-8')
    return {
        'ok': True,
        'source': 'filesystem',
        'path': str(disk_path.relative_to(SUITE_ROOT)).replace('\\', '/'),
    }


class _KfSheetRowsAppend(BaseModel):
    rows: list[Any]
    start_col: str = 'A'
    header_row: Optional[int] = None
    expected_revision: Optional[int] = None


class _KfSheetRowsUpsert(BaseModel):
    rows: list[dict[str, Any]]
    key_columns: list[str]
    header_row: int = 1
    create_missing_columns: bool = False
    expected_revision: Optional[int] = None


class _KfSheetClearRange(BaseModel):
    range: str
    expected_revision: Optional[int] = None


# Folders

@app.get('/api/folders', summary='List all folders (flat, ordered by path)')
def kf_list_folders():
    return korefile.list_folders()


@app.post('/api/folders', status_code=201, summary='Create a folder')
def kf_create_folder(body: _KfFolderCreate):
    try:
        return korefile.create_folder(body.name, body.parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if 'UNIQUE' in str(e):
            raise HTTPException(status_code=409, detail='A folder with that name already exists here')
        raise HTTPException(status_code=400, detail=str(e))


@app.delete('/api/folders/{folder_id}', summary='Delete a folder')
def kf_delete_folder(
    folder_id: int,
    expected_revision: Annotated[int | None, Query()] = None,
    recursive: Annotated[bool, Query()] = False,
):
    try:
        if not korefile.delete_folder(folder_id, expected_revision=expected_revision, recursive=recursive):
            raise HTTPException(status_code=404, detail='Folder not found')
    except HTTPException:
        raise
    except korefile.ConflictError:
        raise HTTPException(status_code=409, detail='Folder changed in the background; refresh and try again.')
    except Exception as e:
        detail = str(e)
        if 'FOREIGN KEY constraint failed' in detail:
            detail = 'Folder is not empty. Confirm recursive delete or move its files and sub-folders first.'
        raise HTTPException(status_code=409, detail=detail)
    return {'ok': True}


@app.patch('/api/folders/{folder_id}', summary='Rename or move a folder')
def kf_patch_folder(folder_id: int, body: _KfFolderPatch):
    if body.name is None and body.parent_id is None:
        raise HTTPException(status_code=400, detail='Provide name and/or parent_id')
    try:
        result = None
        expected_revision = body.expected_revision
        if body.name is not None:
            result = korefile.rename_folder(folder_id, body.name, expected_revision=expected_revision)
            expected_revision = result['revision'] if result else expected_revision
        if body.parent_id is not None:
            result = korefile.move_folder(folder_id, body.parent_id, expected_revision=expected_revision)
        return result
    except korefile.ConflictError:
        raise HTTPException(status_code=409, detail='Folder changed in the background; refresh and try again.')
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if 'UNIQUE' in str(e):
            raise HTTPException(status_code=409, detail='A folder with that name already exists here')
        raise HTTPException(status_code=400, detail=str(e))


# Files

@app.get('/api/files', summary='List files (metadata only)')
def kf_list_files(
    folder_id:   Annotated[int | None, Query()] = None,
    folder_path: Annotated[str | None, Query()] = None,
    type:        Annotated[str | None, Query()] = None,
    name:        Annotated[str | None, Query()] = None,
    limit:       Annotated[int | None, Query(ge=1, le=500)] = None,
):
    return korefile.list_files(folder_id=folder_id, folder_path=folder_path, ext=type, name=name, limit=limit)


@app.get('/api/files/{file_id}', summary='Get a file with full content')
def kf_get_file(file_id: int, include_content: Annotated[bool, Query()] = True):
    f = korefile.get_file(file_id, include_content=include_content)
    if f is None:
        raise HTTPException(status_code=404, detail='File not found')
    return f


@app.post('/api/files', status_code=201, summary='Create a file')
def kf_create_file(body: _KfFileCreate):
    try:
        return korefile.create_file(
            body.folder_id, body.name, body.content, body.metadata
        )
    except Exception as e:
        if 'UNIQUE' in str(e):
            raise HTTPException(status_code=409, detail='A file with that name already exists in this folder')
        raise HTTPException(status_code=400, detail=str(e))


@app.put('/api/files/{file_id}', summary='Update a file')
def kf_update_file(file_id: int, body: _KfFileUpdate):
    try:
        updated = korefile.update_file(file_id, body.content, body.metadata, body.expected_revision)
        if updated is None:
            raise HTTPException(status_code=404, detail='File not found')
        return updated
    except korefile.ConflictError:
        raise HTTPException(status_code=409, detail='File changed in the background; refreshing to the latest version.')


@app.patch('/api/files/{file_id}', summary='Rename or move a file')
def kf_patch_file(file_id: int, body: _KfFilePatch):
    if body.name is None and body.folder_id is None:
        raise HTTPException(status_code=400, detail='Provide name and/or folder_id')
    try:
        result = None
        expected_revision = body.expected_revision
        if body.name is not None:
            result = korefile.rename_file(file_id, body.name, expected_revision=expected_revision)
            if result is None:
                raise HTTPException(status_code=404, detail='File not found')
            expected_revision = result['revision']
        if body.folder_id is not None:
            result = korefile.move_file(file_id, body.folder_id, expected_revision=expected_revision)
            if result is None:
                raise HTTPException(status_code=404, detail='File not found')
        return result
    except HTTPException:
        raise
    except korefile.ConflictError:
        raise HTTPException(status_code=409, detail='File changed in the background; refresh and try again.')
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if 'UNIQUE' in str(e):
            raise HTTPException(status_code=409, detail='A file with that name already exists in this folder')
        raise HTTPException(status_code=400, detail=str(e))


@app.delete('/api/files/{file_id}', summary='Delete a file')
def kf_delete_file(file_id: int, expected_revision: Annotated[int | None, Query()] = None):
    try:
        if not korefile.delete_file(file_id, expected_revision=expected_revision):
            raise HTTPException(status_code=404, detail='File not found')
    except HTTPException:
        raise
    except korefile.ConflictError:
        raise HTTPException(status_code=409, detail='File changed in the background; refresh and try again.')
    return {'ok': True}


@app.get('/api/sheets/{file_id}', summary='Get KoreSheet metadata and optional sparse cells')
def kf_get_sheet(file_id: int, include_cells: Annotated[bool, Query()] = False):
    try:
        return get_sheet(file_id, include_cells=include_cells)
    except ValueError as e:
        detail = str(e)
        raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)


@app.get('/api/sheets/{file_id}/range', summary='Read an A1-style range from a KoreSheet')
def kf_read_sheet_range(
    file_id: int,
    range: Annotated[str, Query()],
    values_only: Annotated[bool, Query()] = False,
):
    try:
        return read_sheet_range(file_id, range=range, values_only=values_only)
    except ValueError as e:
        detail = str(e)
        raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)


@app.get('/api/sheets/{file_id}/table', summary='Read a KoreSheet region as header-keyed rows')
def kf_read_sheet_table(
    file_id: int,
    header_row: Annotated[int, Query(ge=1)] = 1,
    range: Annotated[str | None, Query()] = None,
):
    try:
        return read_sheet_table(file_id, header_row=header_row, range_ref=range)
    except ValueError as e:
        detail = str(e)
        raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)


@app.post('/api/sheets/{file_id}/cells', summary='Apply sparse A1-addressed cell updates to a KoreSheet')
def kf_write_sheet_cells(file_id: int, body: _KfSheetCellsWrite):
    try:
        return write_sheet_cells(file_id, body.cells, expected_revision=body.expected_revision)
    except korefile.ConflictError:
        raise HTTPException(status_code=409, detail='Sheet changed in the background; refresh and try again.')
    except ValueError as e:
        detail = str(e)
        raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)


@app.post('/api/sheets/{file_id}/rows/append', summary='Append rows to a KoreSheet')
def kf_append_sheet_rows(file_id: int, body: _KfSheetRowsAppend):
    try:
        return append_sheet_rows(file_id, body.rows, start_col=body.start_col, header_row=body.header_row, expected_revision=body.expected_revision)
    except korefile.ConflictError:
        raise HTTPException(status_code=409, detail='Sheet changed in the background; refresh and try again.')
    except ValueError as e:
        detail = str(e)
        raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)


@app.post('/api/sheets/{file_id}/rows/upsert', summary='Update or append rows in a KoreSheet by key columns')
def kf_upsert_sheet_rows(file_id: int, body: _KfSheetRowsUpsert):
    try:
        return upsert_sheet_rows(
            file_id,
            body.rows,
            key_columns=body.key_columns,
            header_row=body.header_row,
            create_missing_columns=body.create_missing_columns,
            expected_revision=body.expected_revision,
        )
    except korefile.ConflictError:
        raise HTTPException(status_code=409, detail='Sheet changed in the background; refresh and try again.')
    except ValueError as e:
        detail = str(e)
        raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)


@app.post('/api/sheets/{file_id}/range/clear', summary='Clear a range in a KoreSheet')
def kf_clear_sheet_range(file_id: int, body: _KfSheetClearRange):
    try:
        return clear_sheet_range(file_id, body.range, expected_revision=body.expected_revision)
    except korefile.ConflictError:
        raise HTTPException(status_code=409, detail='Sheet changed in the background; refresh and try again.')
    except ValueError as e:
        detail = str(e)
        raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)


# Search

@app.get('/api/search', summary='Full-text search across all KoreFile documents')
def kf_search(
    q:           str,
    type:        Annotated[str | None, Query()] = None,
    folder_path: Annotated[str | None, Query()] = None,
    limit:       Annotated[int,        Query(ge=1, le=200)] = 20,
):
    try:
        return korefile.search(q, ext=type, folder_path=folder_path, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# Import

@app.post('/api/import-fs', summary='Import flat-FS files into KoreFile DB')
def kf_import_fs():
    """Walk KOREDOCS_DATA_DIR and import every *.kore* file.
    Files already present are skipped (not overwritten).
    """
    return korefile.import_from_fs(DATA_DIR)


# ── Entry point ────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import asyncio
    import json
    import sys
    import threading
    import uvicorn

    parser = argparse.ArgumentParser(description='KoreDocs server')
    parser.add_argument('--host', default=_cfg['host'],
                        help=f'Bind address (default: {_cfg["host"]})')
    parser.add_argument('--port', type=int, default=_cfg['port'],
                        help=f'HTTP port (default: {_cfg["port"]})')
    parser.add_argument('--mcp-stdio', action='store_true',
                        help='Run MCP protocol on stdin/stdout; '
                             'web UI still starts on HTTP in a background thread')
    args = parser.parse_args(argv)

    _setup_logging()
    logging.getLogger(__name__).info(
        'KoreDocs starting — data=%s  db=%s  log=%s', DATA_DIR, DB_PATH, LOG_PATH
    )
    korefile.init_db()

    def _mcp_tool_names() -> list[str]:
        async def _list() -> list[str]:
            tools = await mcp.list_tools()
            return [tool.name for tool in tools]

        return asyncio.run(_list())

    def _startup_report(host: str, port: int, stream=None, include_stdio: bool = False) -> None:
        stream = stream or sys.stdout
        url = f'http://{host}:{port}'
        print(f'[KoreDocs]  {url}/ui', file=stream)
        print(f'[KoreDocs]  MCP endpoint: {url}/mcp', file=stream)
        if include_stdio:
            config = {
                'koredocs': {
                    'command': sys.executable,
                    'args': [str(BASE_DIR / 'server.py'), '--mcp-stdio'],
                },
            }
            print('[KoreDocs]  MCP stdio config:', file=stream)
            print(json.dumps(config, indent=2), file=stream)
        print('[KoreDocs]  MCP tools: ' + ', '.join(_mcp_tool_names()), file=stream)
        print(file=stream)
        # Folders
        folders = korefile.list_folders()
        roots = [f for f in folders if f['parent_id'] is None]
        if roots:
            # Build id→children map for indented display
            children: dict[int, list] = {f['id']: [] for f in folders}
            for f in folders:
                if f['parent_id'] is not None:
                    children[f['parent_id']].append(f)
            def _print_tree(fid: int, indent: int) -> None:
                folder = next(f for f in folders if f['id'] == fid)
                prefix = '  ' * indent + ('└─ ' if indent else '')
                files = korefile.list_files(folder_id=fid)
                file_names = ', '.join(f['name'] for f in files) if files else ''
                suffix = f'  [{file_names}]' if file_names else ''
                print(f'  {prefix}{folder["name"]}/{suffix}', file=stream)
                for child in sorted(children[fid], key=lambda x: x['name']):
                    _print_tree(child['id'], indent + 1)
            for root in sorted(roots, key=lambda x: x['name']):
                _print_tree(root['id'], 0)
        else:
            # No folders yet — show any unfoldered files or empty DB notice
            all_files = korefile.list_files()
            if all_files:
                for f in all_files:
                    print(f'  {f["name"]}', file=stream)
            else:
                print('  (no files yet — open Files to get started)', file=stream)
        print(file=stream)

    _uvicorn_kwargs = dict(
        app=app,
        host=args.host,
        port=args.port,
        log_config=None,   # we own logging; don't let uvicorn override it
    )

    if args.mcp_stdio:
        # ── stdio MCP transport mode ──────────────────────────────────────
        # Uvicorn runs in a *daemon* thread so that when the main thread
        # (the MCP stdio loop) exits — whether normally or via Ctrl+C /
        # SIGINT — Python's threading teardown kills the daemon thread
        # automatically.  No explicit shutdown signalling is needed.
        t = threading.Thread(target=uvicorn.run, kwargs=_uvicorn_kwargs, daemon=True)
        t.start()
        _startup_report(args.host, args.port, stream=sys.stderr, include_stdio=True)
        print(f'[KoreDocs] MCP stdio ready', file=sys.stderr, flush=True)
        try:
            mcp.run(transport='stdio', show_banner=False)
        except KeyboardInterrupt:
            pass  # daemon thread is killed automatically on main-thread exit

    else:
        # ── HTTP mode (SSE + HTTP MCP transports) ─────────────────────────
        # uvicorn.run() registers its own SIGINT/SIGTERM handlers and
        # performs a graceful drain of in-flight requests before returning.
        # Ctrl+C is handled entirely by uvicorn — no extra work needed.
        #
        _startup_report(args.host, args.port)
        uvicorn.run(**_uvicorn_kwargs)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
