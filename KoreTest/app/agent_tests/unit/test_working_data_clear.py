# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression tests for session-scoped bulk scratchpad and dataset clearing.
# MARK: FUNCTIONS
# Primary types: WorkingDataClearTests.
# Function inventory:
# - test_scratchpad_clear_removes_every_key_in_the_session: Implements the test scratchpad clear removes every key in the session operation for this module.
# - test_dataset_clear_removes_runtime_and_spillover_datasets: Implements the test dataset clear removes runtime and spillover datasets operation for this module.
# ====================================================================================================

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(__file__).resolve().parents[4] / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import datasets_pkg.service as dataset_service
from scratchpad import get_store
from scratchpad import scratchpad_clear
from scratchpad import scratchpad_save


class WorkingDataClearTests(unittest.TestCase):
    def test_scratchpad_clear_removes_every_key_in_the_session(self) -> None:
        session_id = "clear_scratchpad_test"
        scratchpad_save("first", "one", session_id=session_id)
        scratchpad_save("second", "two", session_id=session_id)

        result = scratchpad_clear(session_id=session_id)

        self.assertEqual(result, "Scratchpad cleared (2 key(s) removed).")
        self.assertEqual(get_store(session_id=session_id), {})

    def test_dataset_clear_removes_runtime_and_spillover_datasets(self) -> None:
        session_id = "clear_dataset_test"
        dataset_service._SESSION_DATASETS[session_id] = {
            "first":  {"dataset_id": "ds_first"},
            "second": {"dataset_id": "ds_second"},
        }

        with patch.object(dataset_service.datasets_store, "delete_session_datasets") as delete_spillover:
            result = dataset_service.dataset_clear(session_id=session_id)

        self.assertEqual(result, "Cleared 2 dataset(s).")
        self.assertEqual(dataset_service._SESSION_DATASETS[session_id], {})
        delete_spillover.assert_called_once_with(session_id)


if __name__ == "__main__":
    unittest.main()
