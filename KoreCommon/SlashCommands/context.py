from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Context helpers for KoreCommon/SlashCommands.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

from dataclasses import dataclass
from typing import Callable


@dataclass
class SlashCommandContext:
    output: Callable[[str, str], None]
