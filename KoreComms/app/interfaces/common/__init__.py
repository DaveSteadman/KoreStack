"""Shared interface abstractions and registry helpers."""

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Package marker for KoreComms/app/interfaces/common.
# Keeps imports and package boundaries explicit for this package.
# ====================================================================================================

from app.interfaces.common.base import BaseInterface
from app.interfaces.common.registry import REGISTRY, build_adapter

__all__ = ["BaseInterface", "REGISTRY", "build_adapter"]