# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreChat service lifecycle coordinator. The lifespan initialises durable storage, installs the
# Windows connection-reset suppression handler, and runs the periodic maintenance reaper. Long-lived
# operational concerns live here rather than in route modules, keeping request handlers free of
# process-start and shutdown coordination.
# MARK: FUNCTIONS
# Function inventory:
# - _install_loop_exception_handler: Implements the  install loop exception handler operation for this module.
# - _exception_handler: Implements the  exception handler operation for this module.
# - lifespan: Implements the lifespan operation for this module.
# ====================================================================================================

import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import database as db
from app.runtime.reaper import reaper_loop


service_logger = logging.getLogger("korechat.service")
_stop_reaper = threading.Event()


def _install_loop_exception_handler(loop: asyncio.AbstractEventLoop) -> None:
    def _exception_handler(loop_obj: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        handle = context.get("handle")
        callback = getattr(handle, "_callback", None)
        callback_name = getattr(callback, "__qualname__", repr(callback))
        if (
            isinstance(exc, ConnectionResetError)
            and getattr(exc, "winerror", None) == 10054
            and "_call_connection_lost" in str(callback_name)
        ):
            return
        loop_obj.default_exception_handler(context)

    loop.set_exception_handler(_exception_handler)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    _stop_reaper.clear()
    service_logger.info("starting")
    loop = asyncio.get_running_loop()
    _install_loop_exception_handler(loop)
    reaper = threading.Thread(target=reaper_loop, args=(_stop_reaper,), daemon=True)
    reaper.start()
    try:
        yield
    finally:
        _stop_reaper.set()
        service_logger.info("stopped")
