# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Optional process-liveness watchdog for services launched by the KoreStack supervisor. It reads
# supervisor settings from the environment, polls the health endpoint in one daemon thread, and
# terminates the child process after a bounded failure count. Directly launched services are left
# untouched because the controlling health URL is absent.
# MARK: FUNCTIONS
# Function inventory:
# - start_from_environment: Starts from environment for this module.
# - _supervise: Implements the  supervise operation for this module.
# ====================================================================================================

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.request


_LOGGER = logging.getLogger("korestack.watchdog")
_STARTED = False


def start_from_environment() -> None:
    """Start the optional supervisor watchdog configured by KoreStack.

    Directly launched services do not receive ``KORESTACK_HEALTH_URL`` and are
    therefore unaffected.  ``os._exit`` is intentional: a failed supervisor
    must reliably end every worker thread and child event loop in this process.
    """
    global _STARTED
    health_url = os.environ.get("KORESTACK_HEALTH_URL", "").strip()
    if not health_url or _STARTED:
        return

    _STARTED = True
    interval  = max(0.5, float(os.environ.get("KORESTACK_WATCHDOG_INTERVAL_SECONDS", "3")))
    failures  = max(1, int(os.environ.get("KORESTACK_WATCHDOG_FAILURES", "3")))
    grace     = max(0.0, float(os.environ.get("KORESTACK_WATCHDOG_GRACE_SECONDS", "30")))
    service   = os.environ.get("KORESTACK_SERVICE_NAME", "service")

    def _supervise() -> None:
        if grace:
            time.sleep(grace)
        missed = 0
        while True:
            try:
                with urllib.request.urlopen(health_url, timeout=min(interval, 2.0)) as response:
                    healthy = 200 <= response.status < 300
            except Exception:
                healthy = False

            missed = 0 if healthy else missed + 1
            if missed >= failures:
                _LOGGER.error(
                    "%s exiting: KoreStack health endpoint unavailable after %s checks (%s)",
                    service,
                    missed,
                    health_url,
                )
                os._exit(1)
            time.sleep(interval)

    threading.Thread(target=_supervise, name="korestack-watchdog", daemon=True).start()
