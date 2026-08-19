# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared helpers for creating isolated prompt sessions.
#
# Provides make_task_session(), used by KoreConv to create an isolated session context.
#
# Related modules:
#   - input_layer/koreconv_input.py                  -- calls make_task_session
#   - orchestration.py                               -- orchestrate_prompt
# MARK: FUNCTIONS
# Function inventory:
# - make_task_session: Implements the make task session operation for this module.
# ====================================================================================================
from pathlib import Path

from agent.orchestration.engine import ConversationHistory
from agent.orchestration.engine import SessionContext


# ====================================================================================================
# MARK: SESSION FACTORY
# ====================================================================================================

def make_task_session(
    session_id: str,
    persist_path: Path | None,
    max_turns: int = 10,
) -> tuple[ConversationHistory, SessionContext]:
    history = ConversationHistory(max_turns=max_turns)
    ctx = SessionContext(session_id=session_id, persist_path=persist_path)
    return history, ctx
