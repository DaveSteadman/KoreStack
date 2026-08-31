# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# models module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

"""Public dataset-facing operations and metadata helpers."""

from system_skills.WorkingData.collections.service import (
    dataset_clear,
    dataset_delete,
    dataset_fetch_full_text,
    dataset_get,
    dataset_inspect,
    dataset_list,
    dataset_rank,
    dataset_rename,
    dataset_save,
    dataset_select,
    get_prompt_dataset_manifests,
    ingest_auto_dataset,
)

__all__ = [
    "dataset_clear",
    "dataset_delete",
    "dataset_fetch_full_text",
    "dataset_get",
    "dataset_inspect",
    "dataset_list",
    "dataset_rank",
    "dataset_rename",
    "dataset_save",
    "dataset_select",
    "get_prompt_dataset_manifests",
    "ingest_auto_dataset",
]
