from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Context helpers for KoreCommon/SlashCommands.
# Provides the focused helpers and module-level behaviour grouped into this file.
# MARK: FUNCTIONS
# Primary types: SlashCommandContext.
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

from dataclasses import dataclass
from typing import Callable


@dataclass
class SlashCommandContext:
    output: Callable[[str, str], None]
