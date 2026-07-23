"""FastAPI entrypoint for KoreCode."""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import sys
from functools import partial
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.service_app import register_endpoint_manifest
from KoreCommon.service_app import register_suite_config_js

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from .config import cfg as _cfg
from .edit_store import apply_edit_proposal
from .edit_store import create_edit_proposal
from .edit_store import get_edit_proposal
from .agent_playbooks import list_playbooks
from .agent_playbooks import route_task
from .korechat_client import append_internal_followup
from .korechat_client import append_visible_message_for_conversation
from .korechat_client import delete_thread
from .korechat_client import get_thread
from .korechat_client import set_workspace_context_enabled
from .models import ChatFollowupBody
from .models import ChatPromptBuildBody
from .models import ChatRunCreateBody
from .models import ChatSendBody
from .models import ChatToolExecuteBody
from .models import ChatToolFollowupPromptBody
from .models import ChatWorkspaceContextBody
from .models import ContinueRunCreateBody
from .models import EditProposalCreateBody
from .models import PythonExecutionBody
from .models import PythonFunctionInsertBody
from .models import PythonFunctionReplaceBody
from .models import RootBody
from .models import SlashCommandBody
from .models import SlashCommandCompleteBody
from .models import WorkItemCreateBody
from .models import WorkItemUpdateBody
from .prompt_builder import build_prompt_by_mode
from .prompt_builder import build_tool_followup_prompt
from .run_executor import AgentRunServices
from .run_executor import ChatRunRequest
from .run_executor import ContinueRunRequest
from .run_executor import build_continue_prompt
from .run_executor import execute_chat_run
from .run_executor import execute_continue_run
from .run_executor import start_background_run
from .run_store import append_edit_proposal
from .run_store import append_model_response
from .run_store import append_tool_call
from .run_store import create_run
from .run_store import find_latest_run
from .run_store import get_run
from .run_store import list_runs
from .run_store import set_run_output
from .run_store import update_run
from .routes_workspace import register_workspace_routes
from .slash_command_context import KoreCodeSlashCommandContext
from .slash_commands import complete as complete_slash_command
from .slash_commands import handle as handle_slash_command
from .slash_commands import initialize as initialize_slash_commands
from .tool_api import execute_tool_requests
from .tool_api import tool_guide_payload
from .ui_state_store import set_active_workspace_root
from .workspace_artifacts import read_workspace_artifact_status
from .workspace_artifacts import rebuild_workspace_artifacts
from .workspace_index import get_symbol_by_qualname
from .workspace_index import list_indexed_files
from .workspace_index import list_indexed_symbols
from .workspace_index import list_symbol_callees
from .workspace_index import list_symbol_callers
from .workspace_menu import build_workspace_menu
from .workspace_service import WorkspaceService
from .workspace_service import build_context_pack
from .workspace_service import content_hash
from .workspace_service import context_payload
from .workspace_service import ensure_expected_hash
from .workspace_service import find_python_function
from .workspace_service import is_probably_text
from .workspace_service import iter_python_function_symbols
from .workspace_service import parse_python_file
from .workspace_service import python_function_payload
from .workspace_service import python_function_summary
from .workspace_service import read_file_payload
from .workspace_service import read_text
from .workspace_service import run_python_tool
from .workspace_service import validate_python_content
from .work_item_store import attach_run
from .work_item_store import create_work_item
from .work_item_store import get_work_item
from .work_item_store import list_work_items
from .work_item_store import update_work_item


BASE_DIR = Path(__file__).parent.parent.resolve()
STATIC_DIR = Path(
    os.environ.get(
        'KORE_KORECODE_STATIC_DIR',
        str(BASE_DIR.parent / 'KoreUI' / 'KoreCode' / 'static'),
    )
).resolve()
SUITE_ROOT = Path(os.environ.get('KORE_SUITE_ROOT', str(BASE_DIR.parent))).resolve()
COMMONUI_ASSETS = Path(
    os.environ.get(
        'KORE_UIELEMENTS_ASSETS_DIR',
        str(BASE_DIR.parent / 'UIElements' / 'assets'),
    )
).resolve()
LOG = logging.getLogger('korecode')

_WORKSPACE = WorkspaceService(SUITE_ROOT)
_ACTIVE_ROOT = _WORKSPACE.active_root
_WORKSPACE.active_root_getter = lambda: _ACTIVE_ROOT


def _workspace_root() -> Path:
    return _WORKSPACE.workspace_root()


def _root_options_payload() -> dict:
    return _WORKSPACE.root_options_payload()


def _set_workspace_root(value: str) -> Path:
    global _ACTIVE_ROOT
    candidate = _WORKSPACE.normalize_requested_root(value)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='Root folder not found')
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail='Root must be a directory')
    _WORKSPACE.active_root = candidate
    _ACTIVE_ROOT = candidate
    set_active_workspace_root(candidate)
    return _ACTIVE_ROOT


def _resolve_relative_path(value: str) -> Path:
    return _WORKSPACE.resolve_relative_path(value)


def _to_posix(path: Path) -> str:
    return _WORKSPACE.to_posix(path)


def _is_probably_text(path: Path) -> bool:
    return is_probably_text(path)


def _read_text(path: Path) -> tuple[str, str]:
    return read_text(path)


def _content_hash(content: str) -> str:
    return content_hash(content)


def _ensure_expected_hash(current_content: str, expected_hash: str) -> None:
    ensure_expected_hash(current_content, expected_hash)


def _find_python_function(path: Path, symbol: str) -> tuple[str, list[str], dict]:
    return find_python_function(path, symbol)


def _validate_python_content(path: Path, content: str) -> None:
    validate_python_content(path, content)


def _run_python_tool(path: str, mode: str, timeout_seconds: int | None) -> dict:
    return run_python_tool(_WORKSPACE, path, mode, timeout_seconds)


def _python_function_summary(entry: dict, lines: list[str]) -> dict:
    return python_function_summary(entry, lines)


def _build_context_pack(path: Path, start_line: int | None, end_line: int | None, query: str | None = None, include_workspace: bool = False) -> dict:
    return build_context_pack(_WORKSPACE, path, start_line, end_line, query=query, include_workspace=include_workspace)


def _list_directory(rel_path: str) -> dict:
    return _WORKSPACE.list_directory(rel_path)


app = FastAPI(title='KoreCode')
register_endpoint_manifest(app, service_key='korecode', service_label='KoreCode')
register_suite_config_js(app)
initialize_slash_commands(workspace_root_getter=_workspace_root)


class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith('/static/') or request.url.path.startswith('/ui-elements/assets/'):
            response.headers['cache-control'] = 'no-store'
        return response


app.add_middleware(NoCacheMiddleware)

app.mount('/static/code', StaticFiles(directory=STATIC_DIR / 'code'), name='code')
app.mount('/ui-elements/assets', StaticFiles(directory=COMMONUI_ASSETS), name='ui-elements-assets')
app.mount('/static/commonui', StaticFiles(directory=COMMONUI_ASSETS), name='commonui')
globals().update(register_workspace_routes(app, _WORKSPACE))


@app.get('/', include_in_schema=False)
def root():
    return RedirectResponse('/ui')


@app.get('/ui', include_in_schema=False)
def serve_ui():
    return FileResponse(STATIC_DIR / 'code' / 'index.html')


@app.get('/code', include_in_schema=False)
def serve_code_alias():
    """Legacy alias — kept for existing bookmarks."""
    return RedirectResponse('/ui')


@app.get('/status')
def status():
    return {
        'status': 'ok',
        'service': 'korecode',
        'root': str(_workspace_root()),
        'suite_root': str(SUITE_ROOT),
    }


@app.get('/api/root-options')
def api_root_options():
    return _root_options_payload()


@app.post('/api/root')
def api_set_root(body: RootBody):
    new_root = _set_workspace_root(body.root)
    payload = _root_options_payload()
    payload['ok'] = True
    payload['root'] = str(new_root)
    return payload


@app.post('/api/slash')
def api_slash_command(body: SlashCommandBody):
    messages: list[dict] = []

    def _output(text: str, level: str = "info") -> None:
        messages.append({
            "role":  "assistant",
            "text":  text,
            "level": level,
        })

    ctx = KoreCodeSlashCommandContext(
        output                    = _output,
        current_mode              = body.current_mode,
        workspace_context_enabled = body.workspace_context_enabled,
        thread_path               = body.thread_path,
        has_last_user_message     = body.has_last_user_message,
    )
    handled = handle_slash_command(body.text, ctx)
    return {
        "handled":  handled,
        "messages": messages,
        "actions":  ctx.actions,
    }


@app.post('/api/slash/complete')
def api_slash_command_complete(body: SlashCommandCompleteBody):
    ctx = KoreCodeSlashCommandContext(
        output                    = lambda text, level="info": None,
        current_mode              = body.current_mode,
        workspace_context_enabled = body.workspace_context_enabled,
        thread_path               = body.thread_path,
        has_last_user_message     = body.has_last_user_message,
    )
    return {
        "items": complete_slash_command(
            body.text,
            ctx,
            limit = max(1, min(int(body.limit), 25)),
        ),
    }


@app.post('/api/workspace-menu/rebuild')
def api_workspace_menu_rebuild():
    return rebuild_workspace_artifacts(_workspace_root())


@app.post('/api/workspace-index/rebuild')
def api_workspace_index_rebuild():
    return rebuild_workspace_artifacts(_workspace_root())


@app.get('/api/work-items')
def api_work_items(limit: int = 100):
    return {'work_items': list_work_items(workspace_root=_workspace_root(), limit=limit)}


@app.post('/api/work-items')
def api_create_work_item(body: WorkItemCreateBody):
    try:
        return create_work_item(
            title          = body.title,
            description    = body.description,
            scope          = body.scope,
            constraints    = body.constraints,
            workspace_root = _workspace_root(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get('/api/work-items/{work_item_id}')
def api_get_work_item(work_item_id: str):
    try:
        item = get_work_item(work_item_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='Work item not found') from exc
    if item.get('workspace_root') != str(_workspace_root()):
        raise HTTPException(status_code=404, detail='Work item not found in active workspace')
    return item


@app.patch('/api/work-items/{work_item_id}')
def api_update_work_item(work_item_id: str, body: WorkItemUpdateBody):
    try:
        item = get_work_item(work_item_id)
        if item.get('workspace_root') != str(_workspace_root()):
            raise FileNotFoundError(work_item_id)
        return update_work_item(work_item_id, **body.model_dump())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='Work item not found') from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get('/api/workspace-index/status')
def api_workspace_index_status():
    return read_workspace_artifact_status(_workspace_root())


@app.get('/api/workspace-index/files')
def api_workspace_index_files():
    try:
        files = list_indexed_files(_workspace_root())
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='Workspace index not found') from exc
    return {
        'files': files,
    }


@app.get('/api/workspace-index/symbols')
def api_workspace_index_symbols(
    path: str | None = None,
    query: str | None = None,
    kind: str | None = None,
    limit: int = 200,
):
    try:
        symbols = list_indexed_symbols(
            _workspace_root(),
            path  = path,
            query = query,
            kind  = kind,
            limit = limit,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='Workspace index not found') from exc
    return {
        'symbols': symbols,
    }


@app.get('/api/workspace-index/symbol')
def api_workspace_index_symbol(qualname: str = Query(...)):
    try:
        symbol = get_symbol_by_qualname(_workspace_root(), qualname)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='Workspace index not found') from exc
    if symbol is None:
        raise HTTPException(status_code=404, detail='Symbol not found')
    return symbol


@app.get('/api/workspace-index/callers')
def api_workspace_index_callers(qualname: str = Query(...)):
    try:
        callers = list_symbol_callers(_workspace_root(), qualname)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='Workspace index not found') from exc
    return {
        'qualname': qualname,
        'callers': callers,
    }


@app.get('/api/workspace-index/callees')
def api_workspace_index_callees(qualname: str = Query(...)):
    try:
        callees = list_symbol_callees(_workspace_root(), qualname)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='Workspace index not found') from exc
    return {
        'qualname': qualname,
        'callees': callees,
    }


@app.post('/api/chat/prompt')
def api_chat_prompt(body: ChatPromptBuildBody):
    prompt = build_prompt_by_mode(
        mode                      = body.mode,
        user_text                 = str(body.user_text or ''),
        path                      = body.path,
        selection                 = body.selection,
        cursor                    = body.cursor if isinstance(body.cursor, dict) else None,
        workspace_context_enabled = body.workspace_context_enabled,
        workspace_root            = _workspace_root(),
        resolve_relative_path     = _resolve_relative_path,
        is_probably_text          = _is_probably_text,
        read_text                 = _read_text,
        build_context_pack        = _build_context_pack,
        max_mention_count         = body.max_mention_count,
        max_mention_file_chars    = body.max_mention_file_chars,
    )
    return {'prompt': prompt}


@app.post('/api/chat/tool-followup-prompt')
def api_chat_tool_followup_prompt(body: ChatToolFollowupPromptBody):
    prompt = build_tool_followup_prompt(
        mode              = body.mode,
        path              = body.path,
        user_text         = str(body.user_text or ''),
        previous_response = str(body.previous_response or ''),
        tool_results      = list(body.tool_results or []),
        execution_contract = body.execution_contract if isinstance(body.execution_contract, dict) else None,
    )
    return {'prompt': prompt}


@app.post('/api/chat/runs')
def api_chat_runs(body: ChatRunCreateBody):
    return {'run': _start_chat_backend_run(body)}


@app.get('/api/agent/playbooks')
def api_agent_playbooks():
    return {'playbooks': list_playbooks()}


@app.post('/api/chat/continue-runs')
def api_chat_continue_runs(body: ContinueRunCreateBody):
    return {'run': _start_continue_backend_run(body)}


@app.get('/api/chat/tools')
def api_chat_tools():
    return {'tools': tool_guide_payload()}


@app.post('/api/execution/python')
def api_execution_python(body: PythonExecutionBody):
    try:
        return _run_python_tool(body.path, body.mode, body.timeout_seconds)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/api/edit-proposals')
def api_create_edit_proposal(body: EditProposalCreateBody):
    proposal = create_edit_proposal(
        edits                  = list(body.edits or []),
        workspace_root         = _workspace_root(),
        resolve_relative_path  = _resolve_relative_path,
        is_probably_text       = _is_probably_text,
        read_text              = _read_text,
        validate_python_content = _validate_python_content,
        run_id                 = body.run_id,
        source                 = body.source,
        summary                = body.summary,
    )
    run_id = str(body.run_id or '').strip()
    if run_id:
        append_edit_proposal(
            run_id,
            proposal_id   = proposal['proposal_id'],
            source        = proposal['source'],
            summary       = proposal.get('summary', ''),
            validation_ok = bool(proposal.get('validation_ok')),
            edits         = list(proposal.get('edits') or []),
        )
    return proposal


@app.get('/api/edit-proposals/{proposal_id}')
def api_get_edit_proposal(proposal_id: str):
    try:
        return get_edit_proposal(proposal_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='Edit proposal not found') from exc


@app.post('/api/edit-proposals/{proposal_id}/apply')
def api_apply_edit_proposal(proposal_id: str):
    try:
        proposal = apply_edit_proposal(
            proposal_id,
            resolve_relative_path   = _resolve_relative_path,
            is_probably_text        = _is_probably_text,
            read_text               = _read_text,
            validate_python_content = _validate_python_content,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='Edit proposal not found') from exc
    run_id = str(proposal.get('run_id') or '').strip()
    if run_id:
        update_run(
            run_id,
            event_type    = 'edit_proposal_applied',
            event_payload = proposal.get('apply_result') or {},
        )
    return proposal


def _api_read_file_payload(path: str) -> dict:
    candidate = _resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail='Path is not a file')
    if not _is_probably_text(candidate):
        raise HTTPException(status_code=415, detail='Binary files are not supported')
    content, encoding = _read_text(candidate)
    stat              = candidate.stat()
    content_hash      = _content_hash(content)
    return {
        'path':            _to_posix(candidate),
        'name':            candidate.name,
        'content':         content,
        'encoding':        encoding,
        'size':            stat.st_size,
        'modified_at':     int(stat.st_mtime),
        'modified_at_ns':  int(stat.st_mtime_ns),
        'content_hash':    content_hash,
    }


def _api_context_payload(path: str, start_line: int | None, end_line: int | None, include_workspace: bool) -> dict:
    candidate = _resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail='Path is not a file')
    if not _is_probably_text(candidate):
        raise HTTPException(status_code=415, detail='Binary files are not supported')
    return _build_context_pack(candidate, start_line, end_line, query=None, include_workspace=include_workspace)


def _api_python_function_payload(path: str, symbol: str) -> dict:
    candidate = _resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail='Path is not a file')
    if candidate.suffix.lower() not in {'.py', '.pyi'}:
        raise HTTPException(status_code=400, detail='Python function tools require a .py or .pyi file')
    content, lines, entry = _find_python_function(candidate, symbol)
    return {
        'path':         _to_posix(candidate),
        'content_hash': _content_hash(content),
        **_python_function_summary(entry, lines),
    }


def _replace_python_function_proposal_payload(path: str, symbol: str, replacement: str, expected_hash: str) -> dict:
    current = _api_python_function_payload(path, symbol)
    proposal = create_edit_proposal(
        edits = [
            {
                'file':          path,
                'from':          current['start_line'],
                'to':            current['end_line'],
                'replacement':   replacement,
                'reason':        f"Replace Python function {symbol}",
                'expected_hash': expected_hash,
            }
        ],
        workspace_root          = _workspace_root(),
        resolve_relative_path   = _resolve_relative_path,
        is_probably_text        = _is_probably_text,
        read_text               = _read_text,
        validate_python_content = _validate_python_content,
        source                  = 'tool_api',
        summary                 = f"Replace Python function {symbol}",
    )
    return proposal


def _insert_python_function_proposal_payload(path: str, source: str, expected_hash: str, after_symbol: str | None, into_class: str | None) -> dict:
    candidate = _resolve_relative_path(path)
    if not candidate.exists():
        raise HTTPException(status_code=404, detail='File not found')
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail='Path is not a file')
    if candidate.suffix.lower() not in {'.py', '.pyi'}:
        raise HTTPException(status_code=400, detail='Python function tools require a .py or .pyi file')
    existing_content, _encoding = _read_text(candidate)
    _ensure_expected_hash(existing_content, expected_hash)
    _content, lines, tree = parse_python_file(candidate)
    symbols               = iter_python_function_symbols(tree)

    insert_line = len(lines) + 1
    if into_class:
        class_node = next(
            (node for node in getattr(tree, 'body', []) if isinstance(node, ast.ClassDef) and node.name == into_class),
            None,
        )
        if class_node is None:
            raise HTTPException(status_code=404, detail=f'Python class not found: {into_class}')
        insert_line = int(class_node.end_lineno)
    elif after_symbol:
        anchor = next((entry for entry in symbols if entry['symbol'] == after_symbol), None)
        if anchor is None:
            raise HTTPException(status_code=404, detail=f'Anchor function not found: {after_symbol}')
        insert_line = int(anchor['end_line'])

    proposal = create_edit_proposal(
        edits = [
            {
                'file':          path,
                'from':          insert_line + 1,
                'to':            insert_line,
                'replacement':   source,
                'reason':        f"Insert Python function after {after_symbol or into_class or 'end of file'}",
                'expected_hash': expected_hash,
            }
        ],
        workspace_root          = _workspace_root(),
        resolve_relative_path   = _resolve_relative_path,
        is_probably_text        = _is_probably_text,
        read_text               = _read_text,
        validate_python_content = _validate_python_content,
        source                  = 'tool_api',
        summary                 = f"Insert Python function into {path}",
    )
    return proposal


def _apply_agent_edits(
    *,
    workspace_root: Path,
    run_id: str,
    active_path: str,
    user_text: str,
    edits: list[dict],
    summary: str,
    execution_contract: dict | None = None,
) -> dict:
    active_file   = str(active_path or "").strip()
    allowed_paths = {active_file} if active_file and active_file != "." else set()
    allowed_paths.update(_explicit_file_paths(user_text))
    requested_paths = {str(edit.get("file") or "").strip() for edit in edits}
    requested_paths.discard("")

    def resolve_for_run(path: str) -> Path:
        candidate = (workspace_root / str(path or "")).resolve()
        try:
            candidate.relative_to(workspace_root)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="Path escapes the workspace") from exc
        return candidate

    unexpected_paths = requested_paths - allowed_paths
    allows_new_file  = str((execution_contract or {}).get("id") or "") == "create_file"
    if unexpected_paths and not (
        allows_new_file and all(not resolve_for_run(path).exists() for path in unexpected_paths)
    ):
        unexpected = ", ".join(sorted(unexpected_paths))
        return {
            "validation_ok": False,
            "apply_result": {
                "ok":      False,
                "applied": 0,
                "errors":  [f"Agent proposed edits outside the active or explicitly named files: {unexpected}"],
                "paths":   [],
            },
        }

    proposal = create_edit_proposal(
        edits                  = edits,
        workspace_root         = workspace_root,
        resolve_relative_path  = resolve_for_run,
        is_probably_text       = _is_probably_text,
        read_text              = _read_text,
        validate_python_content = _validate_python_content,
        run_id                 = run_id,
        source                 = "agent",
        summary                = summary,
    )
    if not proposal.get("validation_ok"):
        return proposal
    return apply_edit_proposal(
        proposal["proposal_id"],
        resolve_relative_path   = resolve_for_run,
        is_probably_text        = _is_probably_text,
        read_text               = _read_text,
        validate_python_content = _validate_python_content,
    )


def _explicit_file_paths(text: str) -> set[str]:
    paths: set[str] = set()
    pattern = r"(?<![\w/\\])(?:[\w.-]+[/\\])*[\w-]+\.[A-Za-z0-9]+"
    for match in re.finditer(pattern, str(text or "")):
        path = match.group(0).replace("\\", "/")
        if path.startswith("/") or ".." in path:
            continue
        paths.add(path)
    return paths


def _make_agent_run_services() -> AgentRunServices:
    return AgentRunServices(
        append_visible_message_for_conversation = append_visible_message_for_conversation,
        append_internal_followup                = append_internal_followup,
        get_thread                              = get_thread,
        build_tool_followup_prompt              = build_tool_followup_prompt,
        execute_tool_requests                   = partial(
            execute_tool_requests,
            read_file_fn                        = _api_read_file_payload,
            read_context_fn                     = _api_context_payload,
            list_tree_fn                        = _list_directory,
            get_python_function_fn              = _api_python_function_payload,
            run_python_fn                       = _run_python_tool,
            replace_python_function_proposal_fn = _replace_python_function_proposal_payload,
            insert_python_function_proposal_fn  = _insert_python_function_proposal_payload,
        ),
        append_tool_call      = append_tool_call,
        append_model_response = append_model_response,
        apply_agent_edits     = _apply_agent_edits,
        set_run_output        = set_run_output,
        update_run            = update_run,
    )


def _start_chat_backend_run(body: ChatRunCreateBody) -> dict:
    user_text = str(body.user_text or "").strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="user_text cannot be empty")

    thread_path = str(body.thread_path or "__workspace__").strip() or "__workspace__"
    active_path = str(body.active_path or ".").strip() or "."
    work_item_id = str(body.work_item_id or "").strip() or None
    playbook     = route_task(user_text=user_text, mode=body.mode)
    execution_contract = playbook.payload()
    if work_item_id:
        try:
            item = get_work_item(work_item_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Work item not found") from exc
        if item.get("workspace_root") != str(_workspace_root()):
            raise HTTPException(status_code=400, detail="Work item belongs to a different workspace")
    prompt      = build_prompt_by_mode(
        mode                      = body.mode,
        user_text                 = user_text,
        path                      = active_path,
        selection                 = body.selection,
        cursor                    = body.cursor if isinstance(body.cursor, dict) else None,
        workspace_context_enabled = body.workspace_context_enabled,
        workspace_root            = _workspace_root(),
        resolve_relative_path     = _resolve_relative_path,
        is_probably_text          = _is_probably_text,
        read_text                 = _read_text,
        build_context_pack        = _build_context_pack,
        max_mention_count         = body.max_mention_count,
        max_mention_file_chars    = body.max_mention_file_chars,
        execution_contract         = execution_contract,
    )
    run = create_run(
        run_kind                  = "chat_run",
        mode                      = body.mode,
        input_text                = user_text,
        visible_text              = user_text,
        prompt_override           = prompt,
        path                      = thread_path,
        workspace_root            = _workspace_root(),
        workspace_context_enabled = body.workspace_context_enabled,
        conversation_external_id  = body.conversation_external_id,
        work_item_id              = work_item_id,
        context                   = {
            "transport":   "korechat",
            "active_path": active_path,
            "selection":   body.selection,
            "cursor":      body.cursor if isinstance(body.cursor, dict) else None,
            "execution_contract": execution_contract,
        },
    )
    if work_item_id:
        attach_run(work_item_id, run["run_id"])
    request = ChatRunRequest(
        run_id                    = run["run_id"],
        mode                      = body.mode,
        user_text                 = user_text,
        thread_path               = thread_path,
        active_path               = active_path,
        selection                 = body.selection,
        cursor                    = body.cursor if isinstance(body.cursor, dict) else None,
        prompt                    = prompt,
        workspace_root            = _workspace_root(),
        workspace_context_enabled = body.workspace_context_enabled,
        conversation_external_id  = body.conversation_external_id,
        execution_contract        = execution_contract,
    )
    start_background_run(execute_chat_run, request, _make_agent_run_services())
    return get_run(run["run_id"])


def _start_continue_backend_run(body: ContinueRunCreateBody) -> dict:
    active_path = str(body.active_path or "").strip()
    if not active_path:
        raise HTTPException(status_code=400, detail="active_path cannot be empty")

    prompt = build_continue_prompt(body.prefix, body.suffix)
    run    = create_run(
        run_kind                  = "continue_run",
        mode                      = "continue",
        input_text                = str(body.prefix or ""),
        visible_text              = "",
        prompt_override           = prompt,
        path                      = str(body.thread_path or "__workspace__").strip() or "__workspace__",
        workspace_root            = _workspace_root(),
        workspace_context_enabled = body.workspace_context_enabled,
        conversation_external_id  = body.conversation_external_id,
        context                   = {
            "transport":   "korechat",
            "active_path": active_path,
            "offset":      int(body.offset),
            "suffix":      str(body.suffix or ""),
        },
    )
    request = ContinueRunRequest(
        run_id                    = run["run_id"],
        thread_path               = str(body.thread_path or "__workspace__").strip() or "__workspace__",
        active_path               = active_path,
        prefix                    = str(body.prefix or ""),
        suffix                    = str(body.suffix or ""),
        offset                    = int(body.offset),
        prompt                    = prompt,
        workspace_root            = _workspace_root(),
        workspace_context_enabled = body.workspace_context_enabled,
        conversation_external_id  = body.conversation_external_id,
    )
    start_background_run(execute_continue_run, request, _make_agent_run_services())
    return get_run(run["run_id"])


@app.post('/api/chat/tools/execute')
def api_chat_tools_execute(body: ChatToolExecuteBody):
    run_id        = str(body.run_id or '').strip()
    run           = get_run(run_id) if run_id else None
    contract      = (run or {}).get("context", {}).get("execution_contract") or {}
    allowed_tools = tuple(contract.get("allowed_tools") or ()) or None
    results = execute_tool_requests(
        tool_requests             = list(body.tool_requests or []),
        active_path               = body.active_path,
        workspace_context_enabled = body.workspace_context_enabled,
        read_file_fn              = _api_read_file_payload,
        read_context_fn           = _api_context_payload,
        list_tree_fn              = _list_directory,
        get_python_function_fn    = _api_python_function_payload,
        run_python_fn             = _run_python_tool,
        replace_python_function_proposal_fn = _replace_python_function_proposal_payload,
        insert_python_function_proposal_fn  = _insert_python_function_proposal_payload,
        allowed_tools             = allowed_tools,
    )
    if run_id:
        for item in results:
            append_tool_call(
                run_id,
                tool_name     = str(item.get('tool') or ''),
                request_index = int(item.get('request_index') or 0),
                ok            = bool(item.get('ok')),
                request_args  = body.tool_requests[int(item.get('request_index') or 0)].get('args', {}) if int(item.get('request_index') or 0) < len(body.tool_requests) else {},
                result        = item.get('result') if item.get('ok') else None,
                error         = item.get('error') if not item.get('ok') else None,
            )
    return {'results': results}


@app.get('/api/chat/thread')
def api_chat_thread(
    path: str = Query(default='__workspace__'),
    conversation_external_id: str | None = Query(default=None),
    workspace_context_enabled: bool = True,
):
    workspace_root = _workspace_root()
    external_id    = str(conversation_external_id).strip() if isinstance(conversation_external_id, str) else None
    if external_id is None:
        latest = find_latest_run(
            path           = path,
            workspace_root = workspace_root,
        )
        # Gen1 keyed conversations by file. When upgrading to a project thread,
        # retain the most recent project conversation instead of showing a blank chat.
        if latest is None and path == '__workspace__':
            latest = find_latest_run(workspace_root=workspace_root)
        recovered_id = str((latest or {}).get('conversation_external_id') or '').strip()
        external_id  = recovered_id or None
    payload = get_thread(
        workspace_root,
        path,
        create=False,
        conversation_external_id=external_id,
        workspace_context_enabled=workspace_context_enabled,
    )
    if not payload.get('pending_response'):
        latest = find_latest_run(
            conversation_external_id = payload.get('external_id'),
            path                     = payload.get('path'),
            workspace_root           = workspace_root,
            statuses                 = {'created', 'queued', 'waiting_agent'},
        )
        if latest is not None and str(latest.get('run_kind') or '') in {'chat_send', 'chat_followup'}:
            last_assistant = payload.get('last_assistant') or {}
            update_run(
                latest['run_id'],
                status      = 'completed',
                event_type  = 'agent_reply_observed',
                event_payload = {
                    'message_id':   last_assistant.get('id'),
                    'created_at':   last_assistant.get('created_at'),
                    'content_size': len(str(last_assistant.get('content') or '')),
                },
            )
            payload['run'] = get_run(latest['run_id'])
    return payload


@app.post('/api/chat/send')
def api_chat_send(body: ChatSendBody):
    visible_text    = str(body.visible_text or '').strip()
    prompt_override = str(body.prompt_override or '').strip()
    if not visible_text:
        raise HTTPException(status_code=400, detail='visible_text cannot be empty')
    if not prompt_override:
        raise HTTPException(status_code=400, detail='prompt_override cannot be empty')
    run = create_run(
        run_kind                  = 'chat_send',
        mode                      = body.mode,
        input_text                = visible_text,
        visible_text              = visible_text,
        prompt_override           = prompt_override,
        path                      = body.path,
        workspace_root            = _workspace_root(),
        workspace_context_enabled = body.workspace_context_enabled,
        conversation_external_id  = body.conversation_external_id,
        context                   = {
            'transport': 'korechat',
        },
    )
    try:
        update_run(
            run['run_id'],
            status      = 'queued',
            event_type  = 'conversation_append_started',
            event_payload = {
                'path': body.path,
            },
        )
        thread = append_visible_message_for_conversation(
            _workspace_root(),
            body.path,
            visible_text,
            prompt_override,
            conversation_external_id=body.conversation_external_id,
            workspace_context_enabled=body.workspace_context_enabled,
        )
        update_run(
            run['run_id'],
            status                   = 'waiting_agent',
            conversation_external_id = thread.get('external_id'),
            conversation_id          = thread.get('conversation_id'),
            event_type               = 'conversation_append_completed',
            event_payload            = {
                'pending_response': bool(thread.get('pending_response')),
            },
        )
        thread['run'] = get_run(run['run_id'])
        return thread
    except Exception as exc:
        update_run(
            run['run_id'],
            status      = 'failed',
            error       = {'message': str(exc)},
            event_type  = 'conversation_append_failed',
            event_payload = {'path': body.path},
        )
        raise


@app.post('/api/chat/followup')
def api_chat_followup(body: ChatFollowupBody):
    prompt = str(body.prompt or '').strip()
    if not prompt:
        raise HTTPException(status_code=400, detail='prompt cannot be empty')
    run = create_run(
        run_kind                  = 'chat_followup',
        mode                      = body.mode,
        input_text                = prompt,
        visible_text              = str(body.visible_text or '').strip(),
        prompt_override           = prompt,
        path                      = body.path,
        workspace_root            = _workspace_root(),
        workspace_context_enabled = body.workspace_context_enabled,
        conversation_external_id  = body.conversation_external_id,
        context                   = {
            'transport':               'korechat',
            'outbound_sender_display': str(body.outbound_sender_display or '').strip() or 'agent',
        },
    )
    try:
        update_run(
            run['run_id'],
            status      = 'queued',
            event_type  = 'followup_append_started',
            event_payload = {
                'path': body.path,
            },
        )
        thread = append_internal_followup(
            _workspace_root(),
            body.path,
            prompt,
            str(body.visible_text or '').strip(),
            conversation_external_id=body.conversation_external_id,
            outbound_sender_display=str(body.outbound_sender_display or '').strip() or "agent",
            workspace_context_enabled=body.workspace_context_enabled,
        )
        update_run(
            run['run_id'],
            status                   = 'waiting_agent',
            conversation_external_id = thread.get('external_id'),
            conversation_id          = thread.get('conversation_id'),
            event_type               = 'followup_append_completed',
            event_payload            = {
                'pending_response': bool(thread.get('pending_response')),
            },
        )
        thread['run'] = get_run(run['run_id'])
        return thread
    except Exception as exc:
        update_run(
            run['run_id'],
            status      = 'failed',
            error       = {'message': str(exc)},
            event_type  = 'followup_append_failed',
            event_payload = {'path': body.path},
        )
        raise


@app.get('/api/runs')
def api_runs(limit: int = Query(default=50, ge=1, le=200)):
    return {
        'runs': list_runs(limit=limit),
    }


@app.get('/api/runs/{run_id}')
def api_run_detail(run_id: str):
    try:
        return get_run(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='Run not found') from exc


@app.post('/api/chat/workspace-context')
def api_chat_workspace_context(body: ChatWorkspaceContextBody):
    conversation = set_workspace_context_enabled(
        _workspace_root(),
        body.conversation_external_id,
        body.enabled,
    )
    return {
        'ok': bool(conversation is not None or not str(body.conversation_external_id or '').strip()),
        'enabled': bool(body.enabled),
        'conversation_external_id': body.conversation_external_id,
        'conversation_found': conversation is not None,
    }


@app.delete('/api/chat/thread')
def api_chat_delete_thread(
    path: str = Query(default='__workspace__'),
    conversation_external_id: str | None = Query(default=None),
):
    deleted = delete_thread(
        _workspace_root(),
        path,
        conversation_external_id=conversation_external_id,
    )
    return {'ok': True, 'deleted': deleted, 'path': path, 'conversation_external_id': conversation_external_id}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Launch the KoreCode editor.')
    parser.add_argument('--host', default=_cfg['host'])
    parser.add_argument('--port', type=int, default=_cfg['port'])
    parser.add_argument('--reload', action='store_true')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    LOG.info('Starting KoreCode on %s:%s', args.host, args.port)
    uvicorn.run('app.server:app', host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
