from __future__ import annotations
# MARK: OVERVIEW
# ====================================================================================================
# Root entry point for the entire KoreStack suite.
#
# Delegates to KoreStack/main.py via runpy so the full suite can be started from the
# workspace root with:  python ./main.py
#
# Related modules:
#   - KoreStak/main.py  -- orchestrates launching all services and the dashboard
# ====================================================================================================

import runpy
from pathlib import Path


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "KoreStack" / "main.py"), run_name="__main__")
