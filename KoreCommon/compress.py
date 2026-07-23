from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared zlib compression helpers for text content stored in SQLite BLOBs.
# ====================================================================================================

import zlib
from typing import Optional


def compress(text: Optional[str]) -> Optional[bytes]:
    if not text:
        return None
    return zlib.compress(text.encode("utf-8"), level=6)


def decompress(blob: Optional[bytes]) -> Optional[str]:
    if not blob:
        return None
    if isinstance(blob, str):
        return blob
    return zlib.decompress(blob).decode("utf-8")


__all__ = ["compress", "decompress"]
