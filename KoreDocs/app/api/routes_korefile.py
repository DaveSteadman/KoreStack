from __future__ import annotations

from typing import Annotated

from fastapi import HTTPException
from fastapi import Query
from pydantic import BaseModel

from ..documents.korefile import service as korefile
from .routes_textedit import parse_expected_revision


class KfFolderCreate(BaseModel):
    name: str
    parent_id: int = 1


class KfFolderPatch(BaseModel):
    name: str | None = None
    parent_id: int | None = None
    expected_revision: int | str | None = None


class KfFilePatch(BaseModel):
    name: str | None = None
    folder_id: int | None = None
    expected_revision: int | str | None = None


class KfFileCreate(BaseModel):
    folder_id: int
    name: str
    content: str
    metadata: dict | None = None


class KfFileUpdate(BaseModel):
    content: str | None = None
    metadata: dict | None = None
    expected_revision: int | str | None = None


def korefile_revision_tokenize(file_row: dict | None) -> dict | None:
    if file_row is None:
        return None
    row = dict(file_row)
    if 'revision' in row:
        row['revision'] = None if row.get('revision') is None else str(row.get('revision'))
    return row


def korefile_revision_tokenize_many(rows: list[dict]) -> list[dict]:
    return [korefile_revision_tokenize(row) or row for row in rows]


def register_korefile_routes(app, *, data_dir) -> None:
    @app.get('/api/folders', summary='List all folders (flat, ordered by path)')
    def kf_list_folders():
        return korefile_revision_tokenize_many(korefile.list_folders())

    @app.post('/api/folders', status_code=201, summary='Create a folder')
    def kf_create_folder(body: KfFolderCreate):
        try:
            created = korefile.create_folder(body.name, body.parent_id)
            return korefile_revision_tokenize(created)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            if 'UNIQUE' in str(exc):
                raise HTTPException(status_code=409, detail='A folder with that name already exists here')
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete('/api/folders/{folder_id}', summary='Delete a folder')
    def kf_delete_folder(
        folder_id: int,
        expected_revision: Annotated[int | str | None, Query()] = None,
        recursive: Annotated[bool, Query()] = False,
    ):
        try:
            if not korefile.delete_folder(
                folder_id,
                expected_revision=parse_expected_revision(expected_revision),
                recursive=recursive,
            ):
                raise HTTPException(status_code=404, detail='Folder not found')
        except HTTPException:
            raise
        except korefile.ConflictError:
            raise HTTPException(status_code=409, detail='Folder changed in the background; refresh and try again.')
        except Exception as exc:
            detail = str(exc)
            if 'FOREIGN KEY constraint failed' in detail:
                detail = 'Folder is not empty. Confirm recursive delete or move its files and sub-folders first.'
            raise HTTPException(status_code=409, detail=detail)
        return {'ok': True}

    @app.patch('/api/folders/{folder_id}', summary='Rename or move a folder')
    def kf_patch_folder(folder_id: int, body: KfFolderPatch):
        if body.name is None and body.parent_id is None:
            raise HTTPException(status_code=400, detail='Provide name and/or parent_id')
        try:
            result = None
            expected_revision = parse_expected_revision(body.expected_revision)
            if body.name is not None:
                result = korefile.rename_folder(folder_id, body.name, expected_revision=expected_revision)
                expected_revision = result['revision'] if result else expected_revision
            if body.parent_id is not None:
                result = korefile.move_folder(folder_id, body.parent_id, expected_revision=expected_revision)
            return korefile_revision_tokenize(result)
        except korefile.ConflictError:
            raise HTTPException(status_code=409, detail='Folder changed in the background; refresh and try again.')
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            if 'UNIQUE' in str(exc):
                raise HTTPException(status_code=409, detail='A folder with that name already exists here')
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get('/api/files', summary='List files (metadata only)')
    def kf_list_files(
        folder_id: Annotated[int | None, Query()] = None,
        folder_path: Annotated[str | None, Query()] = None,
        type: Annotated[str | None, Query()] = None,
        name: Annotated[str | None, Query()] = None,
        limit: Annotated[int | None, Query(ge=1, le=500)] = None,
    ):
        rows = korefile.list_files(folder_id=folder_id, folder_path=folder_path, ext=type, name=name, limit=limit)
        return korefile_revision_tokenize_many(rows)

    @app.get('/api/files/{file_id}', summary='Get a file with full content')
    def kf_get_file(file_id: int, include_content: Annotated[bool, Query()] = True):
        file_row = korefile.get_file(file_id, include_content=include_content)
        if file_row is None:
            raise HTTPException(status_code=404, detail='File not found')
        return korefile_revision_tokenize(file_row)

    @app.post('/api/files', status_code=201, summary='Create a file')
    def kf_create_file(body: KfFileCreate):
        try:
            created = korefile.create_file(body.folder_id, body.name, body.content, body.metadata)
            return korefile_revision_tokenize(created)
        except Exception as exc:
            if 'UNIQUE' in str(exc):
                raise HTTPException(status_code=409, detail='A file with that name already exists in this folder')
            raise HTTPException(status_code=400, detail=str(exc))

    @app.put('/api/files/{file_id}', summary='Update a file')
    def kf_update_file(file_id: int, body: KfFileUpdate):
        try:
            updated = korefile.update_file(
                file_id,
                body.content,
                body.metadata,
                parse_expected_revision(body.expected_revision),
            )
            if updated is None:
                raise HTTPException(status_code=404, detail='File not found')
            return korefile_revision_tokenize(updated)
        except korefile.ConflictError:
            raise HTTPException(status_code=409, detail='File changed in the background; refreshing to the latest version.')

    @app.patch('/api/files/{file_id}', summary='Rename or move a file')
    def kf_patch_file(file_id: int, body: KfFilePatch):
        if body.name is None and body.folder_id is None:
            raise HTTPException(status_code=400, detail='Provide name and/or folder_id')
        try:
            result = None
            expected_revision = parse_expected_revision(body.expected_revision)
            if body.name is not None:
                result = korefile.rename_file(file_id, body.name, expected_revision=expected_revision)
                if result is None:
                    raise HTTPException(status_code=404, detail='File not found')
                expected_revision = result['revision']
            if body.folder_id is not None:
                result = korefile.move_file(file_id, body.folder_id, expected_revision=expected_revision)
                if result is None:
                    raise HTTPException(status_code=404, detail='File not found')
            return korefile_revision_tokenize(result)
        except HTTPException:
            raise
        except korefile.ConflictError:
            raise HTTPException(status_code=409, detail='File changed in the background; refresh and try again.')
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            if 'UNIQUE' in str(exc):
                raise HTTPException(status_code=409, detail='A file with that name already exists in this folder')
            raise HTTPException(status_code=400, detail=str(exc))

    @app.delete('/api/files/{file_id}', summary='Delete a file')
    def kf_delete_file(file_id: int, expected_revision: Annotated[int | str | None, Query()] = None):
        try:
            if not korefile.delete_file(file_id, expected_revision=parse_expected_revision(expected_revision)):
                raise HTTPException(status_code=404, detail='File not found')
        except HTTPException:
            raise
        except korefile.ConflictError:
            raise HTTPException(status_code=409, detail='File changed in the background; refresh and try again.')
        return {'ok': True}

    @app.get('/api/search', summary='Full-text search across all KoreFile documents')
    def kf_search(
        q: str,
        type: Annotated[str | None, Query()] = None,
        folder_path: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 20,
    ):
        try:
            return korefile.search(q, ext=type, folder_path=folder_path, limit=limit)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post('/api/import-fs', summary='Import flat-FS files into KoreFile DB')
    def kf_import_fs():
        return korefile.import_from_fs(data_dir)
