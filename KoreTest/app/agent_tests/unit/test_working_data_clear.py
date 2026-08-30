# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression tests for session-scoped bulk Working Data clearing.
# MARK: FUNCTIONS
# Primary types: WorkingDataClearTests.
# Function inventory:
# - test_working_data_clear_removes_every_value_in_the_session: Implements the Working Data value clear operation.
# - test_working_data_clear_removes_collections: Implements the Working Data collection clear operation.
# ====================================================================================================

import sys
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[4] / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from working_data import build_persisted_working_data_payload
from working_data import working_data_clear
from working_data import working_data_get
from working_data import working_data_save


class WorkingDataClearTests(unittest.TestCase):
    def test_working_data_clear_removes_every_value_in_the_session(self) -> None:
        session_id = "clear_working_data_values_test"
        working_data_save("first", "one", session_id=session_id)
        working_data_save("second", "two", session_id=session_id)

        result = working_data_clear(session_id=session_id)

        self.assertEqual(result, "Cleared Working Data (2 value(s), 0 collection(s) removed).")
        self.assertIn("not found", working_data_get("first", session_id=session_id).lower())

    def test_working_data_clear_removes_collections(self) -> None:
        session_id = "clear_working_data_collections_test"
        working_data_save("first", [{"value": 1}], session_id=session_id)
        working_data_save("second", [{"value": 2}], session_id=session_id)

        result = working_data_clear(session_id=session_id)

        self.assertEqual(result, "Cleared Working Data (0 value(s), 2 collection(s) removed).")
        self.assertEqual(build_persisted_working_data_payload(session_id)["collections"], {})


if __name__ == "__main__":
    unittest.main()
