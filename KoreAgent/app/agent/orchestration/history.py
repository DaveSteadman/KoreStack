# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# In-memory user/assistant turn-pair container used during orchestration. It enforces pair alignment
# at mutation time and optionally trims whole historical turns, so prompt assembly never receives a
# dangling user or assistant message. Persistent conversation storage remains outside this module.
# MARK: FUNCTIONS
# Primary types: ConversationHistory.
# Function inventory:
# - __init__: Implements the   init   operation for this module.
# - add: Implements the add operation for this module.
# - clear: Clears this module's primary operation.
# - as_list: Implements the as list operation for this module.
# - __len__: Implements the   len   operation for this module.
# - __bool__: Implements the   bool   operation for this module.
# ====================================================================================================


class ConversationHistory:
    """Unbounded or capped store of user / assistant turn pairs."""

    def __init__(self, max_turns: int = 0):
        self._max_turns = max_turns
        self._turns: list[dict] = []

    def add(self, user: str, assistant: str) -> None:
        if len(self._turns) % 2 != 0:
            raise RuntimeError(
                f"ConversationHistory is misaligned - expected even turn count, "
                f"got {len(self._turns)}. A prior add() call is missing its assistant response."
            )
        self._turns.append({"role": "user", "content": user})
        self._turns.append({"role": "assistant", "content": assistant})
        if self._max_turns > 0:
            cap = self._max_turns * 2
            if len(self._turns) > cap:
                self._turns = self._turns[-cap:]

    def clear(self) -> None:
        self._turns = []

    def as_list(self) -> list[dict]:
        return list(self._turns)

    def __len__(self) -> int:
        return len(self._turns) // 2

    def __bool__(self) -> bool:
        return bool(self._turns)


__all__ = ["ConversationHistory"]
