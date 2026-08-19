# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# test tool sets module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Primary types: ToolSetTests.
# Function inventory:
# - test_tool_set_builder_splits_koredocs_into_file_and_document_subsystems: Implements the test tool set builder splits koredocs into file and document subsystems operation for this module.
# - test_active_tool_queue_evicts_oldest_and_reactivation_moves_to_newest: Implements the test active tool queue evicts oldest and reactivation moves to newest operation for this module.
# - test_normalise_tool_sets_rejects_groups_larger_than_the_active_set_half_cap: Implements the test normalise tool sets rejects groups larger than the active set half cap operation for this module.
# - test_save_and_resolve_tool_sets_uses_only_currently_available_tools: Implements the test save and resolve tool sets uses only currently available tools operation for this module.
# - test_related_tool_set_reactivates_the_smallest_matching_group: Implements the test related tool set reactivates the smallest matching group operation for this module.
# - test_relevant_tool_sets_require_a_distinctive_domain_term: Implements the test relevant tool sets require a distinctive domain term operation for this module.
# - test_reevaluation_persists_complete_programmatic_grouping: Implements the test reevaluation persists complete programmatic grouping operation for this module.
# - output: Implements the output operation for this module.
# - fake_save: Implements the fake save operation for this module.
# ====================================================================================================

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[4]
APP_ROOT  = REPO_ROOT / "KoreAgent" / "app"

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from sessions import tool_sets
from sessions import tool_state
from input_layer import slash_commands


class ToolSetTests(unittest.TestCase):
    def test_tool_set_builder_splits_koredocs_into_file_and_document_subsystems(self) -> None:
        inventory = [
            {"name": "koredocs_doc_create",                 "source": "KoreDocs"},
            {"name": "koredocs_doc_create_from_scratchpad", "source": "KoreDocs"},
            {"name": "koredocs_doc_markdown_append",        "source": "KoreDocs"},
            {"name": "koredocs_doc_outline_get",            "source": "KoreDocs"},
            {"name": "koredocs_doc_section_insert",         "source": "KoreDocs"},
            {"name": "koredocs_doc_section_read",           "source": "KoreDocs"},
            {"name": "koredocs_doc_section_replace",        "source": "KoreDocs"},
            {"name": "koredocs_file_create",                "source": "KoreDocs"},
            {"name": "koredocs_file_delete",                "source": "KoreDocs"},
            {"name": "koredocs_file_get",                   "source": "KoreDocs"},
            {"name": "koredocs_file_update",                "source": "KoreDocs"},
            {"name": "koredocs_files_list",                 "source": "KoreDocs"},
            {"name": "koredocs_files_search",               "source": "KoreDocs"},
            {"name": "koredocs_folder_create",              "source": "KoreDocs"},
            {"name": "koredocs_folder_structure_get",       "source": "KoreDocs"},
            {"name": "koredocs_folders_list",               "source": "KoreDocs"},
            {"name": "koredocs_file_history_get",           "source": "KoreDocs"},
        ]

        groups = slash_commands._build_tool_sets(inventory)["sets"]
        by_name = {group["name"]: set(group["tools"]) for group in groups}

        self.assertIn("koredocs_doc_create", by_name["kore_documents"])
        self.assertIn("koredocs_doc_create_from_scratchpad", by_name["kore_documents"])
        self.assertIn("koredocs_file_create", by_name["kore_files"])
        self.assertIn("koredocs_folder_create", by_name["kore_files"])

    def test_active_tool_queue_evicts_oldest_and_reactivation_moves_to_newest(self) -> None:
        session_id = "tool_set_fifo_test"
        current = [f"tool_{index}" for index in range(tool_state.MAX_ACTIVE_TOOLS)]
        conversation = {"tools_active": list(current)}
        tool_state.clear_session_tools_active(session_id)

        added = tool_state.promote_selected_tools(
            ["tool_new"],
            session_id=session_id,
            conversation_entry=conversation,
            persist=False,
        )
        reactivated = tool_state.promote_selected_tools(
            ["tool_4"],
            session_id=session_id,
            conversation_entry=conversation,
            persist=False,
        )

        self.assertEqual(added["evicted"], ["tool_0"])
        self.assertEqual(added["active_tools"][-1], "tool_new")
        self.assertEqual(reactivated["active_tools"][-1], "tool_4")
        self.assertEqual(len(reactivated["active_tools"]), tool_state.MAX_ACTIVE_TOOLS)

    def test_normalise_tool_sets_rejects_groups_larger_than_the_active_set_half_cap(self) -> None:
        tools = [f"tool_{index}" for index in range(20)]
        result = tool_sets.normalise_tool_sets(
            {
                "sets": [
                    {
                        "name": "research_tools",
                        "description": "Research and retrieve data.",
                        "tools": tools + ["missing_tool", "tools_catalog_list"],
                    }
                ]
            },
            known_tool_names=set(tools),
        )

        self.assertEqual(result, [])

    def test_save_and_resolve_tool_sets_uses_only_currently_available_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ToolSets.json"
            with patch.object(tool_sets, "get_tool_sets_path", return_value=path):
                tool_sets.save_tool_sets(
                    {
                        "sets": [
                            {
                                "name": "data_work",
                                "description": "Search and inspect data.",
                                "tools": ["search_data", "inspect_data"],
                            }
                        ]
                    },
                    known_tool_names={"search_data", "inspect_data"},
                )
                stored = json.loads(path.read_text(encoding="utf-8"))
                resolved = tool_sets.resolve_tool_sets(
                    ["data_work"],
                    known_tool_names={"inspect_data"},
                )

        self.assertEqual(stored["sets"][0]["name"], "data_work")
        self.assertEqual(resolved["selected_sets"], ["data_work"])
        self.assertEqual(resolved["tool_names"], ["inspect_data"])
        self.assertEqual(resolved["unavailable_tools"], ["search_data"])

    def test_related_tool_set_reactivates_the_smallest_matching_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ToolSets.json"
            path.write_text(
                json.dumps({
                    "sets": [
                        {
                            "name": "file_access",
                            "description": "File and folder operations.",
                            "tools": ["file_read", "file_write", "folder_create"],
                        },
                        {
                            "name": "exports",
                            "description": "Export operations.",
                            "tools": ["file_write", "export_report", "publish_report", "notify_team"],
                        },
                    ],
                }),
                encoding="utf-8",
            )
            with patch.object(tool_sets, "get_tool_sets_path", return_value=path):
                related = tool_sets.related_tool_set(
                    "file_write",
                    known_tool_names={"file_read", "file_write", "folder_create", "export_report", "publish_report", "notify_team"},
                )

        self.assertEqual(related, ["file_read", "file_write", "folder_create"])

    def test_relevant_tool_sets_require_a_distinctive_domain_term(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ToolSets.json"
            path.write_text(
                json.dumps({
                    "sets": [
                        {
                            "name": "file_access",
                            "description": "File and folder operations.",
                            "tools": ["file_read", "file_write", "folder_create"],
                        },
                        {
                            "name": "system_info",
                            "description": "Operating-system and runtime information.",
                            "tools": ["get_system_info_dict"],
                        },
                    ],
                }),
                encoding="utf-8",
            )
            with patch.object(tool_sets, "get_tool_sets_path", return_value=path):
                selected = tool_sets.relevant_tool_sets(
                    "write the system information to a file",
                    known_tool_names={"file_read", "file_write", "folder_create", "get_system_info_dict"},
                )
                generic_only = tool_sets.relevant_tool_sets(
                    "write the requested output",
                    known_tool_names={"file_read", "file_write", "folder_create", "get_system_info_dict"},
                )

        self.assertEqual([item["name"] for item in selected], ["system_info", "file_access"])
        self.assertEqual(generic_only, [])

    def test_reevaluation_persists_complete_programmatic_grouping(self) -> None:
        inventory = [
            {"name": "tool_one",   "description": "First tool.",   "source": "Test"},
            {"name": "tool_two",   "description": "Second tool.",  "source": "Test"},
            {"name": "tool_three", "description": "Third tool.",   "source": "Test"},
        ]
        captured: dict = {}

        class Context:
            @staticmethod
            def output(*_args) -> None:
                return None

        def fake_save(tool_sets: object, *, known_tool_names: set[str]) -> dict:
            captured["sets"] = tool_sets["sets"]
            return {"path": "ToolSets.json", "sets": tool_sets["sets"]}

        with patch.object(slash_commands, "save_tool_sets", side_effect=fake_save):
            slash_commands._reevaluate_tool_groups(
                Context(),
                inventory,
                {item["name"] for item in inventory},
            )

        self.assertEqual({tool for group in captured["sets"] for tool in group["tools"]}, {
            "tool_one", "tool_two", "tool_three",
        })
        self.assertEqual(captured["sets"][0]["name"], "test")


if __name__ == "__main__":
    unittest.main()
