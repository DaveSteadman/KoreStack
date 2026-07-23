from __future__ import annotations

import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Callable

from fastapi import HTTPException

from .ui_state_store import get_active_workspace_root
from .ui_state_store import set_active_workspace_root
from .workspace_index import get_symbol_by_qualname
from .workspace_index import list_indexed_symbols
from .workspace_menu import read_workspace_menu


IGNORED_DIRS = {
    '.git',
    '.pytest_cache',
    '.mypy_cache',
    '.ruff_cache',
    '.venv',
    '__pycache__',
    'node_modules',
}
TEXT_EXTENSIONS = {
    '.bat',
    '.cfg',
    '.css',
    '.csv',
    '.html',
    '.ini',
    '.js',
    '.json',
    '.md',
    '.ps1',
    '.py',
    '.pyi',
    '.sql',
    '.toml',
    '.txt',
    '.xml',
    '.yaml',
    '.yml',
}
MAX_READ_BYTES = 1_500_000
MAX_WORKSPACE_PATTERN_HITS = 18


def _initial_workspace_root(suite_root: Path) -> Path:
    saved_root = get_active_workspace_root()
    if saved_root:
        try:
            candidate = Path(saved_root).expanduser().resolve()
        except OSError:
            candidate = suite_root
        if candidate.is_dir():
            return candidate
    return suite_root


class WorkspaceService:
    def __init__(self, suite_root: Path) -> None:
        self.suite_root          = suite_root.resolve()
        self.active_root         = _initial_workspace_root(self.suite_root)
        self.active_root_getter: Callable[[], Path] | None = None

    def workspace_root(self) -> Path:
        if self.active_root_getter is not None:
            try:
                external_root = Path(self.active_root_getter()).resolve()
            except (OSError, TypeError, ValueError):
                external_root = self.active_root
            if external_root != self.active_root:
                self.active_root = external_root
        return self.active_root

    def iter_default_roots(self) -> list[Path]:
        options: list[Path] = [self.suite_root]
        try:
            children = sorted(self.suite_root.iterdir(), key=lambda item: item.name.lower())
        except OSError:
            children = []
        for child in children:
            if not child.is_dir() or child.name in IGNORED_DIRS:
                continue
            options.append(child.resolve())

        extra_roots = os.environ.get('KORECODE_EXTRA_ROOTS', '').strip()
        if extra_roots:
            for chunk in extra_roots.split(';'):
                raw = chunk.strip()
                if not raw:
                    continue
                try:
                    candidate = Path(raw).expanduser().resolve()
                except OSError:
                    continue
                if candidate.is_dir() and candidate not in options:
                    options.append(candidate)
        return options

    def root_label_for(self, path: Path) -> str:
        try:
            rel = path.relative_to(self.suite_root).as_posix()
            return self.suite_root.name if not rel else rel
        except ValueError:
            return str(path)

    def normalize_requested_root(self, value: str) -> Path:
        raw = (value or '').strip()
        if not raw:
            return self.suite_root
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (self.suite_root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        return candidate

    def root_options_payload(self) -> dict:
        options = []
        current = self.workspace_root()
        all_roots = self.iter_default_roots()
        if current not in all_roots:
            all_roots.append(current)
        for root in all_roots:
            options.append({'value': str(root), 'label': self.root_label_for(root), 'path': str(root)})
        return {
            'current':      str(current),
            'current_path': str(current),
            'options':      options,
        }

    def set_workspace_root(self, value: str) -> Path:
        candidate = self.normalize_requested_root(value)
        if not candidate.exists():
            raise HTTPException(status_code=404, detail='Root folder not found')
        if not candidate.is_dir():
            raise HTTPException(status_code=400, detail='Root must be a directory')
        self.active_root = candidate
        set_active_workspace_root(self.active_root)
        return self.active_root

    def list_browse_directories(self, path: str | None = None) -> dict:
        raw = (path or '').strip()
        if os.name == 'nt' and not raw:
            directories = []
            for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
                drive = Path(f'{letter}:/')
                if drive.exists():
                    directories.append({'name': f'{letter}:', 'path': str(drive)})
            return {'path': '', 'parent': None, 'directories': directories}

        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        else:
            candidate = candidate.resolve()

        if not candidate.exists():
            raise HTTPException(status_code=404, detail='Browse path not found')
        if not candidate.is_dir():
            raise HTTPException(status_code=400, detail='Browse path is not a directory')

        try:
            children = sorted(candidate.iterdir(), key=lambda item: item.name.lower())
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f'Unable to read browse path: {exc}') from exc

        directories = []
        for child in children:
            if not child.is_dir() or child.name in IGNORED_DIRS:
                continue
            directories.append({'name': child.name, 'path': str(child.resolve())})
            if len(directories) >= 500:
                break

        parent = str(candidate.parent) if candidate.parent != candidate else None
        return {'path': str(candidate), 'parent': parent, 'directories': directories}

    def resolve_relative_path(self, value: str) -> Path:
        root = self.workspace_root()
        candidate = (root / value).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail='Path escapes the workspace root') from exc
        return candidate

    def to_posix(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace_root()).as_posix()
        except ValueError:
            return path.as_posix()

    def list_directory(self, rel_path: str) -> dict:
        root = self.resolve_relative_path(rel_path or '.')
        if not root.exists():
            raise HTTPException(status_code=404, detail='Path not found')
        if not root.is_dir():
            raise HTTPException(status_code=400, detail='Path is not a directory')

        directories = []
        files = []
        try:
            children = sorted(root.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f'Unable to list directory: {exc}') from exc

        for child in children:
            if child.name in IGNORED_DIRS:
                continue
            rel_child = self.to_posix(child)
            stat = child.stat()
            item = {
                'path':        rel_child,
                'name':        child.name,
                'size':        stat.st_size,
                'modified_at': int(stat.st_mtime),
            }
            if child.is_dir():
                directories.append(item)
            else:
                item['text'] = is_probably_text(child)
                files.append(item)

        return {
            'root':        str(self.workspace_root()),
            'path':        self.to_posix(root),
            'name':        root.name if rel_path else self.workspace_root().name,
            'directories': directories,
            'files':       files,
        }


def is_probably_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    try:
        with path.open('rb') as handle:
            chunk = handle.read(4096)
    except OSError:
        return False
    if b'\x00' in chunk:
        return False
    try:
        chunk.decode('utf-8')
        return True
    except UnicodeDecodeError:
        return False


def read_text(path: Path) -> tuple[str, str]:
    if path.stat().st_size > MAX_READ_BYTES:
        raise HTTPException(status_code=413, detail=f'File too large to read (>{MAX_READ_BYTES} bytes)')
    for encoding in ('utf-8', 'utf-8-sig', 'cp1252'):
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=415, detail='Unable to decode file as text')


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def ensure_expected_hash(current_content: str, expected_hash: str) -> None:
    current_hash = content_hash(current_content)
    if current_hash != str(expected_hash or '').strip():
        raise HTTPException(status_code=409, detail='File changed on disk (content hash mismatch)')


def line_window(lines: list[str], start_line: int, end_line: int, pad: int) -> dict:
    total = len(lines)
    start = max(1, start_line - pad)
    end = min(total, end_line + pad)
    numbered = [
        {'line': idx, 'text': lines[idx - 1]}
        for idx in range(start, end + 1)
    ]
    return {'start_line': start, 'end_line': end, 'lines': numbered}


def python_symbol_context(path: Path, content: str, start_line: int, end_line: int) -> dict:
    if path.suffix.lower() not in {'.py', '.pyi'}:
        return {}
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return {}

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        node_start = int(getattr(node, 'lineno', 0) or 0)
        node_end = int(getattr(node, 'end_lineno', node_start) or node_start)
        if node_start <= end_line and node_end >= start_line:
            hits.append({
                'name':       node.name,
                'kind':       'class' if isinstance(node, ast.ClassDef) else 'function',
                'start_line': node_start,
                'end_line':   node_end,
            })
    hits.sort(key=lambda item: (item['start_line'], item['end_line']))
    return {'symbols': hits}


def iter_python_function_symbols(tree: ast.AST) -> list[dict]:
    symbols: list[dict] = []

    def visit_body(body: list[ast.stmt], container: str | None = None) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                symbols.append({
                    'symbol':     node.name,
                    'kind':       'class',
                    'container':  container,
                    'start_line': int(node.lineno),
                    'end_line':   int(getattr(node, 'end_lineno', node.lineno)),
                })
                visit_body(node.body, node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f'{container}.{node.name}' if container else node.name
                symbols.append({
                    'symbol':     qualname,
                    'name':       node.name,
                    'kind':       'function',
                    'container':  container,
                    'start_line': int(node.lineno),
                    'end_line':   int(getattr(node, 'end_lineno', node.lineno)),
                    'async':      isinstance(node, ast.AsyncFunctionDef),
                    'args':       [arg.arg for arg in node.args.args],
                })

    visit_body(getattr(tree, 'body', []))
    return symbols


def parse_python_file(path: Path) -> tuple[str, list[str], ast.AST]:
    content, _encoding = read_text(path)
    try:
        tree = ast.parse(content)
    except SyntaxError as exc:
        raise HTTPException(status_code=400, detail=f'Python parse failed: {exc}') from exc
    return content, content.splitlines(keepends=True), tree


def find_python_function(path: Path, symbol: str) -> tuple[str, list[str], dict]:
    content, lines, tree = parse_python_file(path)
    symbols = iter_python_function_symbols(tree)
    entry = next((item for item in symbols if item.get('symbol') == symbol), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f'Python symbol not found: {symbol}')
    return content, lines, entry


def source_slice(lines: list[str], start_line: int, end_line: int) -> str:
    return '\n'.join(lines[start_line - 1:end_line])


def line_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(' \t'))]


def normalise_insert_source(source: str, base_indent: str, *, indent_first_line: bool = False) -> str:
    text = textwrap.dedent(str(source or '')).strip('\n')
    if not text:
        return ''
    lines = text.splitlines()
    result = []
    for index, line in enumerate(lines):
        if index == 0 and not indent_first_line:
            result.append(line)
        else:
            result.append(base_indent + line if line.strip() else '')
    return '\n'.join(result) + '\n'


def replace_line_range(lines: list[str], start_line: int, end_line: int, replacement_text: str) -> str:
    before = lines[:start_line - 1]
    after = lines[end_line:]
    replacement_lines = replacement_text.rstrip('\n').splitlines()
    return '\n'.join(before + replacement_lines + after) + '\n'


def write_text_file(workspace: WorkspaceService, path: Path, content: str) -> dict:
    path.write_text(content, encoding='utf-8', newline='')
    stat = path.stat()
    return {
        'ok':             True,
        'path':           workspace.to_posix(path),
        'size':           stat.st_size,
        'modified_at':    int(stat.st_mtime),
        'modified_at_ns': int(stat.st_mtime_ns),
        'content_hash':   content_hash(content),
    }


def validate_python_content(path: Path, content: str) -> None:
    if path.suffix.lower() not in {'.py', '.pyi'}:
        return
    try:
        ast.parse(content)
    except SyntaxError as exc:
        raise HTTPException(status_code=400, detail=f'Python validation failed: {exc}') from exc


def run_python_tool(workspace: WorkspaceService, path: str, mode: str, timeout_seconds: int | None) -> dict:
    candidate = workspace.resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail='Path is not a file')
    if candidate.suffix.lower() not in {'.py', '.pyi'}:
        raise HTTPException(status_code=400, detail='Python execution requires a .py or .pyi file')

    normalized_mode = str(mode or '').strip().lower()
    if normalized_mode not in {'check', 'run'}:
        raise HTTPException(status_code=400, detail=f'Unsupported Python execution mode: {mode}')

    timeout = 15 if timeout_seconds is None else max(1, min(30, int(timeout_seconds)))
    command = [sys.executable, '-m', 'py_compile', str(candidate)] if normalized_mode == 'check' else [sys.executable, str(candidate)]
    try:
        proc = subprocess.run(
            command,
            cwd             = str(workspace.workspace_root()),
            text            = True,
            capture_output  = True,
            timeout         = timeout,
            check           = False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            'path':            workspace.to_posix(candidate),
            'mode':            normalized_mode,
            'exit_code':       None,
            'ok':              False,
            'timed_out':       True,
            'timeout_seconds': timeout,
            'stdout':          str(exc.stdout or '')[:12000],
            'stderr':          str(exc.stderr or '')[:12000],
        }
    stdout = str(proc.stdout or '')[:12000]
    stderr = str(proc.stderr or '')[:12000]
    return {
        'path':             workspace.to_posix(candidate),
        'mode':             normalized_mode,
        'command':          command[:3] if normalized_mode == 'check' else command[:2],
        'exit_code':        int(proc.returncode),
        'ok':               proc.returncode == 0,
        'stdout':           stdout,
        'stderr':           stderr,
        'output_truncated': len(str(proc.stdout or '')) > len(stdout) or len(str(proc.stderr or '')) > len(stderr),
    }


def python_function_summary(entry: dict, lines: list[str]) -> dict:
    start_line = int(entry['start_line'])
    end_line = int(entry['end_line'])
    return {
        'symbol':      entry['symbol'],
        'name':        entry.get('name') or entry['symbol'],
        'kind':        entry['kind'],
        'container':   entry.get('container'),
        'start_line':  start_line,
        'end_line':    end_line,
        'source':      source_slice(lines, start_line, end_line),
        'context':     line_window(lines, start_line, end_line, pad=4),
    }


def insert_python_function(
    workspace: WorkspaceService,
    path: Path,
    source: str,
    after_symbol: str | None,
    into_class: str | None,
) -> dict:
    _content, lines, tree = parse_python_file(path)
    symbols = iter_python_function_symbols(tree)
    target_class_name = str(into_class or '').strip() or None
    anchor_symbol = str(after_symbol or '').strip() or None

    insert_line = len(lines) + 1
    insert_indent = ''
    inserted_into = None
    insert_after_index = False
    indent_first_line = False

    if target_class_name:
        class_node = next(
            (node for node in getattr(tree, 'body', []) if isinstance(node, ast.ClassDef) and node.name == target_class_name),
            None,
        )
        if class_node is None:
            raise HTTPException(status_code=404, detail=f'Python class not found: {target_class_name}')
        class_line = lines[class_node.lineno - 1] if class_node.lineno - 1 < len(lines) else ''
        insert_indent = line_indent(class_line) + '    '
        insert_line = int(class_node.end_lineno)
        inserted_into = target_class_name
        insert_after_index = True
        indent_first_line = True

    if anchor_symbol:
        anchor = next((entry for entry in symbols if entry['symbol'] == anchor_symbol), None)
        if anchor is None:
            raise HTTPException(status_code=404, detail=f'Anchor function not found: {anchor_symbol}')
        if target_class_name and anchor.get('container') != target_class_name:
            raise HTTPException(status_code=400, detail='after_symbol is not inside into_class')
        if not target_class_name and anchor.get('container'):
            raise HTTPException(status_code=400, detail='Top-level insert cannot anchor after a class method')
        anchor_line = lines[anchor['start_line'] - 1] if anchor['start_line'] - 1 < len(lines) else ''
        insert_indent = line_indent(anchor_line)
        insert_line = int(anchor['end_line'])
        inserted_into = anchor.get('container')
        insert_after_index = True
        indent_first_line = bool(anchor.get('container'))

    new_source = normalise_insert_source(source, insert_indent, indent_first_line=indent_first_line)
    merged_lines = lines[:insert_line] if insert_after_index else lines[:insert_line - 1]

    if merged_lines:
        previous = merged_lines[-1]
        if previous.strip():
            merged_lines.append('\n')
    merged_lines.append(new_source)
    merged_lines.extend(lines[insert_line:] if insert_after_index else lines[insert_line - 1:])

    merged_content = ''.join(merged_lines)
    validate_python_content(path, merged_content)
    payload = write_text_file(workspace, path, merged_content)
    payload.update({
        'inserted_after': anchor_symbol,
        'inserted_into':  inserted_into,
    })
    return payload


def reference_hits(path: Path, token: str) -> list[dict]:
    hits = []
    if not token:
        return hits
    try:
        content, _encoding = read_text(path)
    except Exception:
        return hits
    for idx, line in enumerate(content.splitlines(), start=1):
        if token in line:
            hits.append({'line': idx, 'text': line[:240]})
            if len(hits) >= 20:
                break
    return hits


def workspace_patterns_from_refs(workspace: WorkspaceService, path: Path, refs: list[dict]) -> list[dict]:
    try:
        symbols = list_indexed_symbols(root=workspace.workspace_root(), query=path.stem, limit=MAX_WORKSPACE_PATTERN_HITS)
    except FileNotFoundError:
        symbols = []
    patterns = []
    for symbol in symbols:
        symbol_path = str(symbol.get('path') or '')
        if not symbol_path or symbol_path == workspace.to_posix(path):
            continue
        patterns.append(symbol)
    if refs:
        patterns.append({'references': refs[:MAX_WORKSPACE_PATTERN_HITS]})
    return patterns[:MAX_WORKSPACE_PATTERN_HITS]


def workspace_menu_excerpt(workspace: WorkspaceService, max_chars: int = 24000) -> dict | None:
    menu = read_workspace_menu(workspace.workspace_root())
    if not menu:
        return None
    text = str(menu.get('content') or '')
    if len(text) > max_chars:
        text = text[:max_chars] + '\n...'
    return {'path': menu.get('path'), 'content': text}


def build_context_pack(
    workspace: WorkspaceService,
    path: Path,
    start_line: int | None,
    end_line: int | None,
    query: str | None = None,
    include_workspace: bool = False,
) -> dict:
    content, encoding = read_text(path)
    lines = content.splitlines()
    total_lines = len(lines)
    if start_line is None:
        start_line = 1
    if end_line is None:
        end_line = min(total_lines, start_line + 120)
    start_line = max(1, int(start_line))
    end_line = max(start_line, min(total_lines, int(end_line)))
    window = line_window(lines, start_line, end_line, pad=8)
    token = str(query or path.stem).strip()
    refs = reference_hits(path, token)
    payload = {
        'path':           workspace.to_posix(path),
        'encoding':       encoding,
        'content_hash':   content_hash(content),
        'total_lines':    total_lines,
        'window':         window,
        'symbols':        python_symbol_context(path, content, start_line, end_line),
        'references':     refs,
        'workspace_menu': workspace_menu_excerpt(workspace) if include_workspace else None,
    }
    if include_workspace:
        payload['workspace_patterns'] = workspace_patterns_from_refs(workspace, path, refs)
    return payload


def read_file_payload(workspace: WorkspaceService, path: str) -> dict:
    candidate = workspace.resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail='Path is not a file')
    if not is_probably_text(candidate):
        raise HTTPException(status_code=415, detail='Binary files are not supported')
    content, encoding = read_text(candidate)
    stat = candidate.stat()
    return {
        'path':           workspace.to_posix(candidate),
        'name':           candidate.name,
        'content':        content,
        'encoding':       encoding,
        'size':           stat.st_size,
        'modified_at':    int(stat.st_mtime),
        'modified_at_ns': int(stat.st_mtime_ns),
        'content_hash':   content_hash(content),
    }


def context_payload(workspace: WorkspaceService, path: str, start_line: int | None, end_line: int | None, include_workspace: bool) -> dict:
    candidate = workspace.resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail='Path is not a file')
    if not is_probably_text(candidate):
        raise HTTPException(status_code=415, detail='Binary files are not supported')
    return build_context_pack(workspace, candidate, start_line, end_line, query=None, include_workspace=include_workspace)


def python_function_payload(workspace: WorkspaceService, path: str, symbol: str) -> dict:
    candidate = workspace.resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail='Path is not a file')
    if candidate.suffix.lower() not in {'.py', '.pyi'}:
        raise HTTPException(status_code=400, detail='Python function tools require a .py or .pyi file')
    content, lines, entry = find_python_function(candidate, symbol)
    return {
        'path':         workspace.to_posix(candidate),
        'content_hash': content_hash(content),
        **python_function_summary(entry, lines),
    }
