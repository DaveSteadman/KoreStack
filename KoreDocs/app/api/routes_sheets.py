from __future__ import annotations

from typing import Annotated, Any

from fastapi import HTTPException
from fastapi import Query
from pydantic import BaseModel

from ..documents.korefile import service as korefile
from ..documents.koresheet import append_sheet_rows
from ..documents.koresheet import clear_sheet_range
from ..documents.koresheet import get_sheet
from ..documents.koresheet import read_sheet_range
from ..documents.koresheet import read_sheet_table
from ..documents.koresheet import upsert_sheet_rows
from ..documents.koresheet import write_sheet_cells


class KfSheetCellsWrite(BaseModel):
    cells: dict[str, Any]
    expected_revision: int | None = None


class KfSheetRowsAppend(BaseModel):
    rows: list[Any]
    start_col: str = 'A'
    header_row: int | None = None
    expected_revision: int | None = None


class KfSheetRowsUpsert(BaseModel):
    rows: list[dict[str, Any]]
    key_columns: list[str]
    header_row: int = 1
    create_missing_columns: bool = False
    expected_revision: int | None = None


class KfSheetClearRange(BaseModel):
    range: str
    expected_revision: int | None = None


def register_sheet_routes(app) -> None:
    @app.get('/api/sheets/{file_id}', summary='Get KoreSheet metadata and optional sparse cells')
    def kf_get_sheet(file_id: int, include_cells: Annotated[bool, Query()] = False):
        try:
            return get_sheet(file_id, include_cells=include_cells)
        except ValueError as exc:
            detail = str(exc)
            raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)

    @app.get('/api/sheets/{file_id}/range', summary='Read an A1-style range from a KoreSheet')
    def kf_read_sheet_range(
        file_id: int,
        range: Annotated[str, Query()],
        values_only: Annotated[bool, Query()] = False,
    ):
        try:
            return read_sheet_range(file_id, range=range, values_only=values_only)
        except ValueError as exc:
            detail = str(exc)
            raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)

    @app.get('/api/sheets/{file_id}/table', summary='Read a KoreSheet region as header-keyed rows')
    def kf_read_sheet_table(
        file_id: int,
        header_row: Annotated[int, Query(ge=1)] = 1,
        range: Annotated[str | None, Query()] = None,
    ):
        try:
            return read_sheet_table(file_id, header_row=header_row, range_ref=range)
        except ValueError as exc:
            detail = str(exc)
            raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)

    @app.post('/api/sheets/{file_id}/cells', summary='Apply sparse A1-addressed cell updates to a KoreSheet')
    def kf_write_sheet_cells(file_id: int, body: KfSheetCellsWrite):
        try:
            return write_sheet_cells(file_id, body.cells, expected_revision=body.expected_revision)
        except korefile.ConflictError:
            raise HTTPException(status_code=409, detail='Sheet changed in the background; refresh and try again.')
        except ValueError as exc:
            detail = str(exc)
            raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)

    @app.post('/api/sheets/{file_id}/rows/append', summary='Append rows to a KoreSheet')
    def kf_append_sheet_rows(file_id: int, body: KfSheetRowsAppend):
        try:
            return append_sheet_rows(file_id, body.rows, start_col=body.start_col, header_row=body.header_row, expected_revision=body.expected_revision)
        except korefile.ConflictError:
            raise HTTPException(status_code=409, detail='Sheet changed in the background; refresh and try again.')
        except ValueError as exc:
            detail = str(exc)
            raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)

    @app.post('/api/sheets/{file_id}/rows/upsert', summary='Update or append rows in a KoreSheet by key columns')
    def kf_upsert_sheet_rows(file_id: int, body: KfSheetRowsUpsert):
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
        except ValueError as exc:
            detail = str(exc)
            raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)

    @app.post('/api/sheets/{file_id}/range/clear', summary='Clear a range in a KoreSheet')
    def kf_clear_sheet_range(file_id: int, body: KfSheetClearRange):
        try:
            return clear_sheet_range(file_id, body.range, expected_revision=body.expected_revision)
        except korefile.ConflictError:
            raise HTTPException(status_code=409, detail='Sheet changed in the background; refresh and try again.')
        except ValueError as exc:
            detail = str(exc)
            raise HTTPException(status_code=404 if 'not found' in detail.lower() else 400, detail=detail)
