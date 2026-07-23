from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared FastAPI route helpers for KoreStack services.
#
# Keeps every service from reimplementing the same suite-shell endpoints:
#   - /__endpoint_manifest
#   - /suite-config.js
#   - /ui-elements/assets/{asset_path:path}
# ====================================================================================================

import json
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from KoreCommon.endpoint_manifest import build_endpoint_manifest
from KoreCommon.suite_paths import get_suite_urls_map


RouteHost = FastAPI | APIRouter


def register_endpoint_manifest(app: RouteHost, *, service_key: str, service_label: str) -> None:
    @app.get("/__endpoint_manifest", include_in_schema=False)
    def endpoint_manifest() -> dict:
        return build_endpoint_manifest(app, service_key=service_key, service_label=service_label)


def register_suite_config_js(app: RouteHost, *, urls_getter: Callable[[], dict[str, str]] = get_suite_urls_map) -> None:
    @app.get("/suite-config.js", include_in_schema=False)
    def suite_config_js() -> Response:
        urls = json.dumps(urls_getter())
        return Response(
            content     = f"window.__koreSuiteUrls = {urls};",
            media_type  = "application/javascript",
            headers     = {"Cache-Control": "no-store"},
        )


def register_ui_elements_assets(app: RouteHost, assets_dir: Path | str) -> None:
    root = Path(assets_dir).resolve()

    @app.get("/ui-elements/assets/{asset_path:path}", include_in_schema=False)
    def serve_ui_elements_asset(asset_path: str) -> FileResponse:
        candidate = (root / asset_path).resolve()
        if candidate != root and root not in candidate.parents:
            raise HTTPException(status_code=404, detail="Asset not found")
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=404, detail="Asset not found")
        return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})


def register_suite_shell_routes(
    app: RouteHost,
    *,
    service_key: str,
    service_label: str,
    ui_elements_assets_dir: Path | str | None = None,
    urls_getter: Callable[[], dict[str, str]] = get_suite_urls_map,
) -> None:
    register_endpoint_manifest(app, service_key=service_key, service_label=service_label)
    register_suite_config_js(app, urls_getter=urls_getter)
    if ui_elements_assets_dir is not None:
        register_ui_elements_assets(app, ui_elements_assets_dir)


__all__ = [
    "register_endpoint_manifest",
    "register_suite_config_js",
    "register_ui_elements_assets",
    "register_suite_shell_routes",
]
