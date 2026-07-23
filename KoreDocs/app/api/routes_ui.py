from __future__ import annotations

from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse

from KoreCommon.service_app import register_suite_config_js


def register_ui_routes(app, *, static_dir: Path) -> None:
    register_suite_config_js(app)

    @app.get('/status', include_in_schema=False)
    def health():
        return {'status': 'ok', 'service': 'koredocs'}

    @app.get('/ui', include_in_schema=False)
    def serve_ui():
        return FileResponse(static_dir / 'korefile' / 'index.html')

    @app.get('/', include_in_schema=False)
    def root():
        return RedirectResponse('/ui')

    @app.get('/doc', include_in_schema=False)
    def serve_doc():
        return FileResponse(static_dir / 'doc' / 'index.html')

    @app.get('/sheet', include_in_schema=False)
    def serve_sheet():
        return FileResponse(static_dir / 'sheet' / 'index.html')

    @app.get('/diag', include_in_schema=False)
    def serve_diag():
        return FileResponse(static_dir / 'diag' / 'index.html')

    @app.get('/textedit', include_in_schema=False)
    def serve_textedit():
        return FileResponse(static_dir / 'textedit' / 'index.html')
