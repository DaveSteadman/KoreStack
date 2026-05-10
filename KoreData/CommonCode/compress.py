# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Zlib compression helpers for KoreData content storage.
#
# Provides compress() and decompress() for storing article/chunk/document content
# as compressed bytes in SQLite BLOBs.  decompress() handles legacy uncompressed
# strings transparently so old rows can be read without migration.
#
# Related modules:
#   - KoreReference/app/database.py  -- stores article bodies compressed
#   - KoreRAG/app/database.py        -- stores chunk content compressed
#   - KoreDocs/app/korefile.py       -- same pattern for KoreFile content
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
        return blob  # legacy uncompressed row
    return zlib.decompress(blob).decode("utf-8")
