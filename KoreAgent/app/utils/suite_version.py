# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# SUITE_VERSION constant loader for KoreAgent.
#
# Reads the SUITE_VERSION string from UIElements/assets/js/suiteMeta.js at import time
# via regex so the version is sourced from one authoritative location shared across all
# services.  The result is LRU-cached so the file is only read once per process.
#
# Public API:
#   get_suite_version()  -- returns the version string (e.g. "1.0.0")
#   SUITE_VERSION        -- module-level constant set from get_suite_version()
#
# Related modules:
#   - input_layer/server.py  -- exposes SUITE_VERSION via GET /version
# ====================================================================================================
import os
import re
from functools import lru_cache
from pathlib import Path


_SUITE_VERSION_RE = re.compile(r"export\s+const\s+SUITE_VERSION\s*=\s*['\"]([^'\"]+)['\"]\s*;")


def _suite_meta_path() -> Path:
    assets_dir = os.environ.get("KORE_UIELEMENTS_ASSETS_DIR")
    if assets_dir:
        return Path(assets_dir).resolve() / "js" / "suiteMeta.js"
    return Path(__file__).resolve().parents[3] / "UIElements" / "assets" / "js" / "suiteMeta.js"


@lru_cache(maxsize=1)
def get_suite_version() -> str:
    text = _suite_meta_path().read_text(encoding="utf-8")
    match = _SUITE_VERSION_RE.search(text)
    if not match:
        raise RuntimeError("Could not parse SUITE_VERSION from suiteMeta.js")
    return match.group(1)


SUITE_VERSION = get_suite_version()