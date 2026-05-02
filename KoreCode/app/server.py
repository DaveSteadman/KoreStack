from __future__ import annotations

import argparse
import json
import logging
import os
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


class WriteBody(BaseModel):
    content: str


def _resolve_relative_path(value: str) -> Path:
    candidate = (SUITE_ROOT / value).resolve()
    try:
        candidate.relative_to(SUITE_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Path escapes workspace root') from exc
    return candidate


def _to_posix(path: Path) -> str:
    try:
        return path.relative_to(SUITE_ROOT).as_posix()
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
        'root': str(SUITE_ROOT),
        'path': _to_posix(root),
        'name': root.name if rel_path else SUITE_ROOT.name,
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
        'root': str(SUITE_ROOT),
    }


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
    return {
        'path': _to_posix(candidate),
        'name': candidate.name,
        'content': content,
        'encoding': encoding,
        'size': stat.st_size,
        'modified_at': int(stat.st_mtime),
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
    candidate.write_text(body.content, encoding='utf-8', newline='')
    stat = candidate.stat()
    return {
        'ok': True,
        'path': _to_posix(candidate),
        'size': stat.st_size,
        'modified_at': int(stat.st_mtime),
    }


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