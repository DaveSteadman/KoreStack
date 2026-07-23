from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Static and redirect routes extracted from the input-layer server.
#
# Owns simple file-serving endpoints for the web client, suite bootstrap assets,
# shared UIElements assets, and the KoreChat UI redirect.  The goal is to keep the
# main input-layer server focused on runtime orchestration rather than static route
# registration noise.
# ====================================================================================================

import os
from pathlib import Path
from typing import Callable

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.responses import RedirectResponse
from fastapi.responses import Response
from KoreCommon.service_app import register_suite_config_js
from KoreCommon.service_app import register_ui_elements_assets


def register_static_routes(
    app,
    *,
    web_dir: Path,
    ui_elements_assets: Path,
    get_korechat_base_url: Callable[[], str | None],
) -> None:
    register_suite_config_js(app)
    register_ui_elements_assets(app, ui_elements_assets)

    @app.get("/", include_in_schema=False)
    def serve_index():
        index = web_dir / "index.html"
        if not index.exists():
            return {"error": "Web UI not found"}
        return FileResponse(str(index), headers={"Cache-Control": "no-store"})

    @app.get("/skills-catalog", include_in_schema=False)
    def serve_skills_catalog():
        page = web_dir / "skills_catalog.html"
        if not page.exists():
            return {"error": "Skills Catalog UI not found"}
        return FileResponse(str(page), headers={"Cache-Control": "no-store"})

    @app.get("/static/app.js", include_in_schema=False)
    def serve_app_js():
        return FileResponse(str(web_dir / "app.js"), headers={"Cache-Control": "no-store"})

    @app.get("/static/style.css", include_in_schema=False)
    def serve_style_css():
        return FileResponse(str(web_dir / "style.css"), headers={"Cache-Control": "no-store"})

    @app.get("/static/{asset_path:path}", include_in_schema=False)
    def serve_static_asset(asset_path: str):
        candidate = (web_dir / asset_path).resolve()
        if candidate != web_dir and web_dir not in candidate.parents:
            raise HTTPException(status_code=404, detail="Asset not found")
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})

    @app.get("/conversations", include_in_schema=False)
    def redirect_to_korechat_ui():
        base_url = get_korechat_base_url()
        if not base_url:
            raise HTTPException(status_code=503, detail="KoreChat is not configured")
        return RedirectResponse(url=f"{base_url}/ui", status_code=307)

    @app.get("/favicon.ico", include_in_schema=False)
    def serve_favicon():
        ico = web_dir / "favicon.ico"
        if not ico.exists():
            return Response(status_code=404)
        return FileResponse(str(ico), media_type="image/x-icon", headers={"Cache-Control": "no-cache"})

    @app.get("/README.md", include_in_schema=False)
    def serve_readme():
        import markdown

        readme = web_dir.parent.parent.parent / "README.md"
        if not readme.exists():
            return Response(status_code=404)
        md_text = readme.read_text(encoding="utf-8")
        body    = markdown.markdown(md_text, extensions=["tables", "fenced_code", "toc"])
        html    = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>README</title>"
            "<style>"
            "body{font-family:sans-serif;max-width:860px;margin:40px auto;padding:0 20px;line-height:1.6;color:#ccc;background:#1a1a1a}"
            "h1,h2,h3{color:#e8e8e8;border-bottom:1px solid #333;padding-bottom:4px}"
            "a{color:#6ab0f5}"
            "code{background:#2a2a2a;padding:2px 5px;border-radius:3px;font-size:0.9em}"
            "pre{background:#2a2a2a;padding:12px;border-radius:4px;overflow-x:auto}"
            "pre code{background:none;padding:0}"
            "table{border-collapse:collapse;width:100%}"
            "th,td{border:1px solid #444;padding:6px 12px;text-align:left}"
            "th{background:#2a2a2a}"
            "blockquote{border-left:3px solid #555;margin:0;padding-left:16px;color:#999}"
            "</style></head><body>"
            + body
            + "</body></html>"
        )
        return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})
