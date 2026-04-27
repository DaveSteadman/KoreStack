# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Logging configuration helper for KoreConversation.
# Produces a uvicorn-compatible log config dict that routes all output to a file.
# ====================================================================================================

from pathlib import Path


# ----------------------------------------------------------------------------------------------------
def make_log_config(log_path: Path) -> dict:
    return {
        "version":                  1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            },
        },
        "handlers": {
            "file": {
                "class":     "logging.FileHandler",
                "filename":  str(log_path),
                "formatter": "default",
                "encoding":  "utf-8",
            },
            "console": {
                "class":     "logging.StreamHandler",
                "formatter": "default",
            },
        },
        "root": {
            "level":    "INFO",
            "handlers": ["file", "console"],
        },
        "loggers": {
            "uvicorn":        {"handlers": ["file", "console"], "level": "INFO", "propagate": False},
            "uvicorn.error":  {"handlers": ["file", "console"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["file", "console"], "level": "INFO", "propagate": False},
        },
    }
