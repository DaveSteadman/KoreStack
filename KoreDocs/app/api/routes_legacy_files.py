from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi import Query
from fastapi.responses import JSONResponse
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from starlette.requests import Request

from KoreCommon.datauser_fs import DataUserConflictError
from KoreCommon.datauser_fs import DataUserPathError
from KoreCommon.datauser_fs import file_etag as datauser_file_etag
from KoreCommon.datauser_fs import list_datauser_files
from KoreCommon.datauser_fs import read_text_file as read_datauser_text_file
from KoreCommon.datauser_fs import resolve_datauser_path
from KoreCommon.datauser_fs import write_text_file as write_datauser_text_file
from KoreCommon.datauser_fs import delete_file as delete_datauser_file

from ..documents.korefile import service as korefile


class WriteBody(BaseModel):
    content: str


class CreateBody(BaseModel):
    name: str
    content: str


def register_legacy_file_routes(app, *, data_dir: Path, allowed_extensions: frozenset[str]) -> None:
    def resolve_legacy_name(name: str) -> Path:
        if not name:
            raise HTTPException(status_code=400, detail='Empty filename')
        if any(c in name for c in ('/', '\\', ':')):
            raise HTTPException(status_code=400, detail='Filename must not contain path separators')
        if '..' in name.split('.'):
            raise HTTPException(status_code=400, detail='Invalid filename')
        try:
            path = resolve_datauser_path(name)
        except DataUserPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if path.parent != data_dir:
            raise HTTPException(status_code=400, detail='Filename must resolve in the datauser root')
        if path.suffix not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type '{path.suffix}'. Allowed: {', '.join(sorted(allowed_extensions))}",
            )
        return path

    def validate_flat_content(name: str, content: str) -> None:
        korefile.validate_serialized_content(name, content)

    def enforce_file_match(request: Request, path: Path) -> None:
        if not path.exists():
            return
        expected = request.headers.get('if-match')
        if expected and expected != datauser_file_etag(path):
            raise HTTPException(status_code=409, detail='File changed on disk; reload before writing')

    @app.get('/api/legacy/files')
    def list_files(type: str | None = Query(default=None)):
        ext_filter = f'.{type}' if type else None
        result = []
        for path in list_datauser_files(search_root='', recursive=False, allowed_extensions=set(allowed_extensions)):
            if ext_filter and path.suffix != ext_filter:
                continue
            stat = path.stat()
            result.append({
                'name':     path.name,
                'type':     path.suffix.lstrip('.'),
                'size':     stat.st_size,
                'modified': stat.st_mtime,
            })
        return result

    @app.get('/api/legacy/files/{name}')
    def read_file(name: str):
        path = resolve_legacy_name(name)
        if not path.exists():
            raise HTTPException(status_code=404, detail='File not found')
        response = PlainTextResponse(read_datauser_text_file(path))
        response.headers['etag'] = datauser_file_etag(path)
        return response

    @app.put('/api/legacy/files/{name}')
    def write_file(name: str, body: WriteBody, request: Request):
        path = resolve_legacy_name(name)
        enforce_file_match(request, path)
        validate_flat_content(name, body.content)
        write_datauser_text_file(path, body.content)
        response = JSONResponse({'ok': True, 'name': name})
        response.headers['etag'] = datauser_file_etag(path)
        return response

    @app.delete('/api/legacy/files/{name}')
    def delete_file(name: str, request: Request):
        path = resolve_legacy_name(name)
        if not path.exists():
            raise HTTPException(status_code=404, detail='File not found')
        enforce_file_match(request, path)
        delete_datauser_file(path)
        return {'ok': True}

    @app.post('/api/legacy/files')
    def create_file(body: CreateBody):
        path = resolve_legacy_name(body.name)
        validate_flat_content(body.name, body.content)
        try:
            write_datauser_text_file(path, body.content, overwrite=False)
        except DataUserConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response = JSONResponse({'ok': True, 'name': body.name})
        response.headers['etag'] = datauser_file_etag(path)
        return response
