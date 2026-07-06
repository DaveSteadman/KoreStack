# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# MCP tools for .koresheet spreadsheet documents.
#
# Keeps only the MCP-exposed wrappers and delegates all non-MCP behavior to the split
# operation modules.
# ====================================================================================================

from __future__ import annotations

from typing import Any, Optional, Annotated, Literal

from ._mcp_shared import mcp
from .koresheet_cells import (
    find_labelled_cells as _find_labelled_cells,
    get_named_value as _get_named_value,
    set_named_value as _set_named_value,
    write_sheet_cells as _write_sheet_cells,
)
from .koresheet_document import (
    create_sheet as _create_sheet,
    create_sheet_table as _create_sheet_table,
    append_sheet_rows as _append_sheet_rows,
    append_sheet_table_rows as _append_sheet_table_rows,
    describe_sheet as _describe_sheet,
    find_sheet_column as _find_sheet_column,
    find_sheet_rows as _find_sheet_rows,
    get_sheet as _get_sheet,
    get_sheet_headers as _get_sheet_headers,
    preview_sheet as _preview_sheet,
    set_sheet_headers as _set_sheet_headers,
    update_sheet_rows as _update_sheet_rows,
    upsert_sheet_rows as _upsert_sheet_rows,
)
from .koresheet_ranges import (
    clear_sheet_range as _clear_sheet_range,
    read_sheet_range as _read_sheet_range,
    read_sheet_table as _read_sheet_table,
)

@mcp.tool()
def koredocs_sheet_create(
    folder_path: Annotated[str, 'Folder path in the shared KoreDocs/datauser tree, such as "/" or "/Projects". Missing folders are created.'],
    name: Annotated[str, 'Filename, with or without the .koresheet extension.'],
    cells: Annotated[Optional[dict], 'Sparse cell map keyed by A1 address.'] = None,
    title: Annotated[Optional[str], 'Sheet title.'] = None,
    cols: Annotated[int, 'Number of columns. Defaults to 26.'] = 26,
    rows: Annotated[int, 'Number of rows. Defaults to 100.'] = 100,
) -> dict:
    """Canonical prefixed alias for create_sheet."""
    return _create_sheet(folder_path=folder_path, name=name, cells=cells, title=title, cols=cols, rows=rows)


@mcp.tool()
def koredocs_sheet_table_create(
    folder_path: Annotated[str, 'Folder path in the shared KoreDocs/datauser tree, such as "/Projects/Calcs". Missing folders are created.'],
    name: Annotated[str, 'Filename, with or without the .koresheet extension.'],
    headers: Annotated[list[str], 'Ordered list of column headers.'],
    rows: Annotated[Optional[list[Any]], 'Optional initial rows. Each row may be a list of values or an object keyed by header names.'] = None,
    header_row: Annotated[int, 'Row number where the headers should be written.'] = 1,
    start_col: Annotated[str, 'Starting column for the first header.'] = 'A',
    title: Annotated[Optional[str], 'Optional sheet title. Defaults to the filename stem.'] = None,
) -> dict:
    """Create a spreadsheet from headers plus initial rows, using table semantics instead of raw cells."""
    return _create_sheet_table(
        folder_path=folder_path,
        name=name,
        headers=headers,
        rows=rows,
        header_row=header_row,
        start_col=start_col,
        title=title,
    )


@mcp.tool()
def koredocs_sheet_get(
    id: Annotated[int, 'KoreSheet file id.'],
    include_cells: Annotated[bool, 'When true, include the sparse cell map in the response.'] = False,
) -> dict:
    """Canonical prefixed alias for get_sheet."""
    return _get_sheet(id=id, include_cells=include_cells)


@mcp.tool()
def koredocs_sheet_headers_get(
    id: Annotated[int, 'KoreSheet file id.'],
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    range_ref: Annotated[Optional[str], 'Optional A1-style range constraining the header scan.'] = None,
) -> dict:
    """Return the detected column headers for a sheet, with an optional guessed header row."""
    return _get_sheet_headers(id=id, header_row=header_row, range_ref=range_ref)


@mcp.tool()
def koredocs_sheet_column_find(
    id: Annotated[int, 'KoreSheet file id.'],
    header_name: Annotated[str, 'Column header to locate.'],
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    match_mode: Annotated[Literal['exact', 'contains'], 'Header matching mode.'] = 'exact',
) -> dict:
    """Locate a sheet column by header name."""
    return _find_sheet_column(id=id, header_name=header_name, header_row=header_row, match_mode=match_mode)


@mcp.tool()
def koredocs_sheet_preview(
    id: Annotated[int, 'KoreSheet file id.'],
    sample_rows: Annotated[int, 'Maximum number of table rows to preview.'] = 5,
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    range_ref: Annotated[Optional[str], 'Optional A1-style range constraining the preview.'] = None,
) -> dict:
    """Return a compact preview of a sheet as header-keyed rows."""
    return _preview_sheet(id=id, sample_rows=sample_rows, header_row=header_row, range_ref=range_ref)


@mcp.tool()
def koredocs_sheet_describe(
    id: Annotated[int, 'KoreSheet file id.'],
    sample_rows: Annotated[int, 'Maximum number of table rows to preview.'] = 5,
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    range_ref: Annotated[Optional[str], 'Optional A1-style range constraining the structural description.'] = None,
) -> dict:
    """Return a structural summary of a sheet, including guessed headers, sample rows, labels, and formulas."""
    return _describe_sheet(id=id, sample_rows=sample_rows, header_row=header_row, range_ref=range_ref)


@mcp.tool()
def koredocs_sheet_rows_find(
    id: Annotated[int, 'KoreSheet file id.'],
    filters: Annotated[dict[str, Any], 'Header-keyed filters. Values may be scalars or objects like {contains: "foo"} or {gte: 10}.'],
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    range_ref: Annotated[Optional[str], 'Optional A1-style range constraining the search.'] = None,
    match_mode: Annotated[Literal['all', 'any'], 'Whether all filters must match or any filter may match.'] = 'all',
) -> dict:
    """Find table rows by header-keyed filters."""
    return _find_sheet_rows(id=id, filters=filters, header_row=header_row, range_ref=range_ref, match_mode=match_mode)


@mcp.tool()
def koredocs_sheet_rows_update(
    id: Annotated[int, 'KoreSheet file id.'],
    match: Annotated[dict[str, Any], 'Header-keyed filters selecting target rows.'],
    updates: Annotated[dict[str, Any], 'Header-keyed values to write into each matched row.'],
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    range_ref: Annotated[Optional[str], 'Optional A1-style range constraining the search.'] = None,
    match_mode: Annotated[Literal['all', 'any'], 'Whether all filters must match or any filter may match.'] = 'all',
    create_missing_columns: Annotated[bool, 'When true, create columns for any missing update headers.'] = False,
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Update all matched table rows using header-keyed values."""
    return _update_sheet_rows(
        id=id,
        match=match,
        updates=updates,
        header_row=header_row,
        range_ref=range_ref,
        match_mode=match_mode,
        create_missing_columns=create_missing_columns,
        expected_revision=expected_revision,
    )


@mcp.tool()
def koredocs_sheet_headers_set(
    id: Annotated[int, 'KoreSheet file id.'],
    headers: Annotated[list[str], 'Header names to write into the sheet.'],
    header_row: Annotated[int, 'Row number where the headers should be written.'] = 1,
    start_col: Annotated[str, 'Starting column for the first header.'] = 'A',
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Write or replace a contiguous header row."""
    return _set_sheet_headers(id=id, headers=headers, header_row=header_row, start_col=start_col, expected_revision=expected_revision)


@mcp.tool()
def koredocs_sheet_table_rows_append(
    id: Annotated[int, 'KoreSheet file id.'],
    rows: Annotated[list[Any], 'Rows to append. Each row may be a list of values or an object keyed by header names.'],
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    create_missing_columns: Annotated[bool, 'When true, create any missing columns referenced by object-style rows.'] = False,
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Append rows using a table-first interface rather than raw cell addresses."""
    return _append_sheet_table_rows(
        id=id,
        rows=rows,
        header_row=header_row,
        create_missing_columns=create_missing_columns,
        expected_revision=expected_revision,
    )



@mcp.tool()
def koredocs_sheet_labels_find(
    id: Annotated[int, 'KoreSheet file id.'],
    labels: Annotated[Optional[list[str]], 'Optional list of labels to search for. Omit to list likely label cells across the sheet.'] = None,
    match_mode: Annotated[Literal['contains', 'exact'], 'How labels should be matched.'] = 'contains',
) -> dict:
    """Find labelled cells and return adjacent right/below values for navigation."""
    return _find_labelled_cells(id=id, labels=labels, match_mode=match_mode)


@mcp.tool()
def koredocs_sheet_named_value_get(
    id: Annotated[int, 'KoreSheet file id.'],
    label: Annotated[str, 'Label to search for, such as "Interest Rate" or "Starting Balance".'],
    direction: Annotated[Literal['right', 'below'], 'Which neighboring cell should be treated as the value cell.'] = 'right',
) -> dict:
    """Read a value cell next to a labelled cell."""
    return _get_named_value(id=id, label=label, direction=direction)


@mcp.tool()
def koredocs_sheet_named_value_set(
    id: Annotated[int, 'KoreSheet file id.'],
    label: Annotated[str, 'Label to search for, such as "Interest Rate" or "Starting Balance".'],
    value: Annotated[Any, 'New value to write into the neighboring value cell.'],
    direction: Annotated[Literal['right', 'below'], 'Which neighboring cell should be treated as the value cell.'] = 'right',
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Write a value cell next to a labelled cell."""
    return _set_named_value(id=id, label=label, value=value, direction=direction, expected_revision=expected_revision)

@mcp.tool()
def koredocs_sheet_range_read(
    id: Annotated[int, 'KoreSheet file id.'],
    range: Annotated[str, 'A1-style range such as A1:C10, A:A, or 2:4.'],
    values_only: Annotated[bool, 'When true, return scalar values instead of full cell objects.'] = False,
) -> dict:
    """Canonical prefixed alias for read_sheet_range."""
    return _read_sheet_range(id=id, range=range, values_only=values_only)


@mcp.tool()
def koredocs_sheet_cells_write(
    id: Annotated[int, 'KoreSheet file id.'],
    cells: Annotated[dict[str, Any], 'Sparse cell updates keyed by A1 address.'],
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Canonical prefixed alias for write_sheet_cells."""
    return _write_sheet_cells(id=id, cells=cells, expected_revision=expected_revision)


@mcp.tool()
def koredocs_sheet_table_read(
    id: Annotated[int, 'KoreSheet file id.'],
    header_row: Annotated[int, 'Row number containing column headers.'] = 1,
    range_ref: Annotated[Optional[str], 'Optional A1-style range constraining the table view.'] = None,
) -> dict:
    """Canonical prefixed alias for read_sheet_table."""
    return _read_sheet_table(id=id, header_row=header_row, range_ref=range_ref)


@mcp.tool()
def koredocs_sheet_rows_append(
    id: Annotated[int, 'KoreSheet file id.'],
    rows: Annotated[list[Any], 'Rows to append.'],
    start_col: Annotated[str, 'Start column for list-style rows. Defaults to A.'] = 'A',
    header_row: Annotated[Optional[int], 'Header row to use when rows are objects keyed by column names.'] = None,
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Canonical prefixed alias for append_sheet_rows."""
    return _append_sheet_rows(id=id, rows=rows, start_col=start_col, header_row=header_row, expected_revision=expected_revision)


@mcp.tool()
def koredocs_sheet_rows_upsert(
    id: Annotated[int, 'KoreSheet file id.'],
    rows: Annotated[list[dict[str, Any]], 'Header-keyed rows to update or append.'],
    key_columns: Annotated[list[str], 'Header names that uniquely identify an existing row.'],
    header_row: Annotated[int, 'Row number containing column headers.'] = 1,
    create_missing_columns: Annotated[bool, 'When true, create any missing header columns before writing.'] = False,
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Canonical prefixed alias for upsert_sheet_rows."""
    return _upsert_sheet_rows(
        id=id,
        rows=rows,
        key_columns=key_columns,
        header_row=header_row,
        create_missing_columns=create_missing_columns,
        expected_revision=expected_revision,
    )


@mcp.tool()
def koredocs_sheet_range_clear(
    id: Annotated[int, 'KoreSheet file id.'],
    range: Annotated[str, 'A1-style range such as B2:D9, A:A, or 3:3.'],
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Canonical prefixed alias for clear_sheet_range."""
    return _clear_sheet_range(id=id, range=range, expected_revision=expected_revision)


