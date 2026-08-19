# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# dbutil module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from KoreCommon.dbutil import compute_word_count
from KoreCommon.dbutil import fts_build_query

__all__ = ["compute_word_count", "fts_build_query"]
