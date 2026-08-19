# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
#   init   module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

"""SFTP file output interface for KoreComms."""

from app.interfaces.sftp_file.adapter import SftpFileInterface

__all__ = ["SftpFileInterface"]
