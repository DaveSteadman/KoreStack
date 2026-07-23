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
        str(Path(__file__).resolve().parents[3] / "UIElements" / "assets"),
    )
).resolve()

NO_STORE_HEADERS = {"Cache-Control": "no-store"}
INPUT_HISTORY_MAX = 32
