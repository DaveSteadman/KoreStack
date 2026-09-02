# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared slash-command dispatch for every Agent prompt source.
# MARK: FUNCTIONS
# Function inventory:
# - process_slash_prompt: Implements the process slash prompt operation for this module.
# - _output: Implements the  output operation for this module.
# ====================================================================================================
from input_layer.slash_command_context import SlashCommandContext
from input_layer.slash_commands import handle as handle_slash


# ----------------------------------------------------------------------------------------------------
def process_slash_prompt(
    prompt:                  str,
    *,
    config,
    output,
    clear_history,
    session_context,
    session_id:              str,
    chat_name:               str | None = None,
    switch_session           = None,
    rename_session           = None,
    delete_session_state     = None,
    compress_history         = None,
) -> str:
    """Dispatch a slash prompt and return the complete human-readable response."""
    output_lines: list[str] = []

    def _output(text: str, level: str = "info") -> None:
        output_lines.append(text)
        output(text, level)

    context = SlashCommandContext(
        config               = config,
        output               = _output,
        clear_history        = clear_history,
        session_context      = session_context,
        session_id           = session_id,
        chat_name            = chat_name,
        switch_session       = switch_session,
        rename_session       = rename_session,
        delete_session_state = delete_session_state,
        compress_history     = compress_history,
    )
    handled = handle_slash(prompt, context)
    return "\n".join(output_lines) if output_lines else ("(done)" if handled else f"Unknown command: {prompt.split()[0]}")
