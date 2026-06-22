from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from KoreCommon.SlashCommands import SlashCommandContext as CommonSlashCommandContext


@dataclass
class KoreCodeSlashCommandContext(CommonSlashCommandContext):
    current_mode:              str
    workspace_context_enabled: bool
    thread_path:               str
    has_last_user_message:     bool = False
    actions:                   list[dict] = field(default_factory=list)

    def add_action(self, action_type: str, **payload) -> None:
        self.actions.append({"type": action_type, **payload})
