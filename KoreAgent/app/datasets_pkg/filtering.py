# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# filtering module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

"""Filtering operations for dataset records."""

from datasets_pkg.service import dataset_drop_where, dataset_filter

__all__ = ["dataset_drop_where", "dataset_filter"]
