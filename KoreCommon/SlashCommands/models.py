from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Models for KoreCommon/SlashCommands.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

from dataclasses import dataclass
from typing import Callable

from .context import SlashCommandContext


SlashHandler = Callable[[str, SlashCommandContext], None]
SlashCompleter = Callable[[str, SlashCommandContext], list[dict]]


@dataclass(slots=True)
class SlashCommandDefinition:
    name:        str
    description: str
    handler:     SlashHandler
    usage:       str = ""
    aliases:     tuple[str, ...] = ()
    completer:   SlashCompleter | None = None

    def all_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)
