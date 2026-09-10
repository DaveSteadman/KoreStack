# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# hydration module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

"""Session hydration helpers for Working Data collections."""

from system_skills.WorkingData.collections.service import (
    coerce_persisted_collections_payload,
    coerce_persisted_values_payload,
    get_persisted_collections_payload,
    hydrate_working_data_state,
    restore_persisted_datasets,
)

__all__ = [
    "coerce_persisted_collections_payload",
    "coerce_persisted_values_payload",
    "get_persisted_collections_payload",
    "hydrate_working_data_state",
    "restore_persisted_datasets",
]
