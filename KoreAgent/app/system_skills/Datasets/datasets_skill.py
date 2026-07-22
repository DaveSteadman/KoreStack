# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Datasets skill wrapper for KoreAgent.
#
# Re-exports the dataset phase-1 functions from app/datasets.py so they are available through the
# skill catalog and normal tool-dispatch pipeline.
# ====================================================================================================

from datasets_pkg.models import dataset_delete
from datasets_pkg.filtering import dataset_drop_where
from datasets_pkg.full_text import dataset_expand_full_text
from datasets_pkg.filtering import dataset_filter
from datasets_pkg.models import dataset_get
from datasets_pkg.models import dataset_inspect
from datasets_pkg.models import dataset_list
from datasets_pkg.models import dataset_rename
from datasets_pkg.models import dataset_save
from datasets_pkg.export import dataset_write_koredoc


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
