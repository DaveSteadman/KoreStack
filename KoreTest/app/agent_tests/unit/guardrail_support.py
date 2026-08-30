# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# guardrail support module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory:
# - load_test_skills_payload: Loads test skills payload for this module.
# - reset_guardrail_state: Implements the reset guardrail state operation for this module.
# ====================================================================================================

from pathlib import Path

from system_skills.WorkingData.collections import clear_session_datasets
from system_skills.WorkingData.collections import delete_session_datasets
from skills_catalog_builder import load_skills_payload
from working_data import working_data_clear as scratchpad_clear


DATASET_SESSION_IDS: tuple[str, ...] = (
    "dataset_test",
    "dataset_restore",
    "dataset_prompt",
    "dataset_filter",
    "dataset_auto",
    "dataset_paging",
    "dataset_export",
    "dataset_fulltext",
    "dataset_load_session",
    "kc_conv_701",
)


def load_test_skills_payload(code_dir: Path) -> dict:
    return load_skills_payload(code_dir / "system_skills" / "skills_catalog.json")


def reset_guardrail_state() -> None:
    scratchpad_clear()
    for session_id in DATASET_SESSION_IDS:
        delete_session_datasets(session_id)
        clear_session_datasets(session_id)
