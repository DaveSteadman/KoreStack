# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Package marker for KoreCommon/SlashCommands.
# Keeps imports and package boundaries explicit for this package.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

from .context import SlashCommandContext
from .dispatcher import handle_slash_command
from .dispatcher import write_help_lines
from .models import SlashCommandDefinition
from .registry import parse_slash_text
from .registry import SlashCommandRegistry

__all__ = [
    "SlashCommandContext",
    "SlashCommandDefinition",
    "SlashCommandRegistry",
    "handle_slash_command",
    "parse_slash_text",
    "write_help_lines",
]
