from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class SlashCommandContext:
    output: Callable[[str, str], None]
