from __future__ import annotations

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Query

from .models import PythonFunctionInsertBody
from .models import PythonFunctionReplaceBody
from .models import WriteBody
from .workspace_service import WorkspaceService
from .workspace_service import content_hash
from .workspace_service import ensure_expected_hash
from .workspace_service import find_python_function
from .workspace_service import insert_python_function
from .workspace_service import is_probably_text
from .workspace_service import line_indent
from .workspace_service import normalise_insert_source
from .workspace_service import python_function_summary
from .workspace_service import read_text
from .workspace_service import replace_line_range
from .workspace_service import validate_python_content
from .workspace_service import write_text_file


def register_workspace_routes(app: FastAPI, workspace: WorkspaceService) -> dict[str, object]:
    @app.get('/api/root-browse')
    def api_root_browse(path: str | None = Query(default=None)):
        return workspace.list_browse_directories(path)

    @app.get('/api/tree')
    def api_tree(path: str = Query(default='')):
        return workspace.list_directory(path)

    @app.get('/api/file')
    def api_read_file(path: str = Query(...)):
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

    @app.put('/api/file')
    def api_write_file(body: WriteBody, path: str = Query(...)):
        candidate = workspace.resolve_relative_path(path)
        if not candidate.exists():
            raise HTTPException(status_code=404, detail='File not found')
        if not candidate.is_file():
            raise HTTPException(status_code=400, detail='Path is not a file')
        if not is_probably_text(candidate):
            raise HTTPException(status_code=415, detail='Binary files are not supported')

        existing_content, _ = read_text(candidate)
        current_hash        = content_hash(existing_content)
        stat_before         = candidate.stat()
        expected_hash       = str(body.expected_hash or '').strip()
        if expected_hash:
            if current_hash != expected_hash:
                raise HTTPException(status_code=409, detail='File changed on disk (content hash mismatch)')
        else:
            if body.expected_modified_at is not None and int(stat_before.st_mtime) != int(body.expected_modified_at):
                raise HTTPException(status_code=409, detail='File changed on disk (modified_at mismatch)')
            if body.expected_modified_at_ns is not None and int(stat_before.st_mtime_ns) != int(body.expected_modified_at_ns):
                raise HTTPException(status_code=409, detail='File changed on disk (modified_at_ns mismatch)')
        return write_text_file(workspace, candidate, body.content)

    @app.get('/api/context')
    def api_context(
        path: str = Query(...),
        start_line: int | None = Query(default=None, ge=1),
        end_line: int | None = Query(default=None, ge=1),
        query: str | None = Query(default=None),
        include_workspace: bool = Query(default=False),
    ):
        candidate = workspace.resolve_relative_path(path)
        if not candidate.exists():
            raise HTTPException(status_code=404, detail='File not found')
        if not candidate.is_file():
            raise HTTPException(status_code=400, detail='Path is not a file')
        if not is_probably_text(candidate):
            raise HTTPException(status_code=415, detail='Binary files are not supported')
        from .workspace_service import build_context_pack

        return build_context_pack(workspace, candidate, start_line, end_line, query=query, include_workspace=include_workspace)

    @app.get('/api/python-function')
    def api_python_function(path: str = Query(...), symbol: str = Query(...)):
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

    @app.put('/api/python-function')
    def api_replace_python_function(body: PythonFunctionReplaceBody):
        candidate = workspace.resolve_relative_path(body.path)
        if not candidate.exists():
            raise HTTPException(status_code=404, detail='File not found')
        if not candidate.is_file():
            raise HTTPException(status_code=400, detail='Path is not a file')
        if candidate.suffix.lower() not in {'.py', '.pyi'}:
            raise HTTPException(status_code=400, detail='Python function tools require a .py or .pyi file')
        content, lines, entry = find_python_function(candidate, body.symbol)
        ensure_expected_hash(content, body.expected_hash)

        original_line = lines[entry['start_line'] - 1] if entry['start_line'] - 1 < len(lines) else ''
        replacement   = normalise_insert_source(body.replacement, line_indent(original_line))
        merged        = replace_line_range(lines, entry['start_line'], entry['end_line'], replacement)
        validate_python_content(candidate, merged)
        payload       = write_text_file(workspace, candidate, merged)
        payload.update({
            'symbol':     entry['symbol'],
            'kind':       entry['kind'],
            'container':  entry.get('container'),
            'start_line': entry['start_line'],
            'end_line':   entry['end_line'],
        })
        return payload

    @app.post('/api/python-function')
    def api_insert_python_function(body: PythonFunctionInsertBody):
        candidate = workspace.resolve_relative_path(body.path)
        if not candidate.exists():
            raise HTTPException(status_code=404, detail='File not found')
        if not candidate.is_file():
            raise HTTPException(status_code=400, detail='Path is not a file')
        if candidate.suffix.lower() not in {'.py', '.pyi'}:
            raise HTTPException(status_code=400, detail='Python function tools require a .py or .pyi file')
        existing_content, _encoding = read_text(candidate)
        ensure_expected_hash(existing_content, body.expected_hash)
        return insert_python_function(workspace, candidate, body.source, body.after_symbol, body.into_class)

    @app.post('/api/file')
    def api_create_file(path: str = Query(...)):
        candidate = workspace.resolve_relative_path(path)
        if candidate.exists():
            raise HTTPException(status_code=409, detail='File already exists')
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text('', encoding='utf-8')
        stat = candidate.stat()
        return {'ok': True, 'path': workspace.to_posix(candidate), 'size': stat.st_size}

    @app.delete('/api/file')
    def api_delete_file(path: str = Query(...)):
        candidate = workspace.resolve_relative_path(path)
        if not candidate.exists():
            raise HTTPException(status_code=404, detail='File not found')
        if not candidate.is_file():
            raise HTTPException(status_code=400, detail='Path is not a file')
        candidate.unlink()
        return {'ok': True, 'path': workspace.to_posix(candidate)}

    @app.post('/api/dir')
    def api_create_dir(path: str = Query(...)):
        candidate = workspace.resolve_relative_path(path)
        if candidate.exists():
            raise HTTPException(status_code=409, detail='Directory already exists')
        candidate.mkdir(parents=True, exist_ok=False)
        return {'ok': True, 'path': workspace.to_posix(candidate)}

    @app.delete('/api/dir')
    def api_delete_dir(path: str = Query(...)):
        candidate = workspace.resolve_relative_path(path)
        if not candidate.exists():
            raise HTTPException(status_code=404, detail='Directory not found')
        if not candidate.is_dir():
            raise HTTPException(status_code=400, detail='Path is not a directory')
        try:
            candidate.rmdir()
        except OSError as exc:
            raise HTTPException(status_code=409, detail='Directory is not empty') from exc
        return {'ok': True, 'path': workspace.to_posix(candidate)}

    return {
        'api_root_browse':              api_root_browse,
        'api_tree':                     api_tree,
        'api_read_file':                api_read_file,
        'api_write_file':               api_write_file,
        'api_context':                  api_context,
        'api_python_function':          api_python_function,
        'api_replace_python_function':  api_replace_python_function,
        'api_insert_python_function':   api_insert_python_function,
        'api_create_file':              api_create_file,
        'api_delete_file':              api_delete_file,
        'api_create_dir':               api_create_dir,
        'api_delete_dir':               api_delete_dir,
    }
