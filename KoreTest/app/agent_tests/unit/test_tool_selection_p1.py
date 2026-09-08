# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Focused regression tests for P1 tool-selection performance and usability behaviour.
# ====================================================================================================

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[4]
APP_ROOT  = REPO_ROOT / "KoreAgent" / "app"

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from sessions import tool_state
import skills_catalog_builder
from system_skills.ToolSelection import tool_selection_skill


class ToolSelectionP1Tests(unittest.TestCase):
    def test_tool_schema_revision_ignores_fifo_reordering(self) -> None:
        session_id   = "tool_schema_revision_test"
        conversation = {"tools_active": ["tool_one", "tool_two"]}
        tool_state.clear_session_tools_active(session_id)

        initial_revision = tool_state.get_tool_schema_revision(session_id, conversation)
        tool_state.note_tool_used("tool_two", session_id=session_id, conversation_entry=conversation)
        reordered_revision = tool_state.get_tool_schema_revision(session_id, conversation)
        tool_state.promote_selected_tools(
            ["tool_three"],
            session_id         = session_id,
            conversation_entry = conversation,
            persist            = False,
        )
        changed_revision = tool_state.get_tool_schema_revision(session_id, conversation)

        self.assertEqual(reordered_revision, initial_revision)
        self.assertGreater(changed_revision, reordered_revision)

    def test_skills_search_returns_focused_exact_matches(self) -> None:
        payload = {
            "skills": [
                {
                    "skill_name":      "file_access",
                    "purpose":         "Read, write, and organize workspace files.",
                    "triggers":        ["files", "folders"],
                    "functions":       ["file_read(path: str)", "file_write(path: str, content: str)"],
                    "is_system_skill": False,
                },
                {
                    "skill_name":      "web_research",
                    "purpose":         "Search public web sources.",
                    "triggers":        ["research"],
                    "functions":       ["search_web(query: str)"],
                    "is_system_skill": False,
                },
            ]
        }
        registered_system_skill = {
            "name":                  "system_skills",
            "selection_description": "Built-in tools.",
            "purpose":               "Built-in tools.",
            "tools":                 [{"name": "skills_search"}],
        }
        with (
            patch.object(tool_selection_skill, "load_skills_payload", return_value=payload),
            patch.object(tool_selection_skill.skill_manager, "list_skills", return_value=[registered_system_skill]),
        ):
            result = tool_selection_skill.skills_search("workspace files")

        self.assertEqual(result["skills"][0]["name"], "file_access")
        self.assertEqual(result["skills"][0]["tool_count"], 2)
        self.assertNotIn("system_skills", [item["name"] for item in result["skills"]])

    def test_catalog_freshness_check_is_cached_between_tool_selections(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "skills_catalog.json"
            catalog_path.write_text('{"skills": []}', encoding="utf-8")
            skills_catalog_builder._FRESHNESS_CHECK_CACHE.clear()
            with patch.object(skills_catalog_builder, "find_skill_files", return_value=[]) as find_skill_files:
                skills_catalog_builder._rebuild_skills_catalog_if_stale(catalog_path)
                skills_catalog_builder._rebuild_skills_catalog_if_stale(catalog_path)

        self.assertEqual(find_skill_files.call_count, 1)


if __name__ == "__main__":
    unittest.main()
