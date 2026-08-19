# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Queue manager stub — retained for import compatibility.
#
# The inbound message queue has moved to KoreChat.  This module is kept as a no-op
# so that any remaining callers do not need to be updated.
#
# Public API:
#   bootstrap()   -- no-op
#   queue_size()  -- always returns 0
#
# Related modules:
#   - app/server.py  -- may call bootstrap() at startup
# MARK: FUNCTIONS
# Function inventory:
# - bootstrap: Implements the bootstrap operation for this module.
# - queue_size: Implements the queue size operation for this module.
# ====================================================================================================
from __future__ import annotations


def bootstrap() -> None:
    pass


def queue_size() -> int:
    return 0
