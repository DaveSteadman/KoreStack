# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Package marker for KoreDocs/app.
# Keeps imports and package boundaries explicit for this package.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

from .documents.korefile import service as korefile

__all__ = ["korefile"]
