# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Backwards-compatible KoreData shim.
#
# The shared suite path and KoreData config helpers now live in
# KoreCommon/suite_paths.py so non-KoreData services can import the same logic.
# KoreData sub-services still import this local module for launch-time
# compatibility.
# ====================================================================================================

import sys
from pathlib import Path

_SUITE_ROOT = Path(__file__).resolve().parents[2]
if str(_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUITE_ROOT))

from KoreCommon.suite_paths import _DATA_SUBSERVICE_OFFSETS
from KoreCommon.suite_paths import get_koredata_dir
from KoreCommon.suite_paths import get_required_local_datacontrol_dir
from KoreCommon.suite_paths import get_suite_datacontrol_dir
from KoreCommon.suite_paths import get_suite_dataroot_dir
from KoreCommon.suite_paths import get_suite_datauser_dir
from KoreCommon.suite_paths import get_suite_root
from KoreCommon.suite_paths import get_suite_urls_map
from KoreCommon.suite_paths import load_config
