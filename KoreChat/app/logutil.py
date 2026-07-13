# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Logging configuration helper for KoreChat.
# Produces a uvicorn-compatible log config dict that routes all output to a file.
# ====================================================================================================

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from KoreCommon.service_logging import get_service_log_path as _common_get_service_log_path
from KoreCommon.service_logging import configure_service_logging as _common_configure_service_logging
from KoreCommon.service_logging import make_service_log_config as _common_make_service_log_config


def configure_service_logging(service_name: str, log_level: str = "INFO") -> Path:
    return _common_configure_service_logging(service_name=service_name, log_level=log_level)


def get_service_log_path(service_name: str) -> Path:
    return _common_get_service_log_path(service_name)


def make_log_config(service_name_or_path: str | Path, log_level: str = "INFO") -> dict:
    candidate    = Path(str(service_name_or_path))
    service_name = candidate.stem if candidate.suffix.lower() == ".log" else str(service_name_or_path)
    return _common_make_service_log_config(service_name=service_name, log_level=log_level)
