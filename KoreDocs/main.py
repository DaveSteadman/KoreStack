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

from app.server import main


if __name__ == '__main__':
    raise SystemExit(main())
