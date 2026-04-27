"""Root launcher for KoreConversation.

Keeps startup consistent with the suite layout while the KoreConversation
implementation remains alongside KoreAgent's internal code tree.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    legacy_dir = Path(__file__).resolve().parent.parent / "KoreAgent" / "code" / "KoreConversation"
    os.chdir(legacy_dir)
    sys.path.insert(0, str(legacy_dir))
    runpy.run_path(
        str(
            Path(__file__).resolve().parent.parent
            / "KoreAgent"
            / "code"
            / "KoreConversation"
            / "main.py"
        ),
        run_name="__main__",
    )