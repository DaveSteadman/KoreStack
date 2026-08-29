"""Native REST invocation routes for every reviewed KoreDocs skill."""

from typing import Any

from KoreCommon.skill_service import register_skill_invocation_routes

from ..documents.korefile import service as korefile
from ..mcp import tools_common
from ..mcp import tools_korediag
from ..mcp import tools_koredoc
from ..mcp import tools_koresheet


def _koredocs_files_list(
    folder_path: str | None = None,
    type: str | None = None,
    name: str | None = None,
    limit: int | None = 100,
) -> list[dict[str, Any]]:
    return korefile.list_files(
        folder_path=folder_path or None,
        ext=type or None,
        name=name or None,
        limit=int(limit) if limit is not None else None,
    )


def _koredocs_file_get(file_id: int, include_content: bool = True) -> dict[str, Any] | None:
    return korefile.get_file(file_id, include_content=include_content)


def _koredocs_file_create(
    folder_id: int,
    name: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return korefile.create_file(folder_id, name, content, metadata)


def _koredocs_search(
    query: str,
    type: str | None = None,
    folder_path: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    return korefile.search(query, ext=type or None, folder_path=folder_path or None, limit=int(limit))


def _handlers() -> dict[str, object]:
    handlers: dict[str, object] = {
        "koredocs_files_list": _koredocs_files_list,
        "koredocs_file_get": _koredocs_file_get,
        "koredocs_file_create": _koredocs_file_create,
        "koredocs_search": _koredocs_search,
    }
    # These are the reviewed, formerly MCP-exposed KoreDocs operations.  The
    # REST boundary deliberately exports only public `koredocs_*` functions;
    # implementation helpers and the ordinary UI API remain private.
    for module in (tools_common, tools_koredoc, tools_koresheet, tools_korediag):
        for name, handler in vars(module).items():
            if name.startswith("koredocs_") and callable(handler):
                handlers[name] = handler
    return handlers


def register_skill_routes(app) -> None:
    register_skill_invocation_routes(app, _handlers())
