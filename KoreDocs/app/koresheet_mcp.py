"""MCP tools and helpers for .koresheet documents."""

from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from typing import Any, Optional, Annotated, Union, Literal
from . import korefile
from ._mcp_shared import mcp, _file_summary, _create_serialized_file, _ensure_extension, _now_iso


def _sheet_content(
    title: str,
    cells: Optional[dict] = None,
    cols: int = 26,
    rows: int = 100,
) -> str:
    normalized_cells = _sheet_normalize_cells(cells or {})
    doc = {
        'version': 1,
        'meta': {
            'title': title,
            'created': datetime.now(timezone.utc).date().isoformat(),
        },
        'cols': cols,
        'rows': rows,
        'cells': normalized_cells,
    }
    _sheet_recompute_all(doc)
    return json.dumps(doc, indent=2)


_SHEET_ADDR_RE = re.compile(r'^([A-Z]+)([1-9]\d*)$')
_SHEET_COL_RANGE_RE = re.compile(r'^([A-Z]+):([A-Z]+)$')
_SHEET_ROW_RANGE_RE = re.compile(r'^([1-9]\d*):([1-9]\d*)$')


def _sheet_file(file_id: int) -> dict:
    file = korefile.get_file(file_id, include_content=True)
    if file is None:
        raise ValueError(f'File not found: {file_id}')
    if file.get('ext') != 'koresheet':
        raise ValueError(f'File {file_id} is not a .koresheet document')
    return file


def _load_sheet_doc(file_id: int) -> tuple[dict, dict]:
    file = _sheet_file(file_id)
    try:
        doc = json.loads(file.get('content') or '{}')
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{file["name"]} does not contain valid KoreSheet JSON') from exc
    if not isinstance(doc, dict):
        raise ValueError(f'{file["name"]} must contain a top-level JSON object')
    doc.setdefault('version', 1)
    doc.setdefault('meta', {})
    doc.setdefault('cols', 26)
    doc.setdefault('rows', 100)
    doc.setdefault('cells', {})
    if not isinstance(doc['meta'], dict) or not isinstance(doc['cells'], dict):
        raise ValueError(f'{file["name"]} is not a valid KoreSheet document')
    doc['cells'] = _sheet_normalize_cells(doc['cells'])
    _sheet_recompute_all(doc)
    return file, doc


def _sheet_normalize_cells(cells: dict) -> dict:
    normalized: dict[str, dict] = {}
    if not isinstance(cells, dict):
        raise ValueError('cells must be an object keyed by A1 address')

    for addr, cell in cells.items():
        _sheet_parse_addr(addr)

        if isinstance(cell, dict):
            normalized[addr] = dict(cell)
            continue

        if cell in ('', None):
            continue

        normalized[addr] = {'value': cell}

    return normalized


def _save_sheet_doc(file_id: int, doc: dict, *, expected_revision: int | None = None) -> dict:
    content = json.dumps(doc, indent=2)
    updated = korefile.update_file(file_id, content, expected_revision=expected_revision)
    if updated is None:
        raise ValueError(f'File not found: {file_id}')
    return updated


def _sheet_col_to_index(col_ref: str) -> int:
    col = col_ref.strip().upper()
    if not col or not col.isalpha():
        raise ValueError(f'Invalid column reference: {col_ref}')
    index = 0
    for ch in col:
        index = index * 26 + (ord(ch) - 64)
    return index


def _sheet_index_to_col(index: int) -> str:
    if index < 1:
        raise ValueError(f'Invalid column index: {index}')
    letters: list[str] = []
    value = index
    while value:
        value, rem = divmod(value - 1, 26)
        letters.append(chr(65 + rem))
    return ''.join(reversed(letters))


def _sheet_parse_addr(addr: str) -> tuple[int, int]:
    match = _SHEET_ADDR_RE.match((addr or '').strip().upper())
    if not match:
        raise ValueError(f'Invalid cell address: {addr}')
    return _sheet_col_to_index(match.group(1)), int(match.group(2))


def _sheet_addr(col: int, row: int) -> str:
    return f'{_sheet_index_to_col(col)}{row}'


def _sheet_range_bounds(range_ref: str, cols: int, rows: int) -> dict:
    ref = (range_ref or '').strip().upper()
    if not ref:
        raise ValueError('range must not be empty')
    if _SHEET_ADDR_RE.match(ref):
        start_col, start_row = _sheet_parse_addr(ref)
        end_col, end_row = start_col, start_row
    else:
        col_match = _SHEET_COL_RANGE_RE.match(ref)
        row_match = _SHEET_ROW_RANGE_RE.match(ref)
        if col_match:
            start_col = _sheet_col_to_index(col_match.group(1))
            end_col = _sheet_col_to_index(col_match.group(2))
            start_row, end_row = 1, rows
        elif row_match:
            start_row = int(row_match.group(1))
            end_row = int(row_match.group(2))
            start_col, end_col = 1, cols
        elif ':' in ref:
            left, right = ref.split(':', 1)
            start_col, start_row = _sheet_parse_addr(left)
            end_col, end_row = _sheet_parse_addr(right)
        else:
            raise ValueError(f'Invalid range reference: {range_ref}')
    start_col, end_col = sorted((start_col, end_col))
    start_row, end_row = sorted((start_row, end_row))
    if start_col < 1 or end_col > cols or start_row < 1 or end_row > rows:
        raise ValueError(f'Range {range_ref} is outside sheet bounds')
    return {
        'start_col': start_col,
        'end_col': end_col,
        'start_row': start_row,
        'end_row': end_row,
        'start_addr': _sheet_addr(start_col, start_row),
        'end_addr': _sheet_addr(end_col, end_row),
        'range': f'{_sheet_addr(start_col, start_row)}:{_sheet_addr(end_col, end_row)}',
    }


def _sheet_cell_scalar(cell: dict | None) -> Any:
    if not isinstance(cell, dict):
        return None
    if 'formula' in cell:
        return cell.get('computed', cell.get('formula'))
    return cell.get('value')


def _sheet_cell_in_bounds(addr: str, bounds: dict) -> bool:
    col, row = _sheet_parse_addr(addr)
    return (
        bounds['start_col'] <= col <= bounds['end_col']
        and bounds['start_row'] <= row <= bounds['end_row']
    )


def _sheet_used_range(cells: dict) -> str | None:
    coords: list[tuple[int, int]] = []
    for addr, cell in cells.items():
        if not isinstance(cell, dict):
            continue
        if 'value' not in cell and 'formula' not in cell and 'style' not in cell:
            continue
        coords.append(_sheet_parse_addr(addr))
    if not coords:
        return None
    min_col = min(col for col, _ in coords)
    max_col = max(col for col, _ in coords)
    min_row = min(row for _, row in coords)
    max_row = max(row for _, row in coords)
    return f'{_sheet_addr(min_col, min_row)}:{_sheet_addr(max_col, max_row)}'


def _sheet_summary(file: dict, doc: dict, *, include_cells: bool = False) -> dict:
    cells = doc.get('cells', {})
    summary = {
        'id': file['id'],
        'name': file['name'],
        'revision': file.get('revision', 1),
        'created_at': file.get('created_at'),
        'modified_at': file.get('modified_at'),
        'meta': doc.get('meta', {}),
        'version': doc.get('version', 1),
        'cols': doc.get('cols', 26),
        'rows': doc.get('rows', 100),
        'used_range': _sheet_used_range(cells),
        'non_empty_cells': len(cells),
    }
    if include_cells:
        summary['cells'] = cells
    return summary


def _sheet_header_map(doc: dict, header_row: int) -> dict[str, int]:
    cols = int(doc.get('cols', 26))
    mapping: dict[str, int] = {}
    for col in range(1, cols + 1):
        value = _sheet_cell_scalar(doc['cells'].get(_sheet_addr(col, header_row)))
        if isinstance(value, str) and value.strip():
            mapping[value.strip()] = col
    return mapping


def _sheet_prune_cell(cell: dict | None) -> dict | None:
    if not isinstance(cell, dict):
        return None
    pruned = dict(cell)
    if pruned.get('style') == {}:
        pruned.pop('style', None)
    if pruned.get('value') == '' and 'formula' not in pruned and 'style' not in pruned:
        pruned.pop('value', None)
    if pruned.get('computed') is None:
        pruned.pop('computed', None)
    if not pruned:
        return None
    return pruned


def _sheet_apply_update(existing: dict | None, update: Any) -> dict | None:
    if isinstance(update, dict):
        if update.get('clear'):
            return None
        cell = dict(existing or {})
        if 'value' in update:
            cell['value'] = update['value']
            cell.pop('formula', None)
            cell.pop('computed', None)
        if 'formula' in update:
            formula = str(update['formula'])
            cell['formula'] = formula
            cell['value'] = formula
            cell.pop('computed', None)
        if 'style' in update:
            style = update['style']
            if style is None:
                cell.pop('style', None)
            elif not isinstance(style, dict):
                raise ValueError('cell style updates must be objects or null')
            else:
                base_style = cell.get('style') if isinstance(cell.get('style'), dict) else {}
                cell['style'] = {**base_style, **style}
        return _sheet_prune_cell(cell)
    cell = dict(existing or {})
    cell['value'] = update
    cell.pop('formula', None)
    cell.pop('computed', None)
    return _sheet_prune_cell(cell)


def _sheet_parse_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return _sheet_round_number(value)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return _sheet_round_number(float(text))
    except ValueError:
        return None


def _sheet_round_number(value: float) -> int | float:
    rounded = float(f'{float(value):.15g}')
    if rounded.is_integer():
        return int(rounded)
    return rounded


def _sheet_expand_range(range_ref: str) -> list[str]:
    left, right = range_ref.upper().split(':', 1)
    start_col, start_row = _sheet_parse_addr(left)
    end_col, end_row = _sheet_parse_addr(right)
    start_col, end_col = sorted((start_col, end_col))
    start_row, end_row = sorted((start_row, end_row))
    addrs: list[str] = []
    for row in range(start_row, end_row + 1):
        for col in range(start_col, end_col + 1):
            addrs.append(_sheet_addr(col, row))
    return addrs


def _sheet_tokenize_formula(src: str) -> list[dict[str, Any]] | None:
    tokens: list[dict[str, Any]] = []
    i = 0
    while i < len(src):
        ch = src[i]
        if ch.isspace():
            i += 1
            continue
        match = re.match(r'^([A-Za-z]+\d+:[A-Za-z]+\d+)', src[i:])
        if match:
            value = match.group(1).upper()
            tokens.append({'type': 'RANGE', 'value': value})
            i += len(value)
            continue
        match = re.match(r'^([A-Za-z]+\d+)', src[i:])
        if match:
            value = match.group(1).upper()
            tokens.append({'type': 'CELL', 'value': value})
            i += len(value)
            continue
        match = re.match(r'^([A-Za-z]+)(?=\s*\()', src[i:])
        if match:
            value = match.group(1).upper()
            tokens.append({'type': 'NAME', 'value': value})
            i += len(value)
            continue
        match = re.match(r'^(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)', src[i:])
        if match:
            value = match.group(1)
            tokens.append({'type': 'NUM', 'value': _sheet_round_number(float(value))})
            i += len(value)
            continue
        if ch == '"':
            j = i + 1
            while j < len(src) and src[j] != '"':
                j += 1
            if j >= len(src):
                return None
            tokens.append({'type': 'STR', 'value': src[i + 1:j]})
            i = j + 1
            continue
        if ch in '+-*/^':
            tokens.append({'type': 'OP', 'value': ch})
        elif ch == '(':
            tokens.append({'type': 'LPAREN'})
        elif ch == ')':
            tokens.append({'type': 'RPAREN'})
        elif ch == ',':
            tokens.append({'type': 'COMMA'})
        else:
            return None
        i += 1
    return tokens


def _sheet_resolve_cell_value(addr: str, cells: dict, seen: set[str]) -> Any:
    if addr in seen:
        return '#CYCLE'
    cell = cells.get(addr)
    if not isinstance(cell, dict):
        return 0
    if 'formula' in cell:
        seen.add(addr)
        value = _sheet_evaluate_formula(str(cell['formula']), cells, seen)
        seen.remove(addr)
        return value
    value = cell.get('value')
    if value in ('', None):
        return 0
    numeric = _sheet_parse_number(value)
    return numeric if numeric is not None else value


def _sheet_resolve_numeric_value(addr: str, cells: dict, seen: set[str]) -> int | float | None | str:
    if addr in seen:
        return '#ERR'
    cell = cells.get(addr)
    if not isinstance(cell, dict):
        return None
    if 'formula' in cell:
        seen.add(addr)
        value = _sheet_evaluate_formula(str(cell['formula']), cells, seen)
        seen.remove(addr)
        return value if isinstance(value, (int, float)) else None
    value = cell.get('value')
    if value in ('', None):
        return None
    return _sheet_parse_number(value)


def _sheet_evaluate_formula(formula: str, cells: dict, seen: set[str] | None = None) -> Any:
    trimmed = (formula or '').strip()
    if not trimmed.startswith('='):
        return '#ERR'
    tokens = _sheet_tokenize_formula(trimmed[1:].strip())
    if not tokens:
        return '#ERR'

    pos = 0
    active = seen or set()

    def peek() -> dict[str, Any] | None:
        return tokens[pos] if pos < len(tokens) else None

    def advance() -> dict[str, Any] | None:
        nonlocal pos
        token = peek()
        if token is not None:
            pos += 1
        return token

    def eat(token_type: str) -> dict[str, Any] | None:
        token = peek()
        if token and token.get('type') == token_type:
            return advance()
        return None

    def expr() -> Any:
        value = term()
        while peek() and peek().get('type') == 'OP' and peek().get('value') in ('+', '-'):
            op = advance()['value']
            rhs = term()
            if value == '#DIV0' or rhs == '#DIV0':
                return '#DIV0'
            if value in ('#ERR', '#CYCLE') or rhs in ('#ERR', '#CYCLE'):
                return '#ERR'
            if not isinstance(value, (int, float)) or not isinstance(rhs, (int, float)):
                return '#ERR'
            value = _sheet_round_number(value + rhs if op == '+' else value - rhs)
        return value

    def term() -> Any:
        value = power()
        while peek() and peek().get('type') == 'OP' and peek().get('value') in ('*', '/'):
            op = advance()['value']
            rhs = power()
            if value == '#DIV0' or rhs == '#DIV0':
                return '#DIV0'
            if value in ('#ERR', '#CYCLE') or rhs in ('#ERR', '#CYCLE'):
                return '#ERR'
            if not isinstance(value, (int, float)) or not isinstance(rhs, (int, float)):
                return '#ERR'
            if op == '/' and rhs == 0:
                return '#DIV0'
            value = _sheet_round_number(value * rhs if op == '*' else value / rhs)
        return value

    def power() -> Any:
        value = unary()
        while peek() and peek().get('type') == 'OP' and peek().get('value') == '^':
            advance()
            rhs = unary()
            if not isinstance(value, (int, float)) or not isinstance(rhs, (int, float)):
                return '#ERR'
            value = _sheet_round_number(value ** rhs)
        return value

    def unary() -> Any:
        if peek() and peek().get('type') == 'OP' and peek().get('value') == '-':
            advance()
            value = unary()
            if not isinstance(value, (int, float)):
                return '#ERR'
            return _sheet_round_number(-value)
        return primary()

    def aggregate(name: str) -> Any:
        numbers: list[int | float] = []
        while peek() and peek().get('type') != 'RPAREN':
            if peek().get('type') == 'RANGE':
                range_value = advance()['value']
                for addr in _sheet_expand_range(range_value):
                    value = _sheet_resolve_numeric_value(addr, cells, active)
                    if value == '#ERR':
                        return '#ERR'
                    if value is not None:
                        numbers.append(value)
            else:
                value = expr()
                if value == '#ERR':
                    return '#ERR'
                if isinstance(value, (int, float)):
                    numbers.append(value)
            if peek() and peek().get('type') == 'COMMA':
                advance()
        if not eat('RPAREN'):
            return '#ERR'
        if name == 'COUNT':
            return len(numbers)
        if not numbers:
            return 0
        if name == 'SUM':
            return _sheet_round_number(sum(numbers))
        if name == 'AVERAGE':
            return _sheet_round_number(sum(numbers) / len(numbers))
        if name == 'MIN':
            return min(numbers)
        if name == 'MAX':
            return max(numbers)
        return '#ERR'

    def primary() -> Any:
        token = peek()
        if token is None:
            return '#ERR'
        if token['type'] in ('NUM', 'STR'):
            advance()
            return token['value']
        if token['type'] == 'CELL':
            advance()
            return _sheet_resolve_cell_value(token['value'], cells, active)
        if token['type'] == 'NAME':
            name = advance()['value']
            if not eat('LPAREN'):
                return '#ERR'
            return aggregate(name)
        if token['type'] == 'LPAREN':
            advance()
            value = expr()
            if not eat('RPAREN'):
                return '#ERR'
            return value
        return '#ERR'

    result = expr()
    if pos != len(tokens):
        return '#ERR'
    return result


def _sheet_recompute_all(doc: dict) -> None:
    cells = doc.get('cells', {})
    for cell in cells.values():
        if isinstance(cell, dict) and 'formula' in cell:
            cell['computed'] = _sheet_evaluate_formula(str(cell['formula']), cells)


def _sheet_unique_headers(headers: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    unique: list[str] = []
    for header in headers:
        counts[header] = counts.get(header, 0) + 1
        unique.append(header if counts[header] == 1 else f'{header}__{counts[header]}')
    return unique


def _sheet_headers_for_range(doc: dict, header_row: int, start_col: int, end_col: int) -> list[dict[str, Any]]:
    raw_headers: list[str] = []
    columns: list[int] = []
    for col in range(start_col, end_col + 1):
        value = _sheet_cell_scalar(doc['cells'].get(_sheet_addr(col, header_row)))
        if isinstance(value, str) and value.strip():
            header = value.strip()
        elif value not in (None, ''):
            header = str(value)
        else:
            header = _sheet_index_to_col(col)
        raw_headers.append(header)
        columns.append(col)
    unique = _sheet_unique_headers(raw_headers)
    return [
        {'name': name, 'source': raw, 'column': _sheet_index_to_col(col), 'column_index': col}
        for name, raw, col in zip(unique, raw_headers, columns)
    ]


def _sheet_last_used_row(doc: dict, minimum_row: int) -> int:
    used = _sheet_used_range(doc.get('cells', {}))
    if not used:
        return minimum_row
    _, last_row = _sheet_parse_addr(used.split(':', 1)[1])
    return max(last_row, minimum_row)


def _sheet_row_key(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def _sheet_ensure_headers(doc: dict, header_row: int, names: list[str]) -> dict[str, int]:
    header_map = _sheet_header_map(doc, header_row)
    next_col = max(header_map.values(), default=0) + 1
    for name in names:
        if name in header_map:
            continue
        addr = _sheet_addr(next_col, header_row)
        doc['cells'][addr] = {'value': name}
        header_map[name] = next_col
        next_col += 1
    doc['cols'] = max(int(doc.get('cols', 26)), next_col - 1)
    doc['rows'] = max(int(doc.get('rows', 100)), header_row)
    return header_map


def _sheet_next_append_row(doc: dict, header_row: int | None = None) -> int:
    used = _sheet_used_range(doc.get('cells', {}))
    if not used:
        return (header_row + 1) if header_row else 1
    end_addr = used.split(':', 1)[1]
    _, last_row = _sheet_parse_addr(end_addr)
    min_row = (header_row + 1) if header_row else 1
    return max(last_row + 1, min_row)


def _sheet_write_row_values(doc: dict, row_index: int, values: list[Any], start_col: int) -> list[str]:
    written: list[str] = []
    for offset, value in enumerate(values):
        addr = _sheet_addr(start_col + offset, row_index)
        cell = _sheet_apply_update(doc['cells'].get(addr), value)
        if cell is None:
            doc['cells'].pop(addr, None)
        else:
            doc['cells'][addr] = cell
        written.append(addr)
    return written


def _sheet_non_empty(value: Any) -> bool:
    return value not in (None, '')


def _sheet_effective_header_row(doc: dict, header_row: int | None) -> int:
    if header_row is not None:
        if header_row < 1 or header_row > int(doc['rows']):
            raise ValueError(f'header_row {header_row} is outside sheet bounds')
        return header_row

    used = _sheet_used_range(doc.get('cells', {}))
    if not used:
        return 1

    start_addr, end_addr = used.split(':', 1)
    _, start_row = _sheet_parse_addr(start_addr)
    _, end_row = _sheet_parse_addr(end_addr)
    cols = int(doc.get('cols', 26))

    best_row = start_row
    best_score = -1

    for row in range(start_row, min(end_row, start_row + 9) + 1):
        string_count = 0
        non_empty_count = 0
        for col in range(1, cols + 1):
            value = _sheet_cell_scalar(doc['cells'].get(_sheet_addr(col, row)))
            if not _sheet_non_empty(value):
                continue
            non_empty_count += 1
            if isinstance(value, str) and value.strip():
                string_count += 1
        if string_count >= 2:
            score = string_count * 10 + non_empty_count
            if score > best_score:
                best_score = score
                best_row = row

    if best_score >= 0:
        return best_row

    return start_row


def _sheet_table_bounds(doc: dict, header_row: int, range_ref: str | None = None) -> dict:
    default_range = _sheet_used_range(doc['cells']) or f'A{header_row}:{_sheet_addr(int(doc["cols"]), header_row)}'
    bounds = _sheet_range_bounds(range_ref or default_range, int(doc['cols']), int(doc['rows']))
    if not (bounds['start_row'] <= header_row <= bounds['end_row']):
        raise ValueError('header_row must be inside the requested range')
    return bounds


def _sheet_table_headers(doc: dict, header_row: int, range_ref: str | None = None) -> tuple[dict, list[dict[str, Any]]]:
    bounds = _sheet_table_bounds(doc, header_row, range_ref)
    headers = _sheet_headers_for_range(doc, header_row, bounds['start_col'], bounds['end_col'])
    return bounds, headers


def _sheet_table_rows(doc: dict, header_row: int, headers: list[dict[str, Any]], bounds: dict, *, include_empty: bool = False) -> list[dict[str, Any]]:
    rows_out: list[dict[str, Any]] = []
    for row_number in range(header_row + 1, bounds['end_row'] + 1):
        row_obj = {'_row': row_number}
        has_values = False
        for header in headers:
            addr = _sheet_addr(header['column_index'], row_number)
            value = _sheet_cell_scalar(doc['cells'].get(addr))
            row_obj[header['name']] = value
            if _sheet_non_empty(value):
                has_values = True
        if has_values or include_empty:
            rows_out.append(row_obj)
    return rows_out


def _sheet_match_expected(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        for operator, operand in expected.items():
            if operator == 'eq' and _sheet_row_key(actual) != _sheet_row_key(operand):
                return False
            if operator == 'contains':
                if operand is None:
                    return False
                if str(operand).strip().lower() not in str(actual or '').lower():
                    return False
            if operator == 'in':
                if actual not in operand:
                    return False
            if operator in ('gt', 'gte', 'lt', 'lte'):
                left = _sheet_parse_number(actual)
                right = _sheet_parse_number(operand)
                if left is None or right is None:
                    return False
                if operator == 'gt' and not (left > right):
                    return False
                if operator == 'gte' and not (left >= right):
                    return False
                if operator == 'lt' and not (left < right):
                    return False
                if operator == 'lte' and not (left <= right):
                    return False
        return True
    return _sheet_row_key(actual) == _sheet_row_key(expected)


def _sheet_row_matches(row_obj: dict[str, Any], filters: dict[str, Any], match_mode: str) -> bool:
    matches = []
    for key, expected in filters.items():
        matches.append(_sheet_match_expected(row_obj.get(key), expected))
    if not matches:
        return True
    return all(matches) if match_mode == 'all' else any(matches)


def _sheet_find_label_matches(doc: dict, labels: list[str] | None = None, match_mode: str = 'contains') -> list[dict]:
    matches: list[dict] = []
    requested = [label.strip() for label in (labels or []) if label and label.strip()]
    for addr, cell in doc.get('cells', {}).items():
        value = _sheet_cell_scalar(cell)
        if not isinstance(value, str) or not value.strip():
            continue
        normalized = value.strip()
        if requested:
            if match_mode == 'exact' and normalized not in requested:
                continue
            if match_mode != 'exact' and not any(label.lower() in normalized.lower() for label in requested):
                continue
        col, row = _sheet_parse_addr(addr)
        right_addr = _sheet_addr(col + 1, row) if col < int(doc.get('cols', 26)) else None
        below_addr = _sheet_addr(col, row + 1) if row < int(doc.get('rows', 100)) else None
        matches.append({
            'label': normalized,
            'addr': addr,
            'right_addr': right_addr,
            'right_value': _sheet_cell_scalar(doc['cells'].get(right_addr)) if right_addr else None,
            'below_addr': below_addr,
            'below_value': _sheet_cell_scalar(doc['cells'].get(below_addr)) if below_addr else None,
        })
    return matches


def _sheet_label_target(doc: dict, label: str, direction: str = 'right') -> tuple[dict, str]:
    matches = _sheet_find_label_matches(doc, [label], match_mode='exact')
    if not matches:
        matches = _sheet_find_label_matches(doc, [label], match_mode='contains')
    if not matches:
        raise ValueError(f'No label found matching {label!r}')
    match = matches[0]
    target_key = f'{direction}_addr'
    target_addr = match.get(target_key)
    if not target_addr:
        raise ValueError(f'Label {label!r} has no writable cell in direction {direction!r}')
    return match, target_addr


def _sheet_write_headers(doc: dict, headers: list[str], header_row: int, start_col: int) -> list[str]:
    written: list[str] = []
    for offset, header in enumerate(headers):
        addr = _sheet_addr(start_col + offset, header_row)
        doc['cells'][addr] = {'value': header}
        written.append(addr)
    doc['cols'] = max(int(doc.get('cols', 26)), start_col + len(headers) - 1)
    doc['rows'] = max(int(doc.get('rows', 100)), header_row)
    return written


def _sheet_build_table_doc(title: str, headers: list[str], rows: list[Any], header_row: int = 1, start_col: str = 'A') -> dict:
    start_col_index = _sheet_col_to_index(start_col)
    doc = {
        'version': 1,
        'meta': {
            'title': title,
            'created': datetime.now(timezone.utc).date().isoformat(),
        },
        'cols': 26,
        'rows': 100,
        'cells': {},
    }
    _sheet_write_headers(doc, headers, header_row, start_col_index)
    next_row = header_row + 1
    if all(isinstance(row, dict) for row in rows):
        header_map = _sheet_ensure_headers(doc, header_row, headers)
        for row_obj in rows:
            for header in headers:
                addr = _sheet_addr(header_map[header], next_row)
                cell = _sheet_apply_update(doc['cells'].get(addr), row_obj.get(header))
                if cell is None:
                    doc['cells'].pop(addr, None)
                else:
                    doc['cells'][addr] = cell
            next_row += 1
    elif all(isinstance(row, list) for row in rows):
        for row_values in rows:
            _sheet_write_row_values(doc, next_row, row_values, start_col_index)
            next_row += 1
    elif rows:
        raise ValueError('rows must contain either all objects or all lists')
    _sheet_recompute_all(doc)
    return doc


def _sheet_compounding_doc(title: str, principal: int | float, annual_rate: int | float, years: int) -> dict:
    rows = max(20, years + 8)
    doc = {
        'version': 1,
        'meta': {
            'title': title,
            'created': datetime.now(timezone.utc).date().isoformat(),
        },
        'cols': 8,
        'rows': rows,
        'cells': {
            'A1': {'value': 'Starting Balance'},
            'B1': {'value': principal},
            'A2': {'value': 'Annual Rate'},
            'B2': {'value': annual_rate},
            'A4': {'value': 'Year'},
            'B4': {'value': 'Opening Balance'},
            'C4': {'value': 'Interest'},
            'D4': {'value': 'Ending Balance'},
        },
    }
    for year in range(1, years + 1):
        row = year + 4
        prev_row = row - 1
        opening_formula = '=B1' if year == 1 else f'=D{prev_row}'
        doc['cells'][f'A{row}'] = {'value': year}
        doc['cells'][f'B{row}'] = {'formula': opening_formula, 'value': opening_formula}
        interest_formula = f'=B{row}*B2'
        ending_formula = f'=B{row}+C{row}'
        doc['cells'][f'C{row}'] = {'formula': interest_formula, 'value': interest_formula}
        doc['cells'][f'D{row}'] = {'formula': ending_formula, 'value': ending_formula}
    _sheet_recompute_all(doc)
    return doc


def get_sheet(
    id: Annotated[int, 'KoreSheet file id.'],
    include_cells: Annotated[bool, 'When true, include the sparse cell map in the response.'] = False,
) -> dict:
    """Return KoreSheet metadata, dimensions, used range, and optionally the sparse cell map."""
    file, doc = _load_sheet_doc(id)
    return _sheet_summary(file, doc, include_cells=include_cells)


def read_sheet_range(
    id: Annotated[int, 'KoreSheet file id.'],
    range: Annotated[str, 'A1-style range such as A1:C10, A:A, or 2:4.'],
    values_only: Annotated[bool, 'When true, return scalar values instead of full cell objects.'] = False,
) -> dict:
    """Read a rectangular region from a KoreSheet document."""
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


def write_sheet_cells(
    id: Annotated[int, 'KoreSheet file id.'],
    cells: Annotated[dict[str, Any], 'Sparse cell updates keyed by A1 address. Values may be scalars or objects like {value, formula, style, clear}.'],
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check. When provided, the sheet must still be at this revision.'] = None,
) -> dict:
    """Apply sparse cell updates to a KoreSheet document without replacing the whole file."""
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


def read_sheet_table(
    id: Annotated[int, 'KoreSheet file id.'],
    header_row: Annotated[int, 'Row number containing column headers.'] = 1,
    range_ref: Annotated[Optional[str], 'Optional A1-style range constraining the table view.'] = None,
) -> dict:
    """Read a sheet region as header-keyed row objects."""
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


def append_sheet_rows(
    id: Annotated[int, 'KoreSheet file id.'],
    rows: Annotated[list[Any], 'Rows to append. Each row may be a list of values or an object keyed by header names.'],
    start_col: Annotated[str, 'Start column for list-style rows. Defaults to A.'] = 'A',
    header_row: Annotated[Optional[int], 'Header row to use when rows are objects keyed by column names.'] = None,
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check. When provided, the sheet must still be at this revision.'] = None,
) -> dict:
    """Append tabular data to a KoreSheet document."""
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
    id: Annotated[int, 'KoreSheet file id.'],
    rows: Annotated[list[dict[str, Any]], 'Header-keyed rows to update or append.'],
    key_columns: Annotated[list[str], 'Header names that uniquely identify an existing row.'],
    header_row: Annotated[int, 'Row number containing column headers.'] = 1,
    create_missing_columns: Annotated[bool, 'When true, create any missing header columns before writing.'] = False,
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check. When provided, the sheet must still be at this revision.'] = None,
) -> dict:
    """Update matching rows by key, or append new rows when no match exists."""
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


def clear_sheet_range(
    id: Annotated[int, 'KoreSheet file id.'],
    range: Annotated[str, 'A1-style range such as B2:D9, A:A, or 3:3.'],
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check. When provided, the sheet must still be at this revision.'] = None,
) -> dict:
    """Clear all cells inside a range of a KoreSheet document."""
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


def create_koresheet(
    folder_path: Annotated[str, 'Folder path in KoreFile, such as "/" or "/Projects". Missing folders are created.'],
    name: Annotated[str, 'Filename, with or without the .koresheet extension.'],
    cells: Annotated[Optional[dict], 'Sparse cell map keyed by A1 address, e.g. {"A1": {"value": "Name"}}.'] = None,
    title: Annotated[Optional[str], 'Sheet title. Defaults to the filename stem.'] = None,
    cols: Annotated[int, 'Number of columns. Defaults to 26.'] = 26,
    rows: Annotated[int, 'Number of rows. Defaults to 100.'] = 100,
) -> dict:
    """Create a .koresheet document from a sparse cell map."""
    doc_title = title or name.rsplit('.', 1)[0]
    content = _sheet_content(doc_title, cells, cols, rows)
    return _create_serialized_file(folder_path, name, 'koresheet', content, {'title': doc_title})


@mcp.tool()
def koredocs_get_sheet(
    id: Annotated[int, 'KoreSheet file id.'],
    include_cells: Annotated[bool, 'When true, include the sparse cell map in the response.'] = False,
) -> dict:
    """Canonical prefixed alias for get_sheet."""
    return get_sheet(id=id, include_cells=include_cells)


@mcp.tool()
def koredocs_get_sheet_headers(
    id: Annotated[int, 'KoreSheet file id.'],
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    range_ref: Annotated[Optional[str], 'Optional A1-style range constraining the header scan.'] = None,
) -> dict:
    """Return the detected column headers for a sheet, with an optional guessed header row."""
    file, doc = _load_sheet_doc(id)
    resolved_header_row = _sheet_effective_header_row(doc, header_row)
    bounds, headers = _sheet_table_headers(doc, resolved_header_row, range_ref)
    return {
        **_sheet_summary(file, doc, include_cells=False),
        'header_row': resolved_header_row,
        'range': bounds['range'],
        'headers': headers,
    }


@mcp.tool()
def koredocs_find_sheet_column(
    id: Annotated[int, 'KoreSheet file id.'],
    header_name: Annotated[str, 'Column header to locate.'],
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    match_mode: Annotated[Literal['exact', 'contains'], 'Header matching mode.'] = 'exact',
) -> dict:
    """Locate a sheet column by header name."""
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


@mcp.tool()
def koredocs_preview_sheet(
    id: Annotated[int, 'KoreSheet file id.'],
    sample_rows: Annotated[int, 'Maximum number of table rows to preview.'] = 5,
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    range_ref: Annotated[Optional[str], 'Optional A1-style range constraining the preview.'] = None,
) -> dict:
    """Return a compact preview of a sheet as header-keyed rows."""
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


@mcp.tool()
def koredocs_describe_sheet(
    id: Annotated[int, 'KoreSheet file id.'],
    sample_rows: Annotated[int, 'Maximum number of table rows to preview.'] = 5,
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    range_ref: Annotated[Optional[str], 'Optional A1-style range constraining the structural description.'] = None,
) -> dict:
    """Return a structural summary of a sheet, including guessed headers, sample rows, labels, and formulas."""
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


@mcp.tool()
def koredocs_find_sheet_rows(
    id: Annotated[int, 'KoreSheet file id.'],
    filters: Annotated[dict[str, Any], 'Header-keyed filters. Values may be scalars or objects like {contains: "foo"} or {gte: 10}.'],
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    range_ref: Annotated[Optional[str], 'Optional A1-style range constraining the search.'] = None,
    match_mode: Annotated[Literal['all', 'any'], 'Whether all filters must match or any filter may match.'] = 'all',
) -> dict:
    """Find table rows by header-keyed filters."""
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


@mcp.tool()
def koredocs_update_sheet_rows(
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


@mcp.tool()
def koredocs_set_sheet_headers(
    id: Annotated[int, 'KoreSheet file id.'],
    headers: Annotated[list[str], 'Header names to write into the sheet.'],
    header_row: Annotated[int, 'Row number where the headers should be written.'] = 1,
    start_col: Annotated[str, 'Starting column for the first header.'] = 'A',
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Write or replace a contiguous header row."""
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


@mcp.tool()
def koredocs_append_sheet_table_rows(
    id: Annotated[int, 'KoreSheet file id.'],
    rows: Annotated[list[Any], 'Rows to append. Each row may be a list of values or an object keyed by header names.'],
    header_row: Annotated[Optional[int], 'Optional header row. When omitted, KoreDocs will guess a likely header row.'] = None,
    create_missing_columns: Annotated[bool, 'When true, create any missing columns referenced by object-style rows.'] = False,
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Append rows using a table-first interface rather than raw cell addresses."""
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


@mcp.tool()
def koredocs_create_sheet_table(
    folder_path: Annotated[str, 'Folder path in KoreFile, such as "/Projects/Calcs". Missing folders are created.'],
    name: Annotated[str, 'Filename, with or without the .koresheet extension.'],
    headers: Annotated[list[str], 'Ordered list of column headers.'],
    rows: Annotated[Optional[list[Any]], 'Optional initial rows. Each row may be a list of values or an object keyed by header names.'] = None,
    header_row: Annotated[int, 'Row number where the headers should be written.'] = 1,
    start_col: Annotated[str, 'Starting column for the first header.'] = 'A',
    title: Annotated[Optional[str], 'Optional sheet title. Defaults to the filename stem.'] = None,
) -> dict:
    """Create a spreadsheet from headers plus initial rows, using table semantics instead of raw cells."""
    if not headers:
        raise ValueError('headers must not be empty')
    doc_title = title or name.rsplit('.', 1)[0]
    doc = _sheet_build_table_doc(doc_title, headers, rows or [], header_row=header_row, start_col=start_col)
    content = json.dumps(doc, indent=2)
    return _create_serialized_file(folder_path, name, 'koresheet', content, {'title': doc_title})


@mcp.tool()
def koredocs_find_labelled_cells(
    id: Annotated[int, 'KoreSheet file id.'],
    labels: Annotated[Optional[list[str]], 'Optional list of labels to search for. Omit to list likely label cells across the sheet.'] = None,
    match_mode: Annotated[Literal['contains', 'exact'], 'How labels should be matched.'] = 'contains',
) -> dict:
    """Find labelled cells and return adjacent right/below values for navigation."""
    file, doc = _load_sheet_doc(id)
    matches = _sheet_find_label_matches(doc, labels, match_mode)
    return {
        **_sheet_summary(file, doc, include_cells=False),
        'match_mode': match_mode,
        'matches': matches,
    }


@mcp.tool()
def koredocs_get_named_value(
    id: Annotated[int, 'KoreSheet file id.'],
    label: Annotated[str, 'Label to search for, such as "Interest Rate" or "Starting Balance".'],
    direction: Annotated[Literal['right', 'below'], 'Which neighboring cell should be treated as the value cell.'] = 'right',
) -> dict:
    """Read a value cell next to a labelled cell."""
    file, doc = _load_sheet_doc(id)
    match, target_addr = _sheet_label_target(doc, label, direction=direction)
    return {
        **_sheet_summary(file, doc, include_cells=False),
        'label_match': match,
        'target_addr': target_addr,
        'value': _sheet_cell_scalar(doc['cells'].get(target_addr)),
    }


@mcp.tool()
def koredocs_set_named_value(
    id: Annotated[int, 'KoreSheet file id.'],
    label: Annotated[str, 'Label to search for, such as "Interest Rate" or "Starting Balance".'],
    value: Annotated[Any, 'New value to write into the neighboring value cell.'],
    direction: Annotated[Literal['right', 'below'], 'Which neighboring cell should be treated as the value cell.'] = 'right',
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Write a value cell next to a labelled cell."""
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


@mcp.tool()
def koredocs_create_compounding_schedule(
    folder_path: Annotated[str, 'Folder path in KoreFile, such as "/Projects/Calcs". Missing folders are created.'],
    name: Annotated[str, 'Filename, with or without the .koresheet extension.'],
    principal: Annotated[float, 'Starting balance.'],
    annual_rate: Annotated[float, 'Annual interest rate as a decimal, e.g. 0.05 for 5%.'],
    years: Annotated[int, 'Number of years to model.'],
    title: Annotated[Optional[str], 'Optional sheet title. Defaults to the filename stem.'] = None,
) -> dict:
    """Create a compound-interest spreadsheet in KoreFile.

    Use this for prompts that ask for a compound interest calculator,
    compounding model, investment growth schedule, or savings projection.
    It creates the folder if needed, creates a .koresheet file, writes labelled
    input cells for Starting Balance and Annual Rate, and fills yearly formula
    rows with Opening Balance, Interest, and Ending Balance.
    """
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


@mcp.tool()
def koredocs_read_sheet_range(
    id: Annotated[int, 'KoreSheet file id.'],
    range: Annotated[str, 'A1-style range such as A1:C10, A:A, or 2:4.'],
    values_only: Annotated[bool, 'When true, return scalar values instead of full cell objects.'] = False,
) -> dict:
    """Canonical prefixed alias for read_sheet_range."""
    return read_sheet_range(id=id, range=range, values_only=values_only)


@mcp.tool()
def koredocs_write_sheet_cells(
    id: Annotated[int, 'KoreSheet file id.'],
    cells: Annotated[dict[str, Any], 'Sparse cell updates keyed by A1 address.'],
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Canonical prefixed alias for write_sheet_cells."""
    return write_sheet_cells(id=id, cells=cells, expected_revision=expected_revision)


@mcp.tool()
def koredocs_read_sheet_table(
    id: Annotated[int, 'KoreSheet file id.'],
    header_row: Annotated[int, 'Row number containing column headers.'] = 1,
    range_ref: Annotated[Optional[str], 'Optional A1-style range constraining the table view.'] = None,
) -> dict:
    """Canonical prefixed alias for read_sheet_table."""
    return read_sheet_table(id=id, header_row=header_row, range_ref=range_ref)


@mcp.tool()
def koredocs_append_sheet_rows(
    id: Annotated[int, 'KoreSheet file id.'],
    rows: Annotated[list[Any], 'Rows to append.'],
    start_col: Annotated[str, 'Start column for list-style rows. Defaults to A.'] = 'A',
    header_row: Annotated[Optional[int], 'Header row to use when rows are objects keyed by column names.'] = None,
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Canonical prefixed alias for append_sheet_rows."""
    return append_sheet_rows(id=id, rows=rows, start_col=start_col, header_row=header_row, expected_revision=expected_revision)


@mcp.tool()
def koredocs_upsert_sheet_rows(
    id: Annotated[int, 'KoreSheet file id.'],
    rows: Annotated[list[dict[str, Any]], 'Header-keyed rows to update or append.'],
    key_columns: Annotated[list[str], 'Header names that uniquely identify an existing row.'],
    header_row: Annotated[int, 'Row number containing column headers.'] = 1,
    create_missing_columns: Annotated[bool, 'When true, create any missing header columns before writing.'] = False,
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Canonical prefixed alias for upsert_sheet_rows."""
    return upsert_sheet_rows(
        id=id,
        rows=rows,
        key_columns=key_columns,
        header_row=header_row,
        create_missing_columns=create_missing_columns,
        expected_revision=expected_revision,
    )


@mcp.tool()
def koredocs_clear_sheet_range(
    id: Annotated[int, 'KoreSheet file id.'],
    range: Annotated[str, 'A1-style range such as B2:D9, A:A, or 3:3.'],
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check.'] = None,
) -> dict:
    """Canonical prefixed alias for clear_sheet_range."""
    return clear_sheet_range(id=id, range=range, expected_revision=expected_revision)


@mcp.tool()
def koredocs_create_koresheet(
    folder_path: Annotated[str, 'Folder path in KoreFile, such as "/" or "/Projects". Missing folders are created.'],
    name: Annotated[str, 'Filename, with or without the .koresheet extension.'],
    cells: Annotated[Optional[dict], 'Sparse cell map keyed by A1 address.'] = None,
    title: Annotated[Optional[str], 'Sheet title.'] = None,
    cols: Annotated[int, 'Number of columns. Defaults to 26.'] = 26,
    rows: Annotated[int, 'Number of rows. Defaults to 100.'] = 100,
) -> dict:
    """Canonical prefixed alias for create_koresheet."""
    return create_koresheet(folder_path=folder_path, name=name, cells=cells, title=title, cols=cols, rows=rows)
