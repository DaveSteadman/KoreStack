# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreCode — an in-browser code and file editor for the KoreStack suite.
#
# Serves a single-page app that allows browsing, viewing, and editing files in the workspace.
# File access is sandboxed to the suite root with configurable ignored directories.
#
# Endpoints:
#   GET /                    serve static web UI (index.html)
#   GET /suite-config.js     service URL map injected as window.__koreSuiteUrls
#   GET /ui                  redirect to / (canonical UI entry)
#   GET /code                redirect to / (legacy alias)
#   GET /status              health check (service name + version)
#   GET /api/tree            directory tree JSON (filtered by IGNORED_DIRS)
#   GET /api/file?path=      read a file (text or binary, up to MAX_READ_BYTES)
#   PUT /api/file?path=      write a file; validates path stays within workspace root
#
# Constants:
#   IGNORED_DIRS     -- directory names excluded from the tree listing
#   TEXT_EXTENSIONS  -- file extensions treated as UTF-8 text
#   MAX_READ_BYTES   -- maximum file size served raw (1.5 MB)
#
# Related modules:
#   - app/config.py       -- load(), cfg (host / port)
#   - static/             -- bundled single-page web application
# ====================================================================================================
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from .config import cfg as _cfg


BASE_DIR = Path(__file__).parent.parent.resolve()
STATIC_DIR = BASE_DIR / 'static'
SUITE_ROOT = Path(os.environ.get('KORE_SUITE_ROOT', str(BASE_DIR.parent))).resolve()
COMMONUI_ASSETS = Path(
    os.environ.get(
        'KORE_UIELEMENTS_ASSETS_DIR',
        str(BASE_DIR.parent / 'UIElements' / 'assets'),
    )
).resolve()
LOG = logging.getLogger('korecode')

IGNORED_DIRS = {
    '.git',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    '.venv',
    '__pycache__',
    'node_modules',
}
TEXT_EXTENSIONS = {
    '.bat',
    '.cfg',
    '.css',
    '.csv',
    '.html',
    '.ini',
    '.js',
    '.json',
    '.md',
    '.ps1',
    '.py',
    '.pyi',
    '.sql',
    '.toml',
    '.txt',
    '.xml',
    '.yaml',
    '.yml',
}
MAX_READ_BYTES = 1_500_000
MAX_WORKSPACE_PATTERN_HITS = 18

_ACTIVE_ROOT = SUITE_ROOT


def _workspace_root() -> Path:
    return _ACTIVE_ROOT


def _iter_default_roots() -> list[Path]:
    options: list[Path] = [SUITE_ROOT]
    try:
        children = sorted(SUITE_ROOT.iterdir(), key=lambda item: item.name.lower())
    except OSError:
        children = []
    for child in children:
        if not child.is_dir() or child.name in IGNORED_DIRS:
            continue
        options.append(child.resolve())

    extra_roots = os.environ.get('KORECODE_EXTRA_ROOTS', '').strip()
    if extra_roots:
        for chunk in extra_roots.split(';'):
            raw = chunk.strip()
            if not raw:
                continue
            try:
                candidate = Path(raw).expanduser().resolve()
            except OSError:
                continue
            if candidate.is_dir() and candidate not in options:
                options.append(candidate)

    return options


def _root_label_for(path: Path) -> str:
    try:
        rel = path.relative_to(SUITE_ROOT).as_posix()
        return SUITE_ROOT.name if not rel else rel
    except ValueError:
        return str(path)


def _normalize_requested_root(value: str) -> Path:
    raw = (value or '').strip()
    if not raw:
        return SUITE_ROOT
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (SUITE_ROOT / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def _root_options_payload() -> dict:
    options = []
    current = _workspace_root()
    all_roots = _iter_default_roots()
    if current not in all_roots:
        all_roots.append(current)
    for root in all_roots:
        options.append({'value': str(root), 'label': _root_label_for(root), 'path': str(root)})
    return {
        'current': str(current),
        'current_path': str(current),
        'options': options,
    }


def _set_workspace_root(value: str) -> Path:
    global _ACTIVE_ROOT
    candidate = _normalize_requested_root(value)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='Root folder not found')
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail='Root must be a directory')
    _ACTIVE_ROOT = candidate
    return _ACTIVE_ROOT


def _list_browse_directories(path: str | None = None) -> dict:
    raw = (path or '').strip()

    if os.name == 'nt' and not raw:
        directories = []
        for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            drive = Path(f'{letter}:/')
            if drive.exists():
                directories.append({'name': f'{letter}:', 'path': str(drive)})
        return {
            'path': '',
            'parent': None,
            'directories': directories,
        }

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (Path.cwd() / candidate).resolve()
    else:
        candidate = candidate.resolve()

    if not candidate.exists():
        raise HTTPException(status_code=404, detail='Browse path not found')
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail='Browse path is not a directory')

    try:
        children = sorted(candidate.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f'Unable to read browse path: {exc}') from exc

    directories = []
    for child in children:
        if not child.is_dir():
            continue
        if child.name in IGNORED_DIRS:
            continue
        directories.append({'name': child.name, 'path': str(child.resolve())})
        if len(directories) >= 500:
            break

    parent = str(candidate.parent) if candidate.parent != candidate else None
    return {
        'path': str(candidate),
        'parent': parent,
        'directories': directories,
    }


class WriteBody(BaseModel):
    content: str
    expected_modified_at: int | None = None
    expected_modified_at_ns: int | None = None
    expected_hash: str | None = None


class RootBody(BaseModel):
    root: str = ''


def _resolve_relative_path(value: str) -> Path:
    root = _workspace_root()
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Path escapes workspace root') from exc
    return candidate


def _to_posix(path: Path) -> str:
    root = _workspace_root()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return ''


def _is_probably_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    try:
        with path.open('rb') as handle:
            sample = handle.read(2048)
    except OSError:
        return False
    if b'\x00' in sample:
        return False
    return True


def _read_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    if len(raw) > MAX_READ_BYTES:
        raise HTTPException(status_code=413, detail='File too large for editor view')
    for encoding in ('utf-8', 'utf-8-sig', 'cp1252'):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=415, detail='File is not a supported text encoding')


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _line_window(lines: list[str], start_line: int, end_line: int, pad: int) -> dict:
    total = len(lines)
    from_line = max(1, start_line - pad)
    to_line = min(total, end_line + pad)
    segment = lines[from_line - 1:to_line]
    return {
        'from_line': from_line,
        'to_line': to_line,
        'content': '\n'.join(segment),
    }


def _python_symbol_context(path: Path, content: str, start_line: int, end_line: int) -> dict:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return {'language': 'python', 'symbol': None, 'imports': []}

    imports: list[str] = []
    symbol = None
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.append('import ' + ', '.join(alias.name for alias in node.names))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            imports.append(f"from {module} import " + ', '.join(alias.name for alias in node.names))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        node_start = getattr(node, 'lineno', None)
        node_end = getattr(node, 'end_lineno', None)
        if node_start is None or node_end is None:
            continue
        if node_start <= start_line and node_end >= end_line:
            kind = 'class' if isinstance(node, ast.ClassDef) else 'function'
            if symbol is None or (node_end - node_start) < (symbol['end_line'] - symbol['start_line']):
                symbol = {
                    'name': node.name,
                    'kind': kind,
                    'start_line': node_start,
                    'end_line': node_end,
                }

    return {
        'language': 'python',
        'symbol': symbol,
        'imports': imports[:40],
    }


def _reference_hits(path: Path, token: str) -> list[dict]:
    if not token or len(token) < 2:
        return []
    hits: list[dict] = []
    max_hits = 25
    root = _workspace_root()
    for candidate in root.rglob('*.py'):
        if any(part in IGNORED_DIRS for part in candidate.parts):
            continue
        try:
            rel = _to_posix(candidate)
            lines = candidate.read_text(encoding='utf-8', errors='ignore').splitlines()
        except OSError:
            continue
        for idx, line in enumerate(lines, start=1):
            if token in line:
                hits.append({'path': rel, 'line': idx, 'preview': line[:160]})
                if len(hits) >= max_hits:
                    return hits
    return hits


def _workspace_patterns_from_refs(path: Path, refs: list[dict]) -> list[dict]:
    current_rel = _to_posix(path)
    patterns: list[dict] = []
    for ref in refs:
        rel = str(ref.get('path') or '')
        if not rel or rel == current_rel:
            continue
        patterns.append({
            'path': rel,
            'line': int(ref.get('line') or 1),
            'preview': str(ref.get('preview') or '')[:180],
        })
        if len(patterns) >= MAX_WORKSPACE_PATTERN_HITS:
            break
    return patterns


def _build_context_pack(path: Path, start_line: int | None, end_line: int | None, query: str | None = None, include_workspace: bool = False) -> dict:
    content, _ = _read_text(path)
    lines = content.splitlines()
    total_lines = max(1, len(lines))
    sel_start = max(1, min(start_line or 1, total_lines))
    sel_end = max(sel_start, min(end_line or sel_start, total_lines))

    language = 'python' if path.suffix.lower() in {'.py', '.pyi'} else 'text'
    symbol_context = _python_symbol_context(path, content, sel_start, sel_end) if language == 'python' else {
        'language': language,
        'symbol': None,
        'imports': [],
    }

    symbol = symbol_context.get('symbol')
    symbol_block = None
    refs: list[dict] = []
    if symbol:
        symbol_block = _line_window(lines, symbol['start_line'], symbol['end_line'], 0)
        refs = _reference_hits(path, symbol['name'])

    workspace_patterns = _workspace_patterns_from_refs(path, refs) if include_workspace else []

    return {
        'path': _to_posix(path),
        'selection': {
            'start_line': sel_start,
            'end_line': sel_end,
            'content': '\n'.join(lines[sel_start - 1:sel_end]),
        },
        'nearby': _line_window(lines, sel_start, sel_end, 30),
        'symbol': symbol,
        'symbol_content': symbol_block,
        'imports': symbol_context.get('imports', []),
        'references': refs,
        'workspace_patterns': workspace_patterns,
        'total_lines': total_lines,
    }


def _list_directory(rel_path: str) -> dict:
    root = _resolve_relative_path(rel_path)
    if not root.exists():
        raise HTTPException(status_code=404, detail='Folder not found')
    if not root.is_dir():
        raise HTTPException(status_code=400, detail='Path is not a folder')

    directories: list[dict] = []
    files: list[dict] = []
    try:
        entries = sorted(root.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f'Unable to list folder: {exc}') from exc

    for entry in entries:
        if entry.name in IGNORED_DIRS:
            continue
        rel_entry = _to_posix(entry)
        if entry.is_dir():
            directories.append({
                'name': entry.name,
                'path': rel_entry,
                'kind': 'directory',
            })
            continue
        if not _is_probably_text(entry):
            continue
        stat = entry.stat()
        files.append({
            'name': entry.name,
            'path': rel_entry,
            'kind': 'file',
            'size': stat.st_size,
            'modified_at': int(stat.st_mtime),
        })

    return {
        'root': str(_workspace_root()),
        'path': _to_posix(root),
        'name': root.name if rel_path else _workspace_root().name,
        'directories': directories,
        'files': files,
    }


app = FastAPI(title='KoreCode')


class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith('/static/') or request.url.path.startswith('/ui-elements/assets/'):
            response.headers['cache-control'] = 'no-store'
        return response


app.add_middleware(NoCacheMiddleware)

app.mount('/static/code', StaticFiles(directory=STATIC_DIR / 'code'), name='code')
app.mount('/ui-elements/assets', StaticFiles(directory=COMMONUI_ASSETS), name='ui-elements-assets')
app.mount('/static/commonui', StaticFiles(directory=COMMONUI_ASSETS), name='commonui')


@app.get('/', include_in_schema=False)
def root():
    return RedirectResponse('/ui')


@app.get('/suite-config.js', include_in_schema=False)
def suite_config_js():
    urls = os.environ.get('KORE_SUITE_URLS', '{}')
    return Response(content=f'window.__koreSuiteUrls = {urls};', media_type='application/javascript', headers={'Cache-Control': 'no-store'})


@app.get('/ui', include_in_schema=False)
def serve_ui():
    return FileResponse(STATIC_DIR / 'code' / 'index.html')


@app.get('/code', include_in_schema=False)
def serve_code_alias():
    """Legacy alias — kept for existing bookmarks."""
    return RedirectResponse('/ui')


@app.get('/status')
def status():
    return {
        'status': 'ok',
        'service': 'korecode',
        'root': str(_workspace_root()),
        'suite_root': str(SUITE_ROOT),
    }


@app.get('/api/root-options')
def api_root_options():
    return _root_options_payload()


@app.post('/api/root')
def api_set_root(body: RootBody):
    new_root = _set_workspace_root(body.root)
    payload = _root_options_payload()
    payload['ok'] = True
    payload['root'] = str(new_root)
    return payload


@app.get('/api/root-browse')
def api_root_browse(path: str | None = Query(default=None)):
    return _list_browse_directories(path)


@app.get('/api/tree')
def api_tree(path: str = Query(default='')):
    return _list_directory(path)


@app.get('/api/file')
def api_read_file(path: str = Query(...)):
    candidate = _resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail='Path is not a file')
    if not _is_probably_text(candidate):
        raise HTTPException(status_code=415, detail='Binary files are not supported')
    content, encoding = _read_text(candidate)
    stat = candidate.stat()
    content_hash = _content_hash(content)
    return {
        'path': _to_posix(candidate),
        'name': candidate.name,
        'content': content,
        'encoding': encoding,
        'size': stat.st_size,
        'modified_at': int(stat.st_mtime),
        'modified_at_ns': int(stat.st_mtime_ns),
        'content_hash': content_hash,
    }


@app.put('/api/file')
def api_write_file(body: WriteBody, path: str = Query(...)):
    candidate = _resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail='Path is not a file')
    if not _is_probably_text(candidate):
        raise HTTPException(status_code=415, detail='Binary files are not supported')
    existing_content, _ = _read_text(candidate)
    current_hash = _content_hash(existing_content)
    stat_before = candidate.stat()
    expected_hash = str(body.expected_hash or '').strip()
    if expected_hash:
        if current_hash != expected_hash:
            raise HTTPException(status_code=409, detail='File changed on disk (content hash mismatch)')
    else:
        if body.expected_modified_at is not None and int(stat_before.st_mtime) != int(body.expected_modified_at):
            raise HTTPException(status_code=409, detail='File changed on disk (modified_at mismatch)')
        if body.expected_modified_at_ns is not None and int(stat_before.st_mtime_ns) != int(body.expected_modified_at_ns):
            raise HTTPException(status_code=409, detail='File changed on disk (modified_at_ns mismatch)')
    candidate.write_text(body.content, encoding='utf-8', newline='')
    stat = candidate.stat()
    return {
        'ok': True,
        'path': _to_posix(candidate),
        'size': stat.st_size,
        'modified_at': int(stat.st_mtime),
        'modified_at_ns': int(stat.st_mtime_ns),
        'content_hash': _content_hash(body.content),
    }


@app.get('/api/context')
def api_context(
    path: str = Query(...),
    start_line: int | None = Query(default=None, ge=1),
    end_line: int | None = Query(default=None, ge=1),
    query: str | None = Query(default=None),
    include_workspace: bool = Query(default=False),
):
    candidate = _resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail='Path is not a file')
    if not _is_probably_text(candidate):
        raise HTTPException(status_code=415, detail='Binary files are not supported')
    return _build_context_pack(candidate, start_line, end_line, query=query, include_workspace=include_workspace)


@app.post('/api/file')
def api_create_file(path: str = Query(...)):
    candidate = _resolve_relative_path(path)
    if candidate.exists():
        raise HTTPException(status_code=409, detail='File already exists')
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text('', encoding='utf-8')
    stat = candidate.stat()
    return {'ok': True, 'path': _to_posix(candidate), 'size': stat.st_size}


@app.delete('/api/file')
def api_delete_file(path: str = Query(...)):
    candidate = _resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail='Path is not a file')
    candidate.unlink()
    return {'ok': True, 'path': _to_posix(candidate)}


@app.post('/api/dir')
def api_create_dir(path: str = Query(...)):
    candidate = _resolve_relative_path(path)
    if candidate.exists():
        raise HTTPException(status_code=409, detail='Directory already exists')
    candidate.mkdir(parents=True, exist_ok=False)
    return {'ok': True, 'path': _to_posix(candidate)}


@app.delete('/api/dir')
def api_delete_dir(path: str = Query(...)):
    candidate = _resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='Directory not found')
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail='Path is not a directory')
    try:
        candidate.rmdir()
    except OSError:
        raise HTTPException(status_code=409, detail='Directory is not empty')
    return {'ok': True, 'path': _to_posix(candidate)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Launch the KoreCode editor.')
    parser.add_argument('--host', default=_cfg['host'])
    parser.add_argument('--port', type=int, default=_cfg['port'])
    parser.add_argument('--reload', action='store_true')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    LOG.info('Starting KoreCode on %s:%s', args.host, args.port)
    uvicorn.run('app.server:app', host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())