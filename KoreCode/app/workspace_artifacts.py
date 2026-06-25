from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Workspace artifacts helpers for KoreCode/app.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

from pathlib import Path

from .workspace_index import build_workspace_index
from .workspace_index import read_workspace_index_status
from .workspace_menu import build_workspace_menu


def rebuild_workspace_artifacts(root: Path) -> dict:
    menu   = build_workspace_menu(root)
    index  = build_workspace_index(root)
    return {
        **menu,
        "index": index,
    }


def read_workspace_artifact_status(root: Path) -> dict:
    menu_path = root.resolve() / "KoreCodeWorkspace.md"
    return {
        "menu_exists":      menu_path.exists(),
        "menu_path":        str(menu_path),
        "index":            read_workspace_index_status(root),
    }
