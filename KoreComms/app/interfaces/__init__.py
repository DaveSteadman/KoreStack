"""Interface packages and shared registry exports."""

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Package marker for KoreComms/app/interfaces.
# Keeps imports and package boundaries explicit for this package.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

from app.interfaces.common import BaseInterface, REGISTRY, build_adapter

__all__ = ["BaseInterface", "REGISTRY", "build_adapter"]
