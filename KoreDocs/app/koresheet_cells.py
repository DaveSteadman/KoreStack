from __future__ import annotations

from typing import Any, Literal

from .koresheet_core import (
    _load_sheet_doc,
    _save_sheet_doc,
    _sheet_apply_update,
    _sheet_cell_scalar,
    _sheet_find_label_matches,
    _sheet_label_target,
    _sheet_parse_addr,
    _sheet_recompute_all,
    _sheet_summary,
)


def write_sheet_cells(id: int, cells: dict[str, Any], expected_revision: int | None = None) -> dict:
    if not isinstance(cells, dict) or not cells:
        raise ValueError('cells must be a non-empty object keyed by A1 address')
    file, doc = _load_sheet_doc(id)
    cols = int(doc['cols'])
    rows = int(doc['rows'])
    written: list[str] = []
    for addr, update in cells.items():
        col, row = _sheet_parse_addr(addr)
        if col > cols or row > rows:
            raise ValueError(f'Cell {addr} is outside sheet bounds')
        cell = _sheet_apply_update(doc['cells'].get(addr), update)
        if cell is None:
            doc['cells'].pop(addr, None)
        else:
            doc['cells'][addr] = cell
        written.append(addr)
    _sheet_recompute_all(doc)
    updated_file = _save_sheet_doc(id, doc, expected_revision=expected_revision)
    return {
        **_sheet_summary(updated_file, doc, include_cells=False),
        'written_cells': sorted(written, key=lambda addr: _sheet_parse_addr(addr)),
    }


def find_labelled_cells(id: int, labels: list[str] | None = None, match_mode: Literal['contains', 'exact'] = 'contains') -> dict:
    file, doc = _load_sheet_doc(id)
    matches = _sheet_find_label_matches(doc, labels, match_mode)
    return {
        **_sheet_summary(file, doc, include_cells=False),
        'match_mode': match_mode,
        'matches': matches,
    }


def get_named_value(id: int, label: str, direction: Literal['right', 'below'] = 'right') -> dict:
    file, doc = _load_sheet_doc(id)
    match, target_addr = _sheet_label_target(doc, label, direction=direction)
    return {
        **_sheet_summary(file, doc, include_cells=False),
        'label_match': match,
        'target_addr': target_addr,
        'value': _sheet_cell_scalar(doc['cells'].get(target_addr)),
    }


def set_named_value(
    id: int,
    label: str,
    value: Any,
    direction: Literal['right', 'below'] = 'right',
    expected_revision: int | None = None,
) -> dict:
    file, doc = _load_sheet_doc(id)
    match, target_addr = _sheet_label_target(doc, label, direction=direction)
    cell = _sheet_apply_update(doc['cells'].get(target_addr), value)
    if cell is None:
        doc['cells'].pop(target_addr, None)
    else:
        doc['cells'][target_addr] = cell
    _sheet_recompute_all(doc)
    updated_file = _save_sheet_doc(id, doc, expected_revision=expected_revision)
    return {
        **_sheet_summary(updated_file, doc, include_cells=False),
        'label_match': match,
        'target_addr': target_addr,
        'value': _sheet_cell_scalar(doc['cells'].get(target_addr)),
    }