"""Legacy launcher for KoreChat.

This shim preserves older paths while the live service implementation now resides
at the suite-level KoreChat/ folder.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    service_dir = Path(__file__).resolve().parents[3] / "KoreChat"
    os.chdir(service_dir)
    sys.path.insert(0, str(service_dir))
    runpy.run_path(str(service_dir / "main.py"), run_name="__main__")
