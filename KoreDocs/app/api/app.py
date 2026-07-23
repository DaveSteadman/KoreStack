from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

_KORECOMMON_PARENT = next((parent for parent in Path(__file__).resolve().parents if (parent / "KoreCommon").is_dir()), None)
if _KORECOMMON_PARENT is not None and str(_KORECOMMON_PARENT) not in sys.path:
    sys.path.insert(0, str(_KORECOMMON_PARENT))

from KoreCommon.service_app import register_endpoint_manifest
from KoreCommon.service_logging import configure_service_logging
from KoreCommon.service_logging import get_service_log_path
from KoreCommon.suite_paths import get_suite_datacontrol_dir
from KoreCommon.suite_paths import get_suite_datauser_dir
from ..config import cfg as _cfg
from ..documents.korefile import service as korefile
from ..mcp import FORMAT_INFO
from ..mcp import mcp
from .middleware import AuthMiddleware
from .middleware import NoCacheMiddleware
from .routes_korefile import register_korefile_routes
from .routes_legacy_files import register_legacy_file_routes
from .routes_sheets import register_sheet_routes
from .routes_textedit import register_textedit_routes
from .routes_ui import register_ui_routes


BASE_DIR = Path(__file__).resolve().parents[2]
STATIC = Path(
    os.environ.get(
        'KORE_KOREDOCS_STATIC_DIR',
        str(BASE_DIR.parent / 'KoreUI' / 'KoreDocs' / 'static'),
    )
).resolve()
COMMONUI_ASSETS = Path(os.environ.get('KORE_UIELEMENTS_ASSETS_DIR', str(BASE_DIR.parent / 'UIElements' / 'assets')))
if not COMMONUI_ASSETS.exists():
    COMMONUI_ASSETS = STATIC / 'shared'
DATA_DIR = Path(os.environ.get('KOREDOCS_DATA_DIR', str(get_suite_datauser_dir()))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONTROL_DIR = Path(os.environ.get('KOREDOCS_CONTROL_DIR', str(get_suite_datacontrol_dir() / 'koredocs')))
CONTROL_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = CONTROL_DIR / 'korefile.db'
LOG_PATH = get_service_log_path('koredocs')
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
API_TOKEN = os.environ.get('KOREDOCS_API_TOKEN')
ALLOWED_EXTENSIONS = frozenset({'.koredoc', '.koresheet', '.korediag'})
TEXTEDIT_MAX_BYTES = 4 * 1024 * 1024

korefile.configure(DATA_DIR, DB_PATH)
(STATIC / 'korefile' / 'js').mkdir(parents=True, exist_ok=True)


def setup_logging() -> None:
    configure_service_logging('koredocs', 'INFO')


_mcp_http_app = mcp.http_app(path='/', transport='streamable-http')


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with _mcp_http_app.router.lifespan_context(_mcp_http_app):
        korefile.init_db()
        yield


app = FastAPI(title='KoreDocs', lifespan=lifespan)
register_endpoint_manifest(app, service_key='koredocs', service_label='KoreDocs')


app.add_middleware(NoCacheMiddleware)
app.add_middleware(AuthMiddleware, api_token=API_TOKEN)

app.mount('/static/doc',      StaticFiles(directory=STATIC / 'doc'),      name='doc')
app.mount('/static/sheet',    StaticFiles(directory=STATIC / 'sheet'),    name='sheet')
app.mount('/static/diag',     StaticFiles(directory=STATIC / 'diag'),     name='diag')
app.mount('/static/textedit', StaticFiles(directory=STATIC / 'textedit'), name='textedit')
app.mount('/static/korefile', StaticFiles(directory=STATIC / 'korefile'), name='korefile')
app.mount('/ui-elements/assets', StaticFiles(directory=COMMONUI_ASSETS),  name='ui-elements-assets')
app.mount('/static/commonui', StaticFiles(directory=COMMONUI_ASSETS),      name='commonui')
app.mount('/static/shared',   StaticFiles(directory=STATIC / 'shared'),    name='shared')
app.mount('/mcp', _mcp_http_app, name='mcp')

register_ui_routes(app, static_dir=STATIC)
register_legacy_file_routes(app, data_dir=DATA_DIR, allowed_extensions=ALLOWED_EXTENSIONS)
register_textedit_routes(app, textedit_max_bytes=TEXTEDIT_MAX_BYTES)
register_korefile_routes(app, data_dir=DATA_DIR)
register_sheet_routes(app)


@app.get('/api/schema')
def list_schemas(type: str | None = None):
    if type is None:
        return [FORMAT_INFO[key] for key in sorted(FORMAT_INFO)]
    if type not in FORMAT_INFO:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f'Unknown type: {type}')
    return FORMAT_INFO[type]


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import asyncio
    import json
    import threading
    import uvicorn

    parser = argparse.ArgumentParser(description='KoreDocs server')
    parser.add_argument('--host', default=_cfg['host'], help=f'Bind address (default: {_cfg["host"]})')
    parser.add_argument('--port', type=int, default=_cfg['port'], help=f'HTTP port (default: {_cfg["port"]})')
    parser.add_argument('--mcp-stdio', action='store_true', help='Run MCP protocol on stdin/stdout; web UI still starts on HTTP in a background thread')
    args = parser.parse_args(argv)

    setup_logging()
    logging.getLogger(__name__).info('KoreDocs starting data=%s db=%s log=%s', DATA_DIR, DB_PATH, LOG_PATH)
    korefile.init_db()

    def mcp_tool_names() -> list[str]:
        async def list_names() -> list[str]:
            tools = await mcp.list_tools()
            return [tool.name for tool in tools]
        return asyncio.run(list_names())

    def startup_report(host: str, port: int, stream=None, include_stdio: bool = False) -> None:
        stream = stream or sys.stdout
        url = f'http://{host}:{port}'
        print(f'[KoreDocs]  {url}/ui', file=stream)
        print(f'[KoreDocs]  MCP endpoint: {url}/mcp', file=stream)
        if include_stdio:
            config = {
                'koredocs': {
                    'command': sys.executable,
                    'args': [str(BASE_DIR / 'main.py'), '--mcp-stdio'],
                },
            }
            print('[KoreDocs]  MCP stdio config:', file=stream)
            print(json.dumps(config, indent=2), file=stream)
        print('[KoreDocs]  MCP tools: ' + ', '.join(mcp_tool_names()), file=stream)
        print(file=stream)

    uvicorn_kwargs = dict(app=app, host=args.host, port=args.port, access_log=False, log_config=None)

    if args.mcp_stdio:
        thread = threading.Thread(target=uvicorn.run, kwargs=uvicorn_kwargs, daemon=True)
        thread.start()
        startup_report(args.host, args.port, stream=sys.stderr, include_stdio=True)
        print('[KoreDocs] MCP stdio ready', file=sys.stderr, flush=True)
        try:
            mcp.run(transport='stdio', show_banner=False)
        except KeyboardInterrupt:
            pass
    else:
        startup_report(args.host, args.port)
        uvicorn.run(**uvicorn_kwargs)

    return 0


if __name__ == '__main__':
    setup_logging()
    try:
        raise SystemExit(main())
    except Exception:
        logging.getLogger('koredocs.service').exception('startup failed')
        raise
