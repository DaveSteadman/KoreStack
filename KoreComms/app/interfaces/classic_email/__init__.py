# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
#   init   module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

"""Classic IMAP/POP3 and SMTP email interface for KoreComms."""

from app.interfaces.classic_email.adapter import ClassicEmailInterface

__all__ = ["ClassicEmailInterface"]
