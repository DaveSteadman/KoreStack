from __future__ import annotations

from typing import Any, Optional

from .koresheet_core import (
    _load_sheet_doc,
    _save_sheet_doc,
    _sheet_addr,
    _sheet_cell_in_bounds,
    _sheet_cell_scalar,
    _sheet_headers_for_range,
    _sheet_parse_addr,
    _sheet_range_bounds,
    _sheet_recompute_all,
    _sheet_summary,
    _sheet_used_range,
)


def read_sheet_range(id: int, range: str, values_only: bool = False) -> dict:
    file, doc = _load_sheet_doc(id)
    bounds = _sheet_range_bounds(range, int(doc['cols']), int(doc['rows']))
    cells: dict[str, Any] = {}
    for addr, cell in doc['cells'].items():
        if _sheet_cell_in_bounds(addr, bounds):
            cells[addr] = _sheet_cell_scalar(cell) if values_only else cell
    return {
        **_sheet_summary(file, doc, include_cells=False),
        'range': bounds['range'],
        'start_addr': bounds['start_addr'],
        'end_addr': bounds['end_addr'],
        'cells': cells,
    }


def read_sheet_table(id: int, header_row: int = 1, range_ref: Optional[str] = None) -> dict:
    file, doc = _load_sheet_doc(id)
    if header_row < 1 or header_row > int(doc['rows']):
        raise ValueError(f'header_row {header_row} is outside sheet bounds')
    bounds = _sheet_range_bounds(
        range_ref or (_sheet_used_range(doc['cells']) or f'A{header_row}:{_sheet_addr(int(doc["cols"]), header_row)}'),
        int(doc['cols']),
        int(doc['rows']),
    )
    if not (bounds['start_row'] <= header_row <= bounds['end_row']):
        raise ValueError('header_row must be inside the requested range')
    headers = _sheet_headers_for_range(doc, header_row, bounds['start_col'], bounds['end_col'])
    rows_out: list[dict[str, Any]] = []
    for row_number in range(header_row + 1, bounds['end_row'] + 1):
        row_obj = {'_row': row_number}
        has_values = False
        for header in headers:
            addr = _sheet_addr(header['column_index'], row_number)
            value = _sheet_cell_scalar(doc['cells'].get(addr))
            row_obj[header['name']] = value
            if value not in (None, ''):
                has_values = True
        if has_values:
            rows_out.append(row_obj)
    return {
        **_sheet_summary(file, doc, include_cells=False),
        'range': bounds['range'],
        'header_row': header_row,
        'headers': headers,
        'rows': rows_out,
    }


def clear_sheet_range(id: int, range: str, expected_revision: int | None = None) -> dict:
    file, doc = _load_sheet_doc(id)
    bounds = _sheet_range_bounds(range, int(doc['cols']), int(doc['rows']))
    cleared = [addr for addr in list(doc['cells']) if _sheet_cell_in_bounds(addr, bounds)]
    for addr in cleared:
        doc['cells'].pop(addr, None)
    _sheet_recompute_all(doc)
    updated_file = _save_sheet_doc(id, doc, expected_revision=expected_revision)
    return {
        **_sheet_summary(updated_file, doc, include_cells=False),
        'cleared_cells': len(cleared),
        'range': bounds['range'],
    }