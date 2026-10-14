from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


APP_ROOT = Path(os.environ["KORESTACK_ROOT"]) / "KoreAgent" / "app"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from system_skills.WorkingData.collections import service


class FullTextWorkflowTests(unittest.TestCase):
    session_id = "full_text_workflow_test"

    def setUp(self) -> None:
        service.delete_session_datasets(self.session_id)

    def tearDown(self) -> None:
        service.delete_session_datasets(self.session_id)

    def test_fetch_full_text_instructs_agent_to_read_created_collection(self) -> None:
        service.dataset_save(
            "search_results",
            [{"title": "Example", "artifact_ref": "feed_entry|domain=news|id=1"}],
            session_id=self.session_id,
        )
        expanded_record = {
            "title": "Example",
            "artifact_ref": "feed_entry|domain=news|id=1",
            "page_text": "Retrieved full article text.",
        }

        with patch.object(service, "_expand_records_with_full_text", return_value=([expanded_record], [])):
            result = service.dataset_fetch_full_text("search_results", session_id=self.session_id)

        self.assertIn("Created dataset 'search_results_fulltext'", result)
        self.assertIn("working_data_get(name='search_results_fulltext')", result)
        self.assertIn("to read it before answering the user", result)


if __name__ == "__main__":
    unittest.main()
