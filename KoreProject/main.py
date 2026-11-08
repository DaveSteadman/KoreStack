from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from KoreCommon.service_logging import configure_service_logging
from KoreProject.app.config import service_config

if __name__ == "__main__":
    config = service_config()
    configure_service_logging("koreproject", "INFO")
    uvicorn.run("KoreProject.app.server:app", host=config["host"], port=config["port"])
