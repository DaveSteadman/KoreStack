# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Root launcher for KoreDocs.
#
# Entry point that keeps startup consistent with the rest of the suite.
# Run with:  python ./main.py
#
# Related modules:
#   - app/server.py  -- main() starts the FastAPI app under uvicorn using the suite config port
# ====================================================================================================

from __future__ import annotations

import sys
from pathlib import Path

_SUITE_ROOT = Path(__file__).resolve().parents[1]
if str(_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUITE_ROOT))

from app.api.app import main


if __name__ == '__main__':
    raise SystemExit(main())
