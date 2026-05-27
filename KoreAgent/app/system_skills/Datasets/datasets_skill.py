# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Datasets skill wrapper for KoreAgent.
#
# Re-exports the dataset phase-1 functions from app/datasets.py so they are available through the
# skill catalog and normal tool-dispatch pipeline.
# ====================================================================================================

from datasets import dataset_delete
from datasets import dataset_drop_where
from datasets import dataset_expand_full_text
from datasets import dataset_filter
from datasets import dataset_get
from datasets import dataset_inspect
from datasets import dataset_list
from datasets import dataset_rename
from datasets import dataset_save
from datasets import dataset_write_koredoc


__all__ = [
    "dataset_delete",
    "dataset_drop_where",
    "dataset_expand_full_text",
    "dataset_filter",
    "dataset_get",
    "dataset_inspect",
    "dataset_list",
    "dataset_rename",
    "dataset_save",
    "dataset_write_koredoc",
]
