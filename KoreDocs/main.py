"""Root launcher for KoreDocs.

Keeps startup consistent with the other repos so KoreDocs can be started with:

    python ./main.py
"""

from __future__ import annotations

from server import main


if __name__ == '__main__':
    raise SystemExit(main())