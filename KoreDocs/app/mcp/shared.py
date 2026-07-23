# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared helpers for KoreDocs MCP modules.
#
# Provides folder/file utilities, name validation, and the koredoc parser helpers
# used by koredoc_mcp.py, koresheet_mcp.py, and korediag_mcp.py.
#
# Key helpers:
#   _normalise_folder_path()         -- normalize and validate folder path strings
#   _folder_tree()                   -- recursive folder listing
#   _folder_tree_with_files()        -- folder tree including file entries
#   _folder_id_for_path()            -- resolve folder path to DB id
#   _file_summary()                  -- compact single-file metadata dict
#   _create_serialized_file()        -- create a new file with serialized content
#   _ensure_extension()              -- append file extension if absent
#   _koredoc_parse / _koredoc_find_heading / etc.  -- heading-level editing helpers
#
# Related modules:
#   - app/_mcp_instance.py   -- mcp singleton re-exported from here
#   - app/korefile.py        -- low-level virtual FS operations
#   - app/koredoc_mcp.py     -- uses koredoc parser helpers
#   - app/koresheet_mcp.py   -- uses _create_serialized_file, _ensure_extension
#   - app/korediag_mcp.py      -- uses _create_serialized_file, _ensure_extension
# ====================================================================================================

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from ..documents.korefile import service as korefile
from .instance import mcp  # re-export for sub-modules


# ── Constants ──────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = frozenset({'koredoc', 'koresheet', 'korediag'})

_KOREDOC_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*$")


# ── Folder helpers ─────────────────────────────────────────────────────────

def _normalise_folder_path(folder_path: str | None) -> str:
    if not folder_path:
        return '/'
    path = folder_path.strip().replace('\\', '/')
    if not path.startswith('/'):
        path = '/' + path
    parts = [p for p in path.split('/') if p]
    if any(p in ('.', '..') for p in parts):
        raise ValueError('folder_path must not contain . or .. segments')
    if parts and parts[0].lower() == 'koredocs':
        parts = parts[1:]
    return '/' + '/'.join(parts) if parts else '/'


def _folder_tree() -> list[dict]:
    folders = korefile.list_folders()
    by_id = {f['id']: {**f, 'children': []} for f in folders}
    roots: list[dict] = []
    for folder in by_id.values():
        parent_id = folder['parent_id']
        if parent_id is None:
            roots.append(folder)
        elif parent_id in by_id:
            by_id[parent_id]['children'].append(folder)
    for folder in by_id.values():
        folder['children'].sort(key=lambda f: f['name'].lower())
    roots.sort(key=lambda f: f['name'].lower())
    return roots


def _folder_tree_with_files() -> list[dict]:
    roots = _folder_tree()

    def attach_files(folder: dict) -> None:
        folder['files'] = korefile.list_files(folder_id=folder['id'])
        for child in folder['children']:
            attach_files(child)

    for root in roots:
        attach_files(root)
    return roots


def _folder_id_for_path(folder_path: str, *, create: bool = False) -> int:
    path = _normalise_folder_path(folder_path)
    folder = korefile.get_folder_by_path(path)
    if folder:
        return folder['id']
    if not create:
        raise ValueError(f'Folder not found: {path}')
    parent_id = 1
    current_path = '/'
    for part in [p for p in path.split('/') if p]:
        current_path = current_path.rstrip('/') + '/' + part
        folder = korefile.get_folder_by_path(current_path)
        if folder:
            parent_id = folder['id']
            continue
        folder = korefile.create_folder(part, parent_id)
        parent_id = folder['id']
    return parent_id


# ── File helpers ───────────────────────────────────────────────────────────

def _validate_name(name: str) -> None:
    if not name or any(c in name for c in ('/', '\\', ':')) or '..' in name.split('.'):
        raise ValueError('name must be a simple filename')
    ext = name.rsplit('.', 1)[-1] if '.' in name else ''
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ', '.join('.' + e for e in sorted(ALLOWED_EXTENSIONS))
        raise ValueError(f'Unsupported file type .{ext}; allowed: {allowed}')


def _ensure_extension(name: str, ext: str) -> str:
    return name if name.endswith(f'.{ext}') else f'{name}.{ext}'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _create_serialized_file(
    folder_path: str,
    name: str,
    ext: str,
    content: str,
    metadata: Optional[dict] = None,
) -> dict:
    name = _ensure_extension(name, ext)
    _validate_name(name)
    return korefile.create_serialized_file(folder_path, name, ext, content, metadata)


def _file_summary(file: dict) -> dict:
    return {
        'id': file['id'],
        'name': file['name'],
        'revision': file.get('revision', 1),
        'created_at': file.get('created_at'),
        'modified_at': file.get('modified_at'),
    }


# ── KoreDoc helpers ────────────────────────────────────────────────────────

def _koredoc_file(file_id: int) -> dict:
    file = korefile.get_file(file_id, include_content=True)
    if file is None:
        raise ValueError(f'File not found: {file_id}')
    if file.get('ext') != 'koredoc':
        raise ValueError(f'File {file_id} is not a .koredoc document')
    return file


def _koredoc_split_frontmatter(content: str) -> dict:
    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != '---':
        return {'frontmatter': '', 'frontmatter_end_line': 0, 'body_start_line': 1}
    for index in range(1, len(lines)):
        if lines[index].strip() == '---':
            return {
                'frontmatter': ''.join(lines[: index + 1]),
                'frontmatter_end_line': index + 1,
                'body_start_line': index + 2,
            }
    return {'frontmatter': '', 'frontmatter_end_line': 0, 'body_start_line': 1}


def _koredoc_parse(content: str) -> dict:
    lines = content.splitlines(keepends=True)
    frontmatter = _koredoc_split_frontmatter(content)
    headings: list[dict] = []
    stack: list[dict] = []

    for line_index in range(frontmatter['body_start_line'] - 1, len(lines)):
        text = lines[line_index].rstrip('\r\n')
        match = _KOREDOC_HEADING_RE.match(text)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        while stack and stack[-1]['level'] >= level:
            stack.pop()
        path = [entry['title'] for entry in stack] + [title]
        heading = {
            'level': level,
            'title': title,
            'path': path,
            'line_start': line_index + 1,
            'content_start_line': line_index + 2,
        }
        headings.append(heading)
        stack.append({'level': level, 'title': title})

    for index, heading in enumerate(headings):
        line_end = len(lines)
        for follower in headings[index + 1 :]:
            if follower['level'] <= heading['level']:
                line_end = follower['line_start'] - 1
                break
        heading['line_end'] = line_end

    return {
        'lines': lines,
        'frontmatter': frontmatter,
        'headings': headings,
        'line_count': len(lines),
    }


def _koredoc_path_label(path: list[str]) -> str:
    return ' > '.join(path)


def _koredoc_find_heading(parsed: dict, heading_path: list[str]) -> dict:
    normalized = [part.strip() for part in heading_path if isinstance(part, str) and part.strip()]
    if not normalized:
        raise ValueError('heading_path must contain at least one heading')
    for heading in parsed['headings']:
        if heading['path'] == normalized:
            return heading
    raise ValueError(f'Heading not found: {_koredoc_path_label(normalized)}')


def _koredoc_extract_lines(parsed: dict, start_line: int, end_line: int) -> str:
    if start_line < 1 or end_line < start_line or end_line > parsed['line_count']:
        raise ValueError(f'Invalid line range {start_line}:{end_line}')
    return ''.join(parsed['lines'][start_line - 1 : end_line])


def _koredoc_normalize_block(markdown: str) -> str:
    block = (markdown or '').replace('\r\n', '\n')
    if block and not block.endswith('\n'):
        block += '\n'
    return block


def _koredoc_splice(content: str, start_line: int, end_line: int, markdown: str) -> str:
    parsed = _koredoc_parse(content)
    if start_line < 1 or end_line < start_line - 1 or end_line > parsed['line_count']:
        raise ValueError(f'Invalid splice range {start_line}:{end_line}')
    before = ''.join(parsed['lines'][: start_line - 1])
    after = ''.join(parsed['lines'][end_line:])
    block = _koredoc_normalize_block(markdown)
    if before and block and not before.endswith(('\n', '\r')):
        before += '\n'
    return before + block + after
