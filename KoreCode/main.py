# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Root launcher for KoreCode.
#
# Entry point that keeps startup consistent with the rest of the suite.
# Run with:  python ./main.py
#
# Related modules:
#   - app/server.py  -- main() brings up the FastAPI app under uvicorn
# ====================================================================================================
from __future__ import annotations

from app.server import main


if __name__ == '__main__':
    raise SystemExit(main())