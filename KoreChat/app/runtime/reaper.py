# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Background maintenance loop for durable event state. It periodically releases abandoned worker
# claims and removes stale delivery notifications, allowing recovery after worker loss without
# burdening request handlers. The caller supplies the stop event so service lifecycle management
# remains in the API startup module.
# MARK: FUNCTIONS
# Function inventory:
# - reaper_loop: Implements the reaper loop operation for this module.
# ====================================================================================================

import logging
import threading

from app import database as db


logger = logging.getLogger(__name__)


def reaper_loop(stop_event: threading.Event) -> None:
    while not stop_event.wait(60):
        try:
            db.release_stale_claims()
        except Exception as exc:
            logger.warning("Reaper error: %s", exc)
        try:
            db.clear_stale_outbound_ready()
        except Exception as exc:
            logger.warning("Reaper outbound_ready cleanup error: %s", exc)
