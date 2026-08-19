# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# export module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

"""Export helpers for dataset records."""

from datasets_pkg.service import dataset_write_koredoc

__all__ = ["dataset_write_koredoc"]
