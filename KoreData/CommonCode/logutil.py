# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Logging configuration helpers for KoreData sub-services.
#
# Provides LineCappedFileHandler: a rotating file handler that caps the log at _MAX_LINES
# and trims it every _TRIM_INTERVAL writes to prevent unbounded growth.
# make_log_config() returns a logging.config dict that wires up console + file handlers.
#
# Related modules:
#   - KoreReference/main.py, KoreFeed/main.py, etc. -- each calls make_log_config() at startup
# ====================================================================================================
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from KoreCommon.service_logging import LineCappedFileHandler
from KoreCommon.service_logging import configure_service_logging as _common_configure_service_logging
from KoreCommon.service_logging import get_service_log_path as _common_get_service_log_path
from KoreCommon.service_logging import make_service_log_config as _common_make_service_log_config


def configure_service_logging(service_name: str, log_level: str = "INFO") -> Path:
    return _common_configure_service_logging(service_name=service_name, log_level=log_level)


def get_service_log_path(service_name: str) -> Path:
    return _common_get_service_log_path(service_name)


def make_log_config(service_name_or_path: str | Path, log_level: str = "INFO") -> dict:
    candidate    = Path(str(service_name_or_path))
    service_name = candidate.stem if candidate.suffix.lower() == ".log" else str(service_name_or_path)
    return _common_make_service_log_config(service_name=service_name, log_level=log_level)
