# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# test model resolution module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory:
# - test_unique_non_token_substring_resolves_model: Implements the test unique non token substring resolves model operation for this module.
# - test_ambiguous_substring_does_not_select_a_model: Implements the test ambiguous substring does not select a model operation for this module.
# - test_numeric_substring_does_not_match_a_larger_number: Implements the test numeric substring does not match a larger number operation for this module.
# ====================================================================================================

from llm_client_openai import resolve_model_name


_MODELS = [
    "nemotron-3.5-lightning:latest",
    "nemotron-3-super:120b",
    "gpt-oss:20b",
    "gpt-oss:120b",
]


def test_unique_non_token_substring_resolves_model() -> None:
    assert resolve_model_name("light", _MODELS) == "nemotron-3.5-lightning:latest"


def test_ambiguous_substring_does_not_select_a_model() -> None:
    assert resolve_model_name("nemotron", _MODELS) is None


def test_numeric_substring_does_not_match_a_larger_number() -> None:
    assert resolve_model_name("20b", _MODELS) == "gpt-oss:20b"
