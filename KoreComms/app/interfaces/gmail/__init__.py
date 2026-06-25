# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Package marker for KoreComms/app/interfaces/gmail.
# Keeps imports and package boundaries explicit for this package.
# ====================================================================================================

from app.interfaces.gmail.adapter import GmailInterface
from app.interfaces.gmail.oauth import build_auth_url, exchange_code

__all__ = ["GmailInterface", "build_auth_url", "exchange_code"]