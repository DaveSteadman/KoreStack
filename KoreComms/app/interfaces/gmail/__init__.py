# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Package marker for KoreComms/app/interfaces/gmail.
# Keeps imports and package boundaries explicit for this package.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

from app.interfaces.gmail.adapter import GmailInterface
from app.interfaces.gmail.oauth import build_auth_url, exchange_code

__all__ = ["GmailInterface", "build_auth_url", "exchange_code"]