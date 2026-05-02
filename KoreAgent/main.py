"""Root launcher for KoreAgent.

Keeps startup consistent with the other suite processes so KoreAgent can be
started with:

    python ./main.py
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    app_dir = Path(__file__).parent / "app"
    os.chdir(app_dir)
    sys.path.insert(0, str(app_dir))
    runpy.run_path(str(app_dir / "main.py"), run_name="__main__")