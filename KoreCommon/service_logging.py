# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Standard service logging configuration for the suite. It creates date-scoped, line-capped log
# files; composes the shared file/console logger hierarchy; and installs one set of unhandled-
# exception hooks for main and worker threads. Individual services supply only their name and level,
# ensuring operational logs have a uniform location, format, and retention policy.
# MARK: FUNCTIONS
# Primary types: LineCappedFileHandler.
# Function inventory:
# - __init__: Implements the   init   operation for this module.
# - emit: Implements the emit operation for this module.
# - _trim: Implements the  trim operation for this module.
# - get_service_log_path: Returns service log path for this module.
# - make_service_log_config: Implements the make service log config operation for this module.
# - configure_service_logging: Implements the configure service logging operation for this module.
# - _install_unhandled_exception_hooks: Implements the  install unhandled exception hooks operation for this module.
# - log_main_exception: Implements the log main exception operation for this module.
# - log_thread_exception: Implements the log thread exception operation for this module.
# ====================================================================================================

from __future__ import annotations

import logging
import logging.config
import sys
import threading
from datetime import datetime
from pathlib import Path

from KoreCommon.suite_paths import get_suite_datacontrol_dir


_MAX_LINES     = 2000
_TRIM_INTERVAL = 50
_UNHANDLED_EXCEPTION_HOOKS_INSTALLED = False


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
    _install_unhandled_exception_hooks(service_name)
    path = get_service_log_path(service_name)
    logging.getLogger(f"{service_name}.service").info("service log initialized path=%s", path)
    return path


def _install_unhandled_exception_hooks(service_name: str) -> None:
    """Send otherwise lost main-thread and background-thread exceptions to the service log."""
    global _UNHANDLED_EXCEPTION_HOOKS_INSTALLED
    if _UNHANDLED_EXCEPTION_HOOKS_INSTALLED:
        return
    _UNHANDLED_EXCEPTION_HOOKS_INSTALLED = True
    logger = logging.getLogger(f"{service_name}.unhandled")
    previous_sys_hook = sys.excepthook
    previous_thread_hook = threading.excepthook

    def log_main_exception(exc_type, exc_value, traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous_sys_hook(exc_type, exc_value, traceback)
            return
        logger.critical("unhandled main-thread exception", exc_info=(exc_type, exc_value, traceback))

    def log_thread_exception(args: threading.ExceptHookArgs) -> None:
        logger.critical(
            "unhandled thread exception thread=%s",
            getattr(args.thread, "name", "unknown"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        previous_thread_hook(args)

    sys.excepthook       = log_main_exception
    threading.excepthook = log_thread_exception
