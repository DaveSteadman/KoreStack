# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# main module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

from __future__ import annotations

from app.server import main


if __name__ == "__main__":
    raise SystemExit(main())
