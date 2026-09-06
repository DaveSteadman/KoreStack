# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Declares the LLM-callable Working Data skill surface by re-exporting the stable functions from
# working_data. This module intentionally contains no storage or routing policy of its own.
#
# Public API:
#   - __all__ -- every re-export whose name begins with working_data_.
#
# Related module:
#   - working_data.py -- public Working Data facade and compatibility boundary.
# ====================================================================================================
"""LLM-callable Working Data functions."""


# ====================================================================================================
# MARK: WORKING DATA TOOL EXPORTS
# ====================================================================================================

from working_data import working_data_clear
from working_data import working_data_delete
from working_data import working_data_drop_where
from working_data import working_data_expand_full_text
from working_data import working_data_fetch_full_text
from working_data import working_data_export
from working_data import working_data_filter
from working_data import working_data_get
from working_data import working_data_inspect
from working_data import working_data_list
from working_data import working_data_peek
from working_data import working_data_query
from working_data import working_data_rank
from working_data import working_data_rename
from working_data import working_data_save
from working_data import working_data_search
from working_data import working_data_select

# ====================================================================================================
# MARK: EXPORTED SKILL CONTRACT
# ====================================================================================================
__all__ = [name for name in globals() if name.startswith("working_data_")]
