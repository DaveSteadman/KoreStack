# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FastAPI application for KoreTemplate.
#
# Endpoints:
#   GET  /                                   redirect to element2 reference page
#   GET  /status                             health check
#   GET  /api/fs/roots                       browse roots for custom file dialogs
#   GET  /api/fs/list                        directory listing for custom file dialogs
#   GET  /suite-config.js                    suite URL map (same pattern across all services)
#   GET  /ui-elements/assets/{path}          UIElements v1 assets  (frozen — for kcui-tag comparison)
#   GET  /ui-elements-2/assets/{path}        UIElements2 assets
#   GET  /ui-elements-3/assets/{path}        UIElements3 / KoreIcons assets (experimental)
#   GET  /ui/{page}                          UI pages (element2.html, kore-icons.html …)
#
# When copying this to a new service:
#   - Rename app title and _SERVICE_NAME
#   - Change port in main.py
#   - Add your routes below the "MARK: ROUTES" section
#   - Remove the UIElements v1 route if the service doesn't need it
# ====================================================================================================
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse, Response

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_BASE = Path(__file__).resolve().parents[2]   # repo root  (KoreStack/)

_UI_ELEMENTS_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(_BASE / "UIElements" / "assets"),
    )
).resolve()

_UI_ELEMENTS2_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS2_ASSETS_DIR",
        str(_BASE / "UIElements2" / "assets"),
    )
).resolve()

_UI_ELEMENTS3_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS3_ASSETS_DIR",
        str(_BASE / "UIElements3" / "assets"),
    )
).resolve()

_UI_DIR = Path(__file__).parent / "ui"


def _build_browse_roots() -> dict[str, Path]:
    configured = os.environ.get("KORETEMPLATE_FILE_DIALOG_ROOTS", "").strip()
    roots: dict[str, Path] = {"workspace": _BASE}
    if configured:
        for index, raw in enumerate(configured.split(os.pathsep), start=1):
            candidate = Path(raw).expanduser().resolve()
            if candidate.exists() and candidate.is_dir():
                roots[f"extra{index}"] = candidate
    return roots


_BROWSE_ROOTS = _build_browse_roots()


def _get_browse_root(root_id: str) -> Path:
    root = _BROWSE_ROOTS.get(root_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Unknown browse root")
    return root


def _resolve_browse_path(root: Path, relative_path: str) -> Path:
    if relative_path in {"", "."}:
        return root

    candidate = (root / relative_path).resolve()
    if candidate != root and root not in candidate.parents:
        raise HTTPException(status_code=400, detail="Path escapes browse root")
    return candidate

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

_SERVICE_NAME = "KoreTemplate"

app = FastAPI(title=_SERVICE_NAME)

# ---------------------------------------------------------------------------
# MARK: INFRASTRUCTURE
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/ui/element2.html")


@app.get("/status")
def status():
    return {"status": "ok", "service": _SERVICE_NAME}


@app.get("/api/fs/roots")
def api_fs_roots():
    return {
        "roots": [
            {
                "id": root_id,
                "label": root_path.name or str(root_path),
                "path": str(root_path),
            }
            for root_id, root_path in _BROWSE_ROOTS.items()
        ]
    }


@app.get("/api/fs/list")
def api_fs_list(
    root: str = Query("workspace"),
    path: str = Query(""),
):
    root_path = _get_browse_root(root)
    current = _resolve_browse_path(root_path, path)
    if not current.exists() or not current.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    try:
        relative = current.relative_to(root_path)
        relative_path = "" if str(relative) == "." else relative.as_posix()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid browse path") from exc

    entries = []
    for child in sorted(current.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        entries.append({
            "name": child.name,
            "type": "dir" if child.is_dir() else "file",
            "path": child.relative_to(root_path).as_posix(),
            "absolute_path": str(child),
            "size": None if child.is_dir() else child.stat().st_size,
        })

    return {
        "root": {
            "id": root,
            "label": root_path.name or str(root_path),
            "path": str(root_path),
        },
        "path": relative_path,
        "display_path": "/" if not relative_path else f"/{relative_path}",
        "absolute_path": str(current),
        "parent_path": "" if not relative_path else ("" if Path(relative_path).parent.as_posix() == "." else Path(relative_path).parent.as_posix()),
        "entries": entries,
    }


@app.get("/suite-config.js", include_in_schema=False)
def suite_config_js():
    urls = os.environ.get("KORE_SUITE_URLS", "{}")
    return Response(
        content=f"window.__koreSuiteUrls = {urls};",
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


# UIElements v1 — frozen; served so element2.html kcui-tag comparison section works
@app.get("/ui-elements/assets/{asset_path:path}", include_in_schema=False)
def serve_ui_elements_asset(asset_path: str):
    candidate = (_UI_ELEMENTS_ASSETS / asset_path).resolve()
    if candidate != _UI_ELEMENTS_ASSETS and _UI_ELEMENTS_ASSETS not in candidate.parents:
        raise HTTPException(status_code=404, detail="Not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})


# UIElements2 — working library
@app.get("/ui-elements-2/assets/{asset_path:path}", include_in_schema=False)
def serve_ui_elements2_asset(asset_path: str):
    candidate = (_UI_ELEMENTS2_ASSETS / asset_path).resolve()
    if candidate != _UI_ELEMENTS2_ASSETS and _UI_ELEMENTS2_ASSETS not in candidate.parents:
        raise HTTPException(status_code=404, detail="Not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})


# UIElements3 — KoreIcons experimental grid system
@app.get("/ui-elements-3/assets/{asset_path:path}", include_in_schema=False)
def serve_ui_elements3_asset(asset_path: str):
    candidate = (_UI_ELEMENTS3_ASSETS / asset_path).resolve()
    if candidate != _UI_ELEMENTS3_ASSETS and _UI_ELEMENTS3_ASSETS not in candidate.parents:
        raise HTTPException(status_code=404, detail="Not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})


# Service pages — HTML files that live in app/ui/ and consume UIElements2 components
@app.get("/ui/{page}", include_in_schema=False)
def serve_ui_page(page: str):
    candidate = (_UI_DIR / page).resolve()
    if candidate != _UI_DIR and _UI_DIR not in candidate.parents:
        raise HTTPException(status_code=404, detail="Not found")
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(str(candidate), headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# MARK: ROUTES  — add service-specific routes here
# ---------------------------------------------------------------------------
