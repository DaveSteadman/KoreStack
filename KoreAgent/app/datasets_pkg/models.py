"""Public dataset-facing operations and metadata helpers."""

from datasets_pkg.service import (
    dataset_delete,
    dataset_get,
    dataset_inspect,
    dataset_list,
    dataset_rename,
    dataset_save,
    get_prompt_dataset_manifests,
    ingest_auto_dataset,
)

__all__ = [
    "dataset_delete",
    "dataset_get",
    "dataset_inspect",
    "dataset_list",
    "dataset_rename",
    "dataset_save",
    "get_prompt_dataset_manifests",
    "ingest_auto_dataset",
]
