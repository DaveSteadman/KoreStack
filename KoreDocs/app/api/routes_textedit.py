from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi import Query
from pydantic import BaseModel

from KoreCommon.datauser_fs import DataUserPathError
from KoreCommon.datauser_fs import datauser_relative_path
from KoreCommon.datauser_fs import read_binary_file
from KoreCommon.datauser_fs import resolve_datauser_path
from KoreCommon.datauser_fs import write_text_file as write_datauser_text_file

from ..documents.korefile import service as korefile


class TextEditSaveBody(BaseModel):
    file_id: int | None = None
    path: str | None = None
    content: str
    expected_revision: int | str | None = None


def parse_expected_revision(value: int | str | None) -> int | None:
    if value is None or value == '':
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='Invalid expected_revision') from exc


def register_textedit_routes(app, *, textedit_max_bytes: int) -> None:
    def resolve_textedit_path(path_value: str) -> Path:
        raw = (path_value or '').strip()
        if not raw:
            raise HTTPException(status_code=400, detail='Path is required')
        try:
            candidate = resolve_datauser_path(raw)
        except DataUserPathError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=404, detail='File not found')
        return candidate

    def textedit_revision_token(value: int | None) -> str | None:
        return None if value is None else str(value)

    @app.get('/api/textedit/open', summary='Open any file as plain UTF-8 text')
    def textedit_open(
        file_id: int | None = Query(default=None),
        path: str | None = Query(default=None),
    ):
        if (file_id is None) == (path is None):
            raise HTTPException(status_code=400, detail='Provide exactly one of file_id or path')

        if file_id is not None:
            file_row = korefile.get_file(file_id, include_content=True)
            if file_row is None:
                raise HTTPException(status_code=404, detail='File not found')
            content = file_row.get('content') or ''
            return {
                'source': 'korefile',
                'file_id': file_row.get('id'),
                'name': file_row.get('name'),
                'revision': textedit_revision_token(file_row.get('revision')),
                'content': content,
                'encoding': 'utf-8',
                'byte_length': len(content.encode('utf-8')),
                'truncated': False,
            }

        disk_path = resolve_textedit_path(path or '')
        raw, truncated, total_len = read_binary_file(disk_path, max_bytes=textedit_max_bytes)
        return {
            'source': 'filesystem',
            'path': datauser_relative_path(disk_path),
            'full_path': str(disk_path),
            'content': raw.decode('utf-8', errors='replace'),
            'encoding': 'utf-8 (replacement for invalid bytes)',
            'byte_length': total_len,
            'truncated': truncated,
        }

    @app.put('/api/textedit/save', summary='Save plain text back to KoreFile or filesystem')
    def textedit_save(body: TextEditSaveBody):
        if (body.file_id is None) == (body.path is None):
            raise HTTPException(status_code=400, detail='Provide exactly one of file_id or path')

        if body.file_id is not None:
            try:
                updated = korefile.update_file(
                    body.file_id,
                    body.content,
                    metadata=None,
                    expected_revision=parse_expected_revision(body.expected_revision),
                )
                if updated is None:
                    raise HTTPException(status_code=404, detail='File not found')
                return {
                    'ok': True,
                    'source': 'korefile',
                    'file_id': updated.get('id'),
                    'revision': textedit_revision_token(updated.get('revision')),
                }
            except korefile.ConflictError:
                raise HTTPException(status_code=409, detail='File changed in the background; refresh and retry.')

        disk_path = resolve_textedit_path(body.path or '')
        write_datauser_text_file(disk_path, body.content)
        return {
            'ok': True,
            'source': 'filesystem',
            'path': datauser_relative_path(disk_path),
        }
