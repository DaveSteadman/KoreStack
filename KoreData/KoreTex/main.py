import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "KoreData" / "CommonCode"))

import logutil
import uvicorn

from KoreData.KoreTex.app.config import cfg


if __name__ == "__main__":
    logutil.configure_service_logging("koretex", cfg["log_level"])
    logger = logging.getLogger("koretex.service")
    try:
        Path(cfg["data_dir"]).mkdir(parents=True, exist_ok=True)
        logger.info("starting host=%s port=%s", cfg["host"], cfg["port"])
        uvicorn.run(
            "KoreData.KoreTex.app.server:app",
            host=cfg["host"],
            port=cfg["port"],
            access_log=False,
            log_level=cfg["log_level"],
            log_config=logutil.make_log_config("koretex", cfg["log_level"]),
        )
    except Exception:
        logger.exception("startup failed")
        raise
    finally:
        logger.info("shutdown complete")
