# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# full text module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

"""Full-text expansion helpers for datasets."""

from system_skills.WorkingData.collections.service import dataset_expand_full_text

__all__ = ["dataset_expand_full_text"]
