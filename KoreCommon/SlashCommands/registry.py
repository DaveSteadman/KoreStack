from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Registry helpers for KoreCommon/SlashCommands.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

from dataclasses import replace

from .context import SlashCommandContext
from .models import SlashCommandDefinition


class SlashCommandRegistry:
    def __init__(self) -> None:
        self._definitions_by_name: dict[str, SlashCommandDefinition] = {}
        self._canonical_names: list[str] = []

    def clear(self) -> None:
        self._definitions_by_name.clear()
        self._canonical_names.clear()

    def register(self, definition: SlashCommandDefinition) -> None:
        canonical = self._normalize_name(definition.name)
        normalized = replace(
            definition,
            name    = canonical,
            aliases = tuple(self._normalize_name(item) for item in definition.aliases),
        )
        if canonical not in self._canonical_names:
            self._canonical_names.append(canonical)
        for name in normalized.all_names():
            self._definitions_by_name[name] = normalized

    def get(self, name: str) -> SlashCommandDefinition | None:
        return self._definitions_by_name.get(self._normalize_name(name))

    def definitions(self) -> list[SlashCommandDefinition]:
        return [
            self._definitions_by_name[name]
            for name in sorted(self._canonical_names)
            if name in self._definitions_by_name
        ]

    def dispatch(
        self,
        text: str,
        ctx: SlashCommandContext,
        *,
        unknown_message: str = "Unknown command. Type /help for available commands.",
    ) -> bool:
        command, arg, _ = parse_slash_text(text)
        if not command:
            return False
        definition = self.get(command)
        if definition is None:
            ctx.output(unknown_message.replace("{command}", command), "dim")
            return True
        definition.handler(arg, ctx)
        return True

    def complete(self, text: str, ctx: SlashCommandContext, *, limit: int = 12) -> list[dict]:
        command, arg, has_space = parse_slash_text(text)
        if not command:
            return []

        if not has_space:
            return self._complete_command_name(command, limit=limit)

        definition = self.get(command)
        if definition is None or definition.completer is None:
            return []

        seen: set[str] = set()
        items: list[dict] = []
        for item in definition.completer(arg, ctx) or []:
            entry = _normalize_completion_item(item, fallback_command=definition.name)
            if not entry:
                continue
            key = str(entry["value"]).lower()
            if key in seen:
                continue
            seen.add(key)
            items.append(entry)
            if len(items) >= limit:
                break
        return items

    def _complete_command_name(self, command_prefix: str, *, limit: int) -> list[dict]:
        prefix = self._normalize_name(command_prefix)
        items: list[dict] = []
        seen: set[str] = set()

        for definition in self.definitions():
            for name in definition.all_names():
                if prefix and not name.startswith(prefix):
                    continue
                value = f"{name} "
                key   = value.lower()
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    {
                        "value":       value,
                        "label":       name,
                        "description": definition.description,
                        "kind":        "command",
                    }
                )
                if len(items) >= limit:
                    return items
        return items

    @staticmethod
    def _normalize_name(name: str) -> str:
        text = str(name or "").strip().lower()
        if not text:
            return ""
        return text if text.startswith("/") else f"/{text}"


def parse_slash_text(text: str) -> tuple[str, str, bool]:
    raw = str(text or "").lstrip()
    if not raw.startswith("/"):
        return "", "", False

    space_idx = raw.find(" ")
    if space_idx < 0:
        return raw.lower(), "", False

    command = raw[:space_idx].lower()
    arg     = raw[space_idx + 1:]
    return command, arg, True


def _normalize_completion_item(item: dict, *, fallback_command: str) -> dict | None:
    value = str(item.get("value") or "").strip()
    if not value:
        return None
    label       = str(item.get("label") or value).strip()
    description = str(item.get("description") or "").strip()
    kind        = str(item.get("kind") or "argument").strip() or "argument"
    if value.startswith("/"):
        final_value = value
    elif fallback_command:
        final_value = f"{fallback_command} {value}".rstrip() + " "
    else:
        final_value = value
    if not final_value.endswith(" "):
        final_value += " "
    return {
        "value":       final_value,
        "label":       label,
        "description": description,
        "kind":        kind,
    }
