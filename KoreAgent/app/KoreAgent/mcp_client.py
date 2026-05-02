# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Bridges the synchronous MiniAgentFramework skill pipeline to async MCP servers.
#
# Maintains a dedicated asyncio event loop in a background daemon thread.
# All async MCP operations are dispatched from the sync orchestration thread via
# asyncio.run_coroutine_threadsafe(...).result(), which blocks the calling thread
# until the result arrives - exactly like any other blocking I/O in the pipeline.
# No changes to orchestration, skill_executor, or any existing skill module are required
# beyond the two call sites that extend tool_defs and route tool calls.
#
# MCP connections are declared in default.json under "mcp_connections":
#   [{"name": "KoreData", "url": "http://localhost:8800/mcp", "expected_prefix": "koredata_"}]
# The older "mcp_servers" key is still accepted as a backwards-compatible alias.
#
# At start() the client connects to each server, calls list_tools(), and builds:
#   - _mcp_tool_defs:  OpenAI-format tool definitions merged into the LLM tool list
#   - _mcp_tool_index: tool_name -> {url, connection, purpose} routing table for dispatch
#
# Related modules:
#   - skill_executor.py  -- calls is_mcp_tool() and call_mcp_tool() per invocation
#   - orchestration.py   -- calls get_mcp_tool_definitions() to extend tool_defs
#   - main.py            -- calls start() at application startup and stop() on exit
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import asyncio
import json
import threading

from contextlib import asynccontextmanager
from pathlib import Path

try:
    from mcp import ClientSession
    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamablehttp_client
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False


# ====================================================================================================
# MARK: STATE
# ====================================================================================================
_loop:        asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None          = None

_mcp_tool_defs:      list[dict] = []
_mcp_tool_index:     dict[str, dict] = {}   # tool_name -> {"url": str, "connection": str, ...}
_configured_servers: list[dict] = []        # normalized MCP connection entries, populated by start()
_server_reachable:   dict[str, bool] = {}   # url -> True/False; populated by start()/reconnect()

_CALL_TIMEOUT    = 30.0  # seconds applied to call_tool
_CONNECT_TIMEOUT =  5.0  # seconds to wait for list_tools during startup
_HEALTH_TIMEOUT  =  2.0  # seconds for fast-fail ping before first call to an unchecked server


# ====================================================================================================
# MARK: LIFECYCLE
# ====================================================================================================
def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


# ----------------------------------------------------------------------------------------------------
def start(config_path: Path) -> None:
    """Start the MCP event loop thread and enumerate tools from all configured connections.

    No-op when the mcp package is not installed or no connections are configured.
    """
    global _loop, _loop_thread, _mcp_tool_defs, _mcp_tool_index, _configured_servers, _server_reachable

    if not _MCP_AVAILABLE:
        return

    servers             = _load_server_config(config_path)
    _configured_servers = servers
    if not servers:
        return

    _loop        = asyncio.new_event_loop()
    _loop_thread = threading.Thread(
        target = _run_loop,
        args   = (_loop,),
        daemon = True,
        name   = "mcp-event-loop",
    )
    _loop_thread.start()

    future          = asyncio.run_coroutine_threadsafe(_enumerate_all_servers(servers), _loop)
    try:
        defs, index = future.result(timeout=_CONNECT_TIMEOUT * len(servers) + 2)
    except TimeoutError:
        print(f"[mcp] Warning: tool enumeration timed out after {_CONNECT_TIMEOUT}s per server - continuing without MCP tools", flush=True)
        # Stop the event loop thread to avoid accumulating orphaned threads across restarts.
        _loop.call_soon_threadsafe(_loop.stop)
        if _loop_thread is not None:
            _loop_thread.join(timeout=2.0)
        _loop        = None
        _loop_thread = None
        defs, index  = [], {}
    _mcp_tool_defs  = defs
    _mcp_tool_index = index

    # Mark each connection reachable/unreachable based on whether any tools were enumerated from it.
    registered_urls = {info["url"] for info in _mcp_tool_index.values()}
    _server_reachable = {srv.get("url", ""): srv.get("url", "") in registered_urls for srv in servers}

    count       = len(defs)
    server_list = ", ".join(s.get("name") or s["url"] for s in servers)
    print(f"[mcp] {count} tool(s) registered from: {server_list}", flush=True)


# ----------------------------------------------------------------------------------------------------
def stop() -> None:
    """Stop the MCP event loop thread. Called on application shutdown."""
    global _loop, _loop_thread

    if _loop is not None:
        _loop.call_soon_threadsafe(_loop.stop)
        _loop        = None
        _loop_thread = None


# ----------------------------------------------------------------------------------------------------
def reconnect() -> tuple[int, list[str]]:
    """Re-enumerate tools from all configured connections without restarting MAF.

    Starts the event loop thread if it was never started or died after a timeout.
    Returns (total_tool_count, [status_line, ...]) so the caller can report results.
    """
    global _loop, _loop_thread, _mcp_tool_defs, _mcp_tool_index, _server_reachable

    if not _MCP_AVAILABLE:
        return 0, ["mcp package not installed"]

    servers = _configured_servers
    if not servers:
        return 0, ["No MCP connections configured in default.json"]

    if _loop is None or _loop_thread is None or not _loop_thread.is_alive():
        _loop        = asyncio.new_event_loop()
        _loop_thread = threading.Thread(
            target = _run_loop,
            args   = (_loop,),
            daemon = True,
            name   = "mcp-event-loop",
        )
        _loop_thread.start()

    future = asyncio.run_coroutine_threadsafe(_enumerate_all_servers(servers), _loop)
    try:
        defs, index = future.result(timeout=_CONNECT_TIMEOUT * len(servers) + 2)
    except TimeoutError:
        return 0, [f"Reconnect timed out after {_CONNECT_TIMEOUT:.0f}s per server"]

    _mcp_tool_defs  = defs
    _mcp_tool_index = index

    registered_urls: dict[str, int] = {}
    for info in index.values():
        registered_urls[info["url"]] = registered_urls.get(info["url"], 0) + 1

    # Refresh reachability cache after reconnect.
    _server_reachable = {srv.get("url", ""): registered_urls.get(srv.get("url", ""), 0) > 0 for srv in servers}

    lines = []
    for srv in servers:
        name    = srv.get("name") or srv.get("url", "?")
        url     = srv.get("url", "?")
        purpose = srv.get("purpose", "")
        count   = registered_urls.get(url, 0)
        ok      = count > 0
        detail  = f" - {purpose}" if purpose else ""
        lines.append(f"  {'OK  ' if ok else 'FAIL'}  {name}  {url}  ({count} tool(s)){detail}")

    return len(defs), lines


# ====================================================================================================
# MARK: PUBLIC API
# ====================================================================================================
def is_mcp_tool(tool_name: str) -> bool:
    return tool_name in _mcp_tool_index


# ----------------------------------------------------------------------------------------------------
def get_mcp_tool_definitions() -> list[dict]:
    return list(_mcp_tool_defs)


# ----------------------------------------------------------------------------------------------------
def get_server_status() -> list[dict]:
    """Return [{name, url, purpose, tool_count, ok}] for each configured connection."""
    registered_urls: dict[str, int] = {}
    for info in _mcp_tool_index.values():
        registered_urls[info["url"]] = registered_urls.get(info["url"], 0) + 1
    return [
        {
            "name":       srv.get("name") or srv.get("url", "?"),
            "url":        srv.get("url", "?"),
            "purpose":    srv.get("purpose", ""),
            "transport":  srv.get("transport", "streamable_http"),
            "enabled":    srv.get("enabled", True),
            "tool_count": registered_urls.get(srv.get("url", ""), 0),
            "ok":         registered_urls.get(srv.get("url", ""), 0) > 0,
        }
        for srv in _configured_servers
    ]


# ----------------------------------------------------------------------------------------------------
def call_mcp_tool(tool_name: str, arguments: dict) -> object:
    """Call an MCP tool synchronously from the orchestration thread.

    Blocks until the remote call completes or _CALL_TIMEOUT seconds elapses.
    Returns a string for text results, a list of dicts for mixed content,
    or an "Error: ..." string when the server signals isError.
    """
    if _loop is None:
        raise RuntimeError("MCP client is not running - call mcp_client.start() at startup")

    entry = _mcp_tool_index.get(tool_name)
    if entry is None:
        raise RuntimeError(f"MCP tool '{tool_name}' is not registered")

    url         = entry["url"]
    server_name = entry.get("connection", entry.get("server", url))

    # Fast-fail: if the server is already known to be unreachable, skip the 30-second wait.
    if _server_reachable.get(url) is False:
        return f"Error: {server_name} is currently unreachable"

    # If reachability is unknown (not populated by start/reconnect), do a quick 2-second ping.
    if url not in _server_reachable:
        ping_future = asyncio.run_coroutine_threadsafe(_ping_server_async(entry), _loop)
        try:
            reachable = ping_future.result(timeout=_HEALTH_TIMEOUT)
        except Exception:
            reachable = False
        _server_reachable[url] = reachable
        if not reachable:
            return f"Error: {server_name} is currently unreachable"

    future = asyncio.run_coroutine_threadsafe(
        _call_tool_async(url, tool_name, arguments),
        _loop,
    )
    try:
        return future.result(timeout=_CALL_TIMEOUT)
    except Exception as exc:
        # Mark the server unreachable so subsequent calls in this run fail fast.
        _server_reachable[url] = False
        raise RuntimeError(f"MCP call to {server_name}/{tool_name} failed: {exc}") from exc


# ====================================================================================================
# MARK: INTERNAL - CONFIG
# ====================================================================================================
def _load_server_config(config_path: Path) -> list[dict]:
    try:
        data    = json.loads(config_path.read_text(encoding="utf-8"))
        raw_connections = data.get("mcp_connections")
        if raw_connections is None:
            raw_connections = data.get("mcp_servers", [])
        if raw_connections is None and isinstance(data.get("mcp"), dict):
            raw_connections = data["mcp"].get("connections", [])

        # If the bootstrap file has no connections, fall through to the merged suite config.
        # This allows mcp connections to be declared in config/default.json (with service
        # references resolved against the suite's services block) rather than hardcoded here.
        if not isinstance(raw_connections, list) or not raw_connections:
            from KoreAgent.utils.workspace_utils import load_runtime_config
            raw_connections = load_runtime_config().get("mcp_connections", [])

        if not isinstance(raw_connections, list):
            return []
        return [
            _normalize_connection(s)
            for s in raw_connections
            if isinstance(s, dict) and s.get("url") and s.get("enabled", True) is not False
        ]
    except FileNotFoundError:
        return []
    except Exception as exc:
        import sys
        print(f"[mcp_client] Warning: could not load MCP server config from {config_path}: {exc}", file=sys.stderr)
        return []


# ----------------------------------------------------------------------------------------------------
def _normalize_connection(raw: dict) -> dict:
    """Return the canonical connection shape used internally.

    MCP tool names are owned by each server. expected_prefix is a validation guard only; it does not
    rename tools.
    """
    name = str(raw.get("name") or raw.get("server") or raw.get("url", "")).strip()
    allowed_tools = raw.get("allowed_tools") or []
    blocked_tools = raw.get("blocked_tools") or []
    return {
        "name":            name,
        "url":             str(raw.get("url", "")).strip(),
        "transport":       str(raw.get("transport") or "streamable_http").strip().lower(),
        "purpose":         str(raw.get("purpose", "")).strip(),
        "expected_prefix": str(raw.get("expected_prefix") or raw.get("tool_prefix") or "").strip(),
        "allowed_tools":   [str(tool).strip() for tool in allowed_tools if str(tool).strip()] if isinstance(allowed_tools, list) else [],
        "blocked_tools":   [str(tool).strip() for tool in blocked_tools if str(tool).strip()] if isinstance(blocked_tools, list) else [],
        "enabled":         raw.get("enabled", True) is not False,
    }


# ----------------------------------------------------------------------------------------------------
def _format_connection_error(exc: BaseException) -> str:
    """Return a compact, useful message for MCP startup/connect failures.

    The MCP streamable HTTP client can wrap ordinary connection errors in nested TaskGroup
    ExceptionGroups. Showing the outer message gives "unhandled errors in a TaskGroup", which is
    technically true but not useful at startup.
    """
    nested = getattr(exc, "exceptions", None)
    if nested:
        parts = [_format_connection_error(inner) for inner in nested]
        compact = []
        for part in parts:
            if part and part not in compact:
                compact.append(part)
        if compact:
            return "; ".join(compact)

    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


# ----------------------------------------------------------------------------------------------------
@asynccontextmanager
async def _open_transport(server: dict):
    """Open the configured MCP transport and yield (read_stream, write_stream)."""
    url       = server["url"]
    transport = (server.get("transport") or "streamable_http").lower()

    if transport == "sse":
        async with sse_client(url, timeout=_CONNECT_TIMEOUT, sse_read_timeout=_CALL_TIMEOUT) as (read, write):
            yield read, write
        return

    if transport not in ("streamable_http", "streamable-http", "http"):
        raise ValueError(f"Unsupported MCP transport '{transport}'")

    async with streamablehttp_client(url) as (read, write, _):
        yield read, write


# ====================================================================================================
# MARK: INTERNAL - ASYNC OPERATIONS
# ====================================================================================================
async def _ping_server_async(server: dict) -> bool:
    # Lightweight connectivity check: open a session and initialise, then exit.
    # Returns True if the server responds within the caller-imposed timeout, False otherwise.
    try:
        async with _open_transport(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------------------------------
async def _enumerate_all_servers(servers: list[dict]) -> tuple[list[dict], dict]:
    defs:  list[dict] = []
    index: dict       = {}

    for server in servers:
        name = server.get("name") or server["url"]
        url  = server["url"]
        try:
            server_defs, server_index = await asyncio.wait_for(
                _list_tools_async(server), timeout=_CONNECT_TIMEOUT
            )
            duplicate_names = sorted(set(index).intersection(server_index))
            if duplicate_names:
                names = ", ".join(duplicate_names)
                print(f"[mcp] Warning: duplicate tool name(s) from '{name}' ignored: {names}", flush=True)
                server_defs = [
                    tool_def for tool_def in server_defs
                    if tool_def.get("function", {}).get("name") not in duplicate_names
                ]
                for tool_name in duplicate_names:
                    server_index.pop(tool_name, None)
            defs.extend(server_defs)
            index.update(server_index)
        except Exception as exc:
            print(f"[mcp] Warning: could not connect to '{name}' at {url}: {_format_connection_error(exc)}", flush=True)

    return defs, index


# ----------------------------------------------------------------------------------------------------
async def _list_tools_async(server: dict) -> tuple[list[dict], dict]:
    defs:  list[dict] = []
    index: dict       = {}
    url               = server["url"]
    server_name       = server.get("name") or url
    expected_prefix   = server.get("expected_prefix", "")
    allowed_tools     = set(server.get("allowed_tools") or [])
    blocked_tools     = set(server.get("blocked_tools") or [])

    async with _open_transport(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()

            for tool in result.tools:
                if allowed_tools and tool.name not in allowed_tools:
                    print(
                        f"[mcp] Warning: ignoring tool '{tool.name}' from '{server_name}' "
                        "because it is not in allowed_tools",
                        flush=True,
                    )
                    continue
                if tool.name in blocked_tools:
                    print(
                        f"[mcp] Warning: ignoring tool '{tool.name}' from '{server_name}' "
                        "because it is in blocked_tools",
                        flush=True,
                    )
                    continue
                if expected_prefix and not tool.name.startswith(expected_prefix):
                    print(
                        f"[mcp] Warning: ignoring tool '{tool.name}' from '{server_name}' "
                        f"because it does not match expected_prefix '{expected_prefix}'",
                        flush=True,
                    )
                    continue
                schema   = tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
                tool_def = {
                    "type": "function",
                    "function": {
                        "name":        tool.name,
                        "description": tool.description or "",
                        "parameters":  schema,
                    },
                }
                defs.append(tool_def)
                index[tool.name] = {
                    "url":             url,
                    "server":          server_name,
                    "connection":      server_name,
                    "transport":       server.get("transport", "streamable_http"),
                    "purpose":         server.get("purpose", ""),
                    "expected_prefix": expected_prefix,
                    "allowed_tools":   list(allowed_tools),
                    "blocked_tools":   list(blocked_tools),
                }

    return defs, index


# ----------------------------------------------------------------------------------------------------
async def _call_tool_async(url: str, tool_name: str, arguments: dict) -> object:
    entry = _mcp_tool_index.get(tool_name) or {"url": url}
    async with _open_transport(entry) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)

    if result.isError:
        text_parts = [c.text for c in result.content if hasattr(c, "text") and c.text]
        error_msg  = " ".join(text_parts) if text_parts else "MCP tool returned an error"
        return f"Error: {error_msg}"

    text_parts = [c.text for c in result.content if hasattr(c, "text") and c.text]
    if text_parts:
        return "\n".join(text_parts) if len(text_parts) > 1 else text_parts[0]

    return [c.__dict__ for c in result.content]
