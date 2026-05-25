# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Thread-safe import state tracker for KoreReference bulk imports.
#
# Maintains a shared state dict (running, done, total, limit, errors, mode, seed)
# protected by threading.Lock so the progress can be polled by the API while a
# background import thread is running.
#
# Related modules:
#   - app/importers/kiwix.py  -- reads/writes import state during crawl
#   - app/server.py           -- exposes import state via GET /api/import/status
# ====================================================================================================
import threading

import_lock: threading.Lock = threading.Lock()
state_lock: threading.Lock = threading.Lock()
import_stop_event: threading.Event = threading.Event()
import_state: dict = {
    "running": False, "done": 0, "total": 0, "limit": 0, "errors": 0,
    "last_error": None, "mode": None, "seed": None,
    "delay_seconds": 0.0,
    "redirects_stored": 0, "last_redirect": None,
}
