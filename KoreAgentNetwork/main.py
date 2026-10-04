from __future__ import annotations

import uvicorn
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import service_config  # noqa: E402


if __name__ == "__main__":
    config = service_config()
    uvicorn.run("app.server:app", host=config["host"], port=config["port"])
