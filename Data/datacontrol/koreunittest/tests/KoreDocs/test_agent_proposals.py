from __future__ import annotations

import os
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(os.environ["KORESTACK_ROOT"])
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from KoreDocs.app.documents.korefile import service as korefile
from KoreDocs.app.agent_proposals import koredocs_proposal_create
from KoreDocs.app.api.routes_skills import _handlers


class KoreDocsAgentProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name) / "datauser"
        korefile.configure(self.root)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_proposal_is_hidden_until_explicitly_accepted(self) -> None:
        created = korefile.create_file(1, "proposal.koredoc", "# Original")
        proposal = koredocs_proposal_create(
            file_id=created["id"],
            expected_revision=created["revision"],
            proposed_content="# Proposed",
            summary="Clarify the introduction",
        )

        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(korefile.get_file(created["id"])["body_content"], "# Original")
        self.assertEqual([item["id"] for item in korefile.list_proposals(created["id"])], [proposal["id"]])
        self.assertEqual([item["id"] for item in korefile.list_files()], [created["id"]])

        accepted = korefile.apply_proposal(proposal["id"])

        self.assertEqual(accepted["status"], "applied")
        self.assertEqual(korefile.get_file(created["id"])["body_content"], "# Proposed")
        self.assertEqual(korefile.list_proposals(created["id"]), [])

    def test_proposal_tool_is_registered_through_native_json_skills(self) -> None:
        manifest_path = REPO_ROOT / "KoreDocs" / "skill_registration.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tools = {
            tool["name"]: tool
            for skill in manifest["skills"]
            for tool in skill["tools"]
        }

        self.assertIn("koredocs_proposal_create", _handlers())
        self.assertIn("koredocs_proposal_create", tools)
        self.assertEqual(
            tools["koredocs_proposal_create"]["parameters"]["required"],
            ["file_id", "expected_revision", "proposed_content", "summary"],
        )
        self.assertNotIn("koredocs_proposal_apply", tools)

    def test_stale_proposal_cannot_replace_newer_content(self) -> None:
        created = korefile.create_file(1, "stale-proposal.koredoc", "# Original")
        korefile.update_file(created["id"], "# Updated elsewhere")

        with self.assertRaises(korefile.ConflictError):
            koredocs_proposal_create(
                file_id=created["id"],
                expected_revision=created["revision"],
                proposed_content="# Proposed",
                summary="Stale change",
            )

        self.assertEqual(korefile.get_file(created["id"])["body_content"], "# Updated elsewhere")

    def test_rejected_proposal_does_not_change_source_file(self) -> None:
        created = korefile.create_file(1, "rejected-proposal.koredoc", "# Original")
        proposal = koredocs_proposal_create(
            file_id=created["id"],
            expected_revision=created["revision"],
            proposed_content="# Proposed",
            summary="Not needed",
        )

        rejected = korefile.reject_proposal(proposal["id"])

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(korefile.get_file(created["id"])["body_content"], "# Original")
        self.assertEqual(korefile.list_proposals(created["id"]), [])


if __name__ == "__main__":
    unittest.main()
