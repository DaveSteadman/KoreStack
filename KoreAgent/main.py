"""Root launcher for KoreAgent.

Keeps startup consistent with the other suite processes so KoreAgent can be
started with:

    python ./main.py
"""

from __future__ import annotations

import runpy
from pathlib import Path


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).parent / "code" / "main.py"), run_name="__main__")