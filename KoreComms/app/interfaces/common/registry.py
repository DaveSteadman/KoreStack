# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Interface type registry for KoreComms.
#
# Maps the string type name (stored in interfaces.type) to the adapter class.
# To register a new interface type, add it to REGISTRY with a unique string key.
#
# Public API:
#   REGISTRY             -- dict[str, type[BaseInterface]]
#   build_adapter(row)   -- instantiates the adapter for a database interface row
#
# Related modules:
#   - app/interfaces/common/base.py       -- BaseInterface ABC
#   - app/interfaces/manual/adapter.py    -- manual channel adapter
#   - app/interfaces/discord/adapter.py   -- Discord bot adapter
#   - app/interfaces/gmail/adapter.py     -- Gmail OAuth adapter
#   - app/poller.py                       -- calls build_adapter for each enabled interface
# ====================================================================================================
from __future__ import annotations

from app.interfaces.common.base import BaseInterface
from app.interfaces.discord import DiscordInterface
from app.interfaces.gmail import GmailInterface
from app.interfaces.manual import ManualInterface

REGISTRY: dict[str, type[BaseInterface]] = {
    "manual": ManualInterface,
    "discord": DiscordInterface,
    "gmail": GmailInterface,
}


def build_adapter(row: dict) -> BaseInterface:
    """Construct the correct adapter from a database interfaces row."""
    cls = REGISTRY.get(row["type"])
    if cls is None:
        raise ValueError(f"Unknown interface type: {row['type']!r}")
    return cls(
        interface_id=row["id"],
        name=row["name"],
        config=row,
    )