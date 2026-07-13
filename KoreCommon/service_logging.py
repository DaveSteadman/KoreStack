from __future__ import annotations

import logging
import logging.config
from datetime import datetime
from pathlib import Path

from KoreCommon.suite_paths import get_suite_datacontrol_dir


_MAX_LINES     = 2000
_TRIM_INTERVAL = 50


class LineCappedFileHandler(logging.FileHandler):
    """FileHandler that keeps only the most recent _MAX_LINES lines."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._trim_counter = 0

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self._trim_counter += 1
        if self._trim_counter >= _TRIM_INTERVAL:
            self._trim_counter = 0
            self._trim()

    def _trim(self) -> None:
        path = Path(self.baseFilename)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except OSError:
            return
        if len(lines) > _MAX_LINES:
            path.write_text("".join(lines[-_MAX_LINES:]), encoding="utf-8")


def get_service_log_path(service_name: str) -> Path:
    cleaned  = str(service_name or "").strip().lower() or "service"
    date_dir = get_suite_datacontrol_dir() / "logs" / "services" / datetime.now().strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    return date_dir / f"{cleaned}.log"


def make_service_log_config(service_name: str, log_level: str = "INFO") -> dict:
    cleaned = str(service_name or "").strip().lower() or "service"
    path    = str(get_service_log_path(cleaned))
    fmt     = f"%(asctime)s [%(levelname)s] [{cleaned}] %(name)s: %(message)s"
    level   = str(log_level or "INFO").upper()

    return {
        "version":                  1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format":  fmt,
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "file": {
                "()":        "KoreCommon.service_logging.LineCappedFileHandler",
                "filename":  path,
                "formatter": "default",
                "encoding":  "utf-8",
            },
            "console": {
                "class":     "logging.StreamHandler",
                "formatter": "default",
            },
        },
        "root": {
            "level":    level,
            "handlers": ["file", "console"],
        },
        "loggers": {
            "uvicorn":        {"handlers": ["file", "console"], "level": level,     "propagate": False},
            "uvicorn.error":  {"handlers": ["file", "console"], "level": level,     "propagate": False},
            "uvicorn.access": {"handlers": ["file", "console"], "level": "WARNING", "propagate": False},
            "httpx":          {"handlers": ["file", "console"], "level": "WARNING", "propagate": False},
            "mcp":            {"handlers": ["file", "console"], "level": "WARNING", "propagate": False},
            "apscheduler":    {"handlers": ["file", "console"], "level": "WARNING", "propagate": False},
        },
    }


def configure_service_logging(service_name: str, log_level: str = "INFO") -> Path:
    logging.config.dictConfig(make_service_log_config(service_name=service_name, log_level=log_level))
    return get_service_log_path(service_name)
