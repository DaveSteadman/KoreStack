from __future__ import annotations

import json

from typing import Any, Optional, Literal

from ._mcp_shared import _create_serialized_file
from .koresheet_core import (
    _load_sheet_doc,
    _save_sheet_doc,
    _sheet_addr,
    _sheet_apply_update,
    _sheet_build_table_doc,
    _sheet_cell_scalar,
    _sheet_col_to_index,
    _sheet_compounding_doc,
    _sheet_effective_header_row,
    _sheet_ensure_headers,
    _sheet_find_label_matches,
    _sheet_header_map,
    _sheet_headers_for_range,
    _sheet_last_used_row,
    _sheet_next_append_row,
    _sheet_parse_addr,
    _sheet_recompute_all,
    _sheet_row_key,
    _sheet_row_matches,
    _sheet_summary,
    _sheet_table_headers,
    _sheet_table_rows,
    _sheet_unique_headers,
    _sheet_used_range,
    _sheet_write_headers,
    _sheet_write_row_values,
)


def create_sheet(
    folder_path: str,
    name: str,
    cells: Optional[dict] = None,
    title: Optional[str] = None,
    cols: int = 26,
    rows: int = 100,
) -> dict:
    from .koresheet_core import _sheet_content

    doc_title = title or name.rsplit('.', 1)[0]
    content = _sheet_content(doc_title, cells, cols, rows)
    return _create_serialized_file(folder_path, name, 'koresheet', content, {'title': doc_title})


def get_sheet(id: int, include_cells: bool = False) -> dict:
    file, doc = _load_sheet_doc(id)
    return _sheet_summary(file, doc, include_cells=include_cells)


def append_sheet_rows(
    id: int,
    rows: list[Any],
    start_col: str = 'A',
    header_row: int | None = None,
    expected_revision: int | None = None,
) -> dict:
    if not isinstance(rows, list) or not rows:
        raise ValueError('rows must be a non-empty list')
    file, doc = _load_sheet_doc(id)
    start_col_index = _sheet_col_to_index(start_col)
    if start_col_index > int(doc['cols']):
        raise ValueError(f'start_col {start_col} is outside sheet bounds')
    next_row = _sheet_next_append_row(doc, header_row)
    written: list[str] = []
    if all(isinstance(row, dict) for row in rows):
        if header_row is None:
            raise ValueError('header_row is required when appending object-style rows')
        header_map = _sheet_header_map(doc, header_row)
        if not header_map:
            raise ValueError(f'No headers found on row {header_row}')
        for row_data in rows:
            for key, value in row_data.items():
                if key not in header_map:
                    raise ValueError(f'Unknown header on row {header_row}: {key}')
                addr = _sheet_addr(header_map[key], next_row)
                cell = _sheet_apply_update(doc['cells'].get(addr), value)
                if cell is None:
                    doc['cells'].pop(addr, None)
                else:
                    doc['cells'][addr] = cell
                written.append(addr)
            next_row += 1
    elif all(isinstance(row, list) for row in rows):
        for row_values in rows:
            if start_col_index + len(row_values) - 1 > int(doc['cols']):
                raise ValueError('row data exceeds sheet width')
            written.extend(_sheet_write_row_values(doc, next_row, row_values, start_col_index))
            next_row += 1
    else:
        raise ValueError('rows must contain either all objects or all lists')
    _sheet_recompute_all(doc)
    updated_file = _save_sheet_doc(id, doc, expected_revision=expected_revision)
    first_addr = min(written, key=lambda addr: _sheet_parse_addr(addr))
    first_row = _sheet_parse_addr(first_addr)[1]
    last_addr = max(written, key=lambda addr: _sheet_parse_addr(addr))
    _, last_row = _sheet_parse_addr(last_addr)
    return {
        **_sheet_summary(updated_file, doc, include_cells=False),
        'appended_rows': len(rows),
        'written_range': f'{first_addr}:{last_addr}',
        'start_row': first_row,
        'end_row': last_row,
    }


def upsert_sheet_rows(
    id: int,
    rows: list[dict[str, Any]],
    key_columns: list[str],
    header_row: int = 1,
    create_missing_columns: bool = False,
    expected_revision: int | None = None,
) -> dict:
    if not isinstance(rows, list) or not rows:
        raise ValueError('rows must be a non-empty list of objects')
    if not isinstance(key_columns, list) or not key_columns:
        raise ValueError('key_columns must be a non-empty list')
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError('rows must contain only objects keyed by header name')
    file, doc = _load_sheet_doc(id)
    required_headers = list(dict.fromkeys([*key_columns, *(key for row in rows for key in row.keys())]))
    header_map = _sheet_header_map(doc, header_row)
    missing_headers = [name for name in required_headers if name not in header_map]
    if missing_headers and not create_missing_columns:
        raise ValueError(f'Missing sheet columns: {", ".join(missing_headers)}')
    if missing_headers:
        header_map = _sheet_ensure_headers(doc, header_row, missing_headers)

    last_row = _sheet_last_used_row(doc, header_row)
    existing_rows: dict[tuple[Any, ...], int] = {}
    for row_number in range(header_row + 1, last_row + 1):
        key = tuple(
            _sheet_row_key(_sheet_cell_scalar(doc['cells'].get(_sheet_addr(header_map[column], row_number))))
            for column in key_columns
        )
        if any(value not in (None, '') for value in key):
            existing_rows[key] = row_number

    appended_rows = 0
    updated_rows = 0
    next_row = _sheet_next_append_row(doc, header_row)
    touched_rows: list[int] = []
    for row_data in rows:
        key = tuple(_sheet_row_key(row_data.get(column)) for column in key_columns)
        if any(value in (None, '') for value in key):
            raise ValueError('Every upsert row must provide non-empty values for all key_columns')
        target_row = existing_rows.get(key)
        if target_row is None:
            target_row = next_row
            next_row += 1
            appended_rows += 1
            existing_rows[key] = target_row
        else:
            updated_rows += 1
        touched_rows.append(target_row)
        for column_name, value in row_data.items():
            if column_name not in header_map:
                raise ValueError(f'Unknown header: {column_name}')
            addr = _sheet_addr(header_map[column_name], target_row)
            cell = _sheet_apply_update(doc['cells'].get(addr), value)
            if cell is None:
                doc['cells'].pop(addr, None)
            else:
                doc['cells'][addr] = cell

    _sheet_recompute_all(doc)
    updated_file = _save_sheet_doc(id, doc, expected_revision=expected_revision)
    return {
        **_sheet_summary(updated_file, doc, include_cells=False),
        'updated_rows': updated_rows,
        'appended_rows': appended_rows,
        'touched_rows': sorted(set(touched_rows)),
    }


def get_sheet_headers(id: int, header_row: int | None = None, range_ref: str | None = None) -> dict:
    file, doc = _load_sheet_doc(id)
    resolved_header_row = _sheet_effective_header_row(doc, header_row)
    bounds, headers = _sheet_table_headers(doc, resolved_header_row, range_ref)
    return {
        **_sheet_summary(file, doc, include_cells=False),
        'header_row': resolved_header_row,
        'range': bounds['range'],
        'headers': headers,
    }


def find_sheet_column(
    id: int,
    header_name: str,
    header_row: int | None = None,
    match_mode: Literal['exact', 'contains'] = 'exact',
) -> dict:
    file, doc = _load_sheet_doc(id)
    resolved_header_row = _sheet_effective_header_row(doc, header_row)
    _, headers = _sheet_table_headers(doc, resolved_header_row, None)
    needle = header_name.strip().lower()
    for header in headers:
        candidate = header['name'].strip().lower()
        if (match_mode == 'exact' and candidate == needle) or (match_mode == 'contains' and needle in candidate):
            return {
                **_sheet_summary(file, doc, include_cells=False),
                'header_row': resolved_header_row,
                'header': header,
            }
    raise ValueError(f'No column found matching {header_name!r}')


def preview_sheet(
    id: int,
    sample_rows: int = 5,
    header_row: int | None = None,
    range_ref: str | None = None,
) -> dict:
    if sample_rows < 1 or sample_rows > 100:
        raise ValueError('sample_rows must be between 1 and 100')
    file, doc = _load_sheet_doc(id)
    resolved_header_row = _sheet_effective_header_row(doc, header_row)
    bounds, headers = _sheet_table_headers(doc, resolved_header_row, range_ref)
    rows_out = _sheet_table_rows(doc, resolved_header_row, headers, bounds)[:sample_rows]
    return {
        **_sheet_summary(file, doc, include_cells=False),
        'header_row': resolved_header_row,
        'range': bounds['range'],
        'headers': headers,
        'rows': rows_out,
    }


def describe_sheet(
    id: int,
    sample_rows: int = 5,
    header_row: int | None = None,
    range_ref: str | None = None,
) -> dict:
    if sample_rows < 1 or sample_rows > 100:
        raise ValueError('sample_rows must be between 1 and 100')
    file, doc = _load_sheet_doc(id)
    resolved_header_row = _sheet_effective_header_row(doc, header_row)
    bounds, headers = _sheet_table_headers(doc, resolved_header_row, range_ref)
    rows_out = _sheet_table_rows(doc, resolved_header_row, headers, bounds)[:sample_rows]
    formula_cells = [
        {'addr': addr, 'formula': cell.get('formula'), 'computed': cell.get('computed')}
        for addr, cell in sorted(doc['cells'].items(), key=lambda item: _sheet_parse_addr(item[0]))
        if isinstance(cell, dict) and cell.get('formula')
    ]
    labels = _sheet_find_label_matches(doc, None, 'contains')[:20]
    return {
        **_sheet_summary(file, doc, include_cells=False),
        'header_row': resolved_header_row,
        'range': bounds['range'],
        'headers': headers,
        'sample_rows': rows_out,
        'formula_cells': formula_cells[:20],
        'labels': labels,
    }


def find_sheet_rows(
    id: int,
    filters: dict[str, Any],
    header_row: int | None = None,
    range_ref: str | None = None,
    match_mode: Literal['all', 'any'] = 'all',
) -> dict:
    file, doc = _load_sheet_doc(id)
    resolved_header_row = _sheet_effective_header_row(doc, header_row)
    bounds, headers = _sheet_table_headers(doc, resolved_header_row, range_ref)
    rows_out = _sheet_table_rows(doc, resolved_header_row, headers, bounds)
    matched_rows = [row for row in rows_out if _sheet_row_matches(row, filters, match_mode)]
    return {
        **_sheet_summary(file, doc, include_cells=False),
        'header_row': resolved_header_row,
        'range': bounds['range'],
        'match_mode': match_mode,
        'matched_count': len(matched_rows),
        'rows': matched_rows,
    }


def update_sheet_rows(
    id: int,
    match: dict[str, Any],
    updates: dict[str, Any],
    header_row: int | None = None,
    range_ref: str | None = None,
    match_mode: Literal['all', 'any'] = 'all',
    create_missing_columns: bool = False,
    expected_revision: int | None = None,
) -> dict:
    if not updates:
        raise ValueError('updates must not be empty')
    file, doc = _load_sheet_doc(id)
    resolved_header_row = _sheet_effective_header_row(doc, header_row)
    bounds, headers = _sheet_table_headers(doc, resolved_header_row, range_ref)
    header_map = _sheet_header_map(doc, resolved_header_row)
    missing_headers = [name for name in updates if name not in header_map]
    if missing_headers and not create_missing_columns:
        raise ValueError(f'Missing sheet columns: {", ".join(missing_headers)}')
    if missing_headers:
        header_map = _sheet_ensure_headers(doc, resolved_header_row, missing_headers)
        bounds, headers = _sheet_table_headers(doc, resolved_header_row, range_ref)
    rows_out = _sheet_table_rows(doc, resolved_header_row, headers, bounds)
    matched_rows = [row for row in rows_out if _sheet_row_matches(row, match, match_mode)]
    if not matched_rows:
        return {
            **_sheet_summary(file, doc, include_cells=False),
            'header_row': resolved_header_row,
            'updated_rows': 0,
            'touched_rows': [],
        }
    written_cells: list[str] = []
    touched_rows: list[int] = []
    for row in matched_rows:
        row_number = int(row['_row'])
        touched_rows.append(row_number)
        for header_name, value in updates.items():
            addr = _sheet_addr(header_map[header_name], row_number)
            cell = _sheet_apply_update(doc['cells'].get(addr), value)
            if cell is None:
                doc['cells'].pop(addr, None)
            else:
                doc['cells'][addr] = cell
            written_cells.append(addr)
    _sheet_recompute_all(doc)
    updated_file = _save_sheet_doc(id, doc, expected_revision=expected_revision)
    return {
        **_sheet_summary(updated_file, doc, include_cells=False),
        'header_row': resolved_header_row,
        'updated_rows': len(matched_rows),
        'touched_rows': sorted(set(touched_rows)),
        'written_cells': sorted(set(written_cells), key=lambda addr: _sheet_parse_addr(addr)),
    }


def set_sheet_headers(
    id: int,
    headers: list[str],
    header_row: int = 1,
    start_col: str = 'A',
    expected_revision: int | None = None,
) -> dict:
    if not headers:
        raise ValueError('headers must not be empty')
    file, doc = _load_sheet_doc(id)
    start_col_index = _sheet_col_to_index(start_col)
    written = _sheet_write_headers(doc, headers, header_row, start_col_index)
    _sheet_recompute_all(doc)
    updated_file = _save_sheet_doc(id, doc, expected_revision=expected_revision)
    _, header_info = _sheet_table_headers(doc, header_row, None)
    return {
        **_sheet_summary(updated_file, doc, include_cells=False),
        'header_row': header_row,
        'headers': header_info,
        'written_cells': written,
    }


def append_sheet_table_rows(
    id: int,
    rows: list[Any],
    header_row: int | None = None,
    create_missing_columns: bool = False,
    expected_revision: int | None = None,
) -> dict:
    if not isinstance(rows, list) or not rows:
        raise ValueError('rows must be a non-empty list')
    file, doc = _load_sheet_doc(id)
    resolved_header_row = _sheet_effective_header_row(doc, header_row)
    header_map = _sheet_header_map(doc, resolved_header_row)
    if all(isinstance(row, dict) for row in rows):
        if not header_map:
            headers = list(dict.fromkeys(key for row in rows for key in row.keys()))
            _sheet_write_headers(doc, headers, resolved_header_row, 1)
            header_map = _sheet_header_map(doc, resolved_header_row)
        missing_headers = [key for key in dict.fromkeys(key for row in rows for key in row.keys()) if key not in header_map]
        if missing_headers and not create_missing_columns:
            raise ValueError(f'Missing sheet columns: {", ".join(missing_headers)}')
        if missing_headers:
            header_map = _sheet_ensure_headers(doc, resolved_header_row, missing_headers)
        next_row = _sheet_next_append_row(doc, resolved_header_row)
        written: list[str] = []
        for row_obj in rows:
            for key, value in row_obj.items():
                addr = _sheet_addr(header_map[key], next_row)
                cell = _sheet_apply_update(doc['cells'].get(addr), value)
                if cell is None:
                    doc['cells'].pop(addr, None)
                else:
                    doc['cells'][addr] = cell
                written.append(addr)
            next_row += 1
        _sheet_recompute_all(doc)
        updated_file = _save_sheet_doc(id, doc, expected_revision=expected_revision)
        return {
            **_sheet_summary(updated_file, doc, include_cells=False),
            'header_row': resolved_header_row,
            'appended_rows': len(rows),
            'written_cells': sorted(set(written), key=lambda addr: _sheet_parse_addr(addr)),
        }
    return append_sheet_rows(id=id, rows=rows, start_col='A', header_row=resolved_header_row, expected_revision=expected_revision)


def create_sheet_table(
    folder_path: str,
    name: str,
    headers: list[str],
    rows: Optional[list[Any]] = None,
    header_row: int = 1,
    start_col: str = 'A',
    title: Optional[str] = None,
) -> dict:
    if not headers:
        raise ValueError('headers must not be empty')
    doc_title = title or name.rsplit('.', 1)[0]
    doc = _sheet_build_table_doc(doc_title, headers, rows or [], header_row=header_row, start_col=start_col)
    content = json.dumps(doc, indent=2)
    return _create_serialized_file(folder_path, name, 'koresheet', content, {'title': doc_title})


def create_compounding_schedule(
    folder_path: str,
    name: str,
    principal: float,
    annual_rate: float,
    years: int,
    title: Optional[str] = None,
) -> dict:
    if years < 1 or years > 200:
        raise ValueError('years must be between 1 and 200')
    doc_title = title or name.rsplit('.', 1)[0]
    doc = _sheet_compounding_doc(doc_title, principal, annual_rate, years)
    content = json.dumps(doc, indent=2)
    created = _create_serialized_file(folder_path, name, 'koresheet', content, {'title': doc_title})
    final_row = years + 4
    return {
        **created,
        'created_kind': 'compound_interest_schedule',
        'requested_path': folder_path,
        'input_labels': {
            'Starting Balance': 'B1',
            'Annual Rate': 'B2',
        },
        'table': {
            'header_row': 4,
            'range': f'A4:D{final_row}',
            'columns': ['Year', 'Opening Balance', 'Interest', 'Ending Balance'],
            'data_rows': years,
        },
        'final_year': years,
        'final_balance': _sheet_cell_scalar(doc['cells'].get(f'D{final_row}')),
        'open_url_hint': f'/sheet?src=kf&id={created.get("id")}',
    }