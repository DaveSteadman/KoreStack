# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Centralised, immutable API configuration for KoreChat's UI delivery. These values resolve the
# optional deployment overrides once at import time and are shared by UI routes, avoiding repeated
# path construction and keeping cache and input-history policy consistent across endpoints.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

import os
from pathlib import Path


UI_DIR = Path(
    os.environ.get(
        "KORE_KORECHAT_UI_DIR",
        str(Path(__file__).resolve().parents[3] / "KoreUI" / "KoreChat" / "ui"),
    )
).resolve()

UI_ELEMENTS_ASSETS = Path(
    os.environ.get(
        "KORE_UIELEMENTS_ASSETS_DIR",
        str(Path(__file__).resolve().parents[3] / "KoreUI" / "UIElements" / "assets"),
    )
).resolve()

NO_STORE_HEADERS = {"Cache-Control": "no-store"}
INPUT_HISTORY_MAX = 32
