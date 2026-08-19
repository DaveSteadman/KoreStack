# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# sitecustomize module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

"""Enable KoreStack's process watchdog for Python processes it launches."""

from KoreCommon.stack_watchdog import start_from_environment

start_from_environment()
