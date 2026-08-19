# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# test context window module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory:
# - test_short_prompt_uses_minimum_working_context: Implements the test short prompt uses minimum working context operation for this module.
# - test_context_grows_with_payload_but_never_exceeds_maximum: Implements the test context grows with payload but never exceeds maximum operation for this module.
# ====================================================================================================

from agent.orchestration.context_window import choose_context_window


def test_short_prompt_uses_minimum_working_context() -> None:
    assert choose_context_window(100_000, [{"role": "user", "content": "hi"}]) == 8_192


def test_context_grows_with_payload_but_never_exceeds_maximum() -> None:
    messages = [{"role": "user", "content": "x" * 70_000}]
    assert choose_context_window(100_000, messages) == 32_768
    assert choose_context_window(16_384, messages) == 16_384
