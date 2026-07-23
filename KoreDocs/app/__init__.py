# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Package marker for KoreDocs/app.
# Keeps imports and package boundaries explicit for this package.
# ====================================================================================================

from .documents.korefile import service as korefile

__all__ = ["korefile"]
