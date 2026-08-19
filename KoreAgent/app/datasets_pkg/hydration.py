# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# hydration module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

"""Session hydration helpers for the datasets subsystem."""

from datasets_pkg.service import (
    build_persisted_scratchpad_payload,
    coerce_persisted_datasets_payload,
    coerce_persisted_scratchpad_payload,
    get_persisted_datasets_payload,
    hydrate_session_state,
    restore_persisted_datasets,
)

__all__ = [
    "build_persisted_scratchpad_payload",
    "coerce_persisted_datasets_payload",
    "coerce_persisted_scratchpad_payload",
    "get_persisted_datasets_payload",
    "hydrate_session_state",
    "restore_persisted_datasets",
]
