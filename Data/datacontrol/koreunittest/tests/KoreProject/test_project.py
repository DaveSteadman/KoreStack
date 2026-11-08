from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(os.environ["KORESTACK_ROOT"])
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from KoreCommon.skill_registration import _build_registration
from KoreProject.app.server import create_app
from KoreProject.app.store import ProjectError, ProjectStore


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = create_app(self.root, register=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.store = self.app.state.store
        self.project = self.store.save("project", {"key": "TEST", "title": "Project"})
        self.generation = self.store.ticket_generation()

    def save(self, record_kind, **data):
        return self.store.save(record_kind, data, self.project["id"], generation=self.generation)

    def update(self, kind, record, **data):
        return self.store.save(kind, data, record_id=record["id"], expected_version=record["version"],
                               generation=self.generation)

    def board(self):
        board = self.save("board", title="Delivery")
        lanes = self.store.list("lane", self.project["id"])["items"]
        return board, {lane["title"]: lane for lane in lanes if lane["board_id"] == board["id"]}

    def test_persistence_and_separate_stores(self):
        requirement = self.save("statement", title="Requirement", body="Demonstrable")
        ticket = self.save("ticket", title="Implement it")
        restarted = ProjectStore(self.root)
        restarted.initialize()
        self.assertEqual(requirement, restarted.get("statement", requirement["id"]))
        self.assertEqual(ticket, restarted.get("ticket", ticket["id"]))
        with restarted.connection("statement") as conn:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("statements", names)
        self.assertNotIn("tickets", names)
        with restarted.connection("ticket") as conn:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn("statements", names)

    def test_ticket_references_persist_across_retries_archival_restart_and_reset(self):
        data = {"title": "First ticket"}
        first = self.store.save("ticket", data, self.project["id"],
                                request_id="first-ticket", generation=self.generation)
        self.assertEqual(first["ref"], "TEST-TKT-1")
        self.assertEqual(first, self.store.save("ticket", data, self.project["id"],
                                               request_id="first-ticket", generation=self.generation))
        self.update("ticket", first, archived=True)
        self.assertEqual(self.save("statement", title="Requirement")["ref"], "TEST-REQ-1")
        restarted = ProjectStore(self.root)
        restarted.initialize()
        self.assertEqual(restarted.save("ticket", data, self.project["id"],
                                       generation=self.generation)["ref"], "TEST-TKT-2")
        generation = restarted.reset_tickets("RESET ALL TICKETS", self.generation)["generation"]
        self.assertEqual(restarted.save("ticket", data, self.project["id"],
                                       generation=generation)["ref"], "TEST-TKT-3")
        other = self.store.save("project", {"key": "GRID", "title": "Another project"})
        self.assertEqual(restarted.save("ticket", data, other["id"], generation=generation)["ref"], "GRID-TKT-1")
        with restarted.connection("ticket", write=True) as conn:
            conn.execute("UPDATE ticket_counters SET next_number=101 WHERE project_id=?", (other["id"],))
        self.assertEqual(restarted.save("ticket", data, other["id"], generation=generation)["ref"], "GRID-TKT-101")

    def test_ticket_number_allocation_is_transactional(self):
        stores = [self.store, ProjectStore(self.root)]
        stores[1].initialize()

        def create(index):
            return stores[index % 2].save("ticket", {"title": f"Ticket {index}"}, self.project["id"],
                                          generation=self.generation)

        with ThreadPoolExecutor(max_workers=4) as executor:
            records = list(executor.map(create, range(12)))
        self.assertEqual({record["ref"] for record in records}, {f"TEST-TKT-{number}" for number in range(1, 13)})
        with patch.object(self.store, "_activity", side_effect=RuntimeError("Simulated write failure")):
            with self.assertRaisesRegex(RuntimeError, "Simulated write failure"):
                self.save("ticket", title="Rolled back")
        self.assertEqual(self.save("ticket", title="Next")["ref"], "TEST-TKT-13")

    def test_v1_ticket_migration_preserves_identity_relationships_history_and_retries(self):
        board, lanes = self.board()
        data = {"title": "Legacy parent", "lane_id": lanes["Planned"]["id"]}
        parent = self.store.save("ticket", data, self.project["id"],
                                 request_id="legacy-ticket", generation=self.generation)
        child = self.save("ticket", title="Archived child", parent_id=parent["id"], archived=True)
        requirement = self.save("statement", title="Requirement")
        link = self.store.link("ticket", parent["id"], "statement", requirement["id"], "addresses",
                               generation=self.generation)
        comment = self.store.comment("ticket", parent["id"], "Preserve comment", "operator", self.generation)
        other = self.store.save("project", {"key": "GRID-APP", "title": "Another project"})
        independent = self.store.save("ticket", {"title": "Independent"}, other["id"], generation=self.generation)
        legacy = {**parent, "ref": f"TEST-TIC-{parent['id'][:12]}"}
        with self.store.connection("ticket", write=True) as conn:
            for record, prefix in ((parent, "TEST"), (child, "TEST"), (independent, "GRID-APP")):
                conn.execute("UPDATE tickets SET ref=? WHERE id=?",
                             (f"{prefix}-TIC-{record['id'][:12]}", record["id"]))
            conn.execute("UPDATE requests SET response=? WHERE key='legacy-ticket'", (json.dumps(legacy),))
            self.store._activity(conn, parent["id"], "operator", "updated", legacy)
            conn.execute("DROP TABLE ticket_counters")
            conn.execute("PRAGMA user_version=1")
        restarted = ProjectStore(self.root)
        restarted.initialize()
        migrated = restarted.get("ticket", parent["id"])
        self.assertEqual(migrated["ref"], "TEST-TKT-1")
        self.assertEqual(migrated["version"], parent["version"] + 1)
        self.assertEqual(migrated["lane_id"], parent["lane_id"])
        self.assertEqual(restarted.get("ticket", child["id"])["parent_id"], parent["id"])
        self.assertEqual(restarted.get("ticket", child["id"])["ref"], "TEST-TKT-2")
        self.assertEqual(restarted.get("ticket", independent["id"])["ref"], "GRID-APP-TKT-1")
        self.assertEqual(restarted.save("ticket", data, self.project["id"], request_id="legacy-ticket",
                                       generation=self.generation), migrated)
        detail = restarted.detail("ticket", parent["id"])
        self.assertIn(link, detail["links"])
        self.assertIn(comment, detail["comments"])
        snapshots = [json.loads(entry["snapshot"]) for entry in detail["history"]]
        self.assertIn(legacy, snapshots)
        self.assertTrue(any(snapshot.get("previous_ref") == legacy["ref"] for snapshot in snapshots))
        self.assertEqual(restarted.get("board", board["id"]), board)
        self.assertEqual(restarted.get("statement", requirement["id"]), requirement)
        self.assertEqual(restarted.ticket_generation(), self.generation)
        restarted.initialize()
        self.assertEqual(restarted.get("ticket", parent["id"]), migrated)
        self.assertEqual(restarted.save("ticket", {"title": "Next"}, self.project["id"],
                                       generation=self.generation)["ref"], "TEST-TKT-3")

    def test_requirement_references_persist_without_reusing_numbers(self):
        data = {"title": "First requirement"}
        first = self.store.save("statement", data, self.project["id"], request_id="first-requirement")
        self.assertEqual(first["ref"], "TEST-REQ-1")
        self.assertEqual(first, self.store.save("statement", data, self.project["id"], request_id="first-requirement"))
        design = self.save("statement", title="Design", kind="design")
        self.assertEqual(design["ref"], "TEST-DES-1")
        self.assertEqual(self.save("statement", title="Second")["ref"], "TEST-REQ-2")
        self.update("statement", first, archived=True)
        restarted = ProjectStore(self.root)
        restarted.initialize()
        restarted.reset_tickets("RESET ALL TICKETS", self.generation)
        self.assertEqual(restarted.save("statement", {"title": "Third"}, self.project["id"])["ref"], "TEST-REQ-3")
        other = self.store.save("project", {"key": "GRID", "title": "Another project"})
        self.assertEqual(self.store.save("statement", data, other["id"])["ref"], "GRID-REQ-1")

    def test_statement_number_allocation_is_transactional(self):
        stores = [self.store, ProjectStore(self.root)]
        stores[1].initialize()

        for kind, prefix in (("requirement", "REQ"), ("design", "DES")):
            with self.subTest(kind=kind):
                def create(index):
                    return stores[index % 2].save("statement", {"title": f"Statement {index}", "kind": kind}, self.project["id"])

                with ThreadPoolExecutor(max_workers=4) as executor:
                    records = list(executor.map(create, range(12)))
                self.assertEqual({record["ref"] for record in records}, {f"TEST-{prefix}-{number}" for number in range(1, 13)})
                with patch.object(self.store, "_activity", side_effect=RuntimeError("Simulated write failure")):
                    with self.assertRaisesRegex(RuntimeError, "Simulated write failure"):
                        self.save("statement", title="Rolled back", kind=kind)
                self.assertEqual(self.save("statement", title="Next", kind=kind)["ref"], f"TEST-{prefix}-13")

    def test_design_references_persist_and_use_independent_project_sequences(self):
        data = {"title": "Design", "kind": "design"}
        first = self.store.save("statement", data, self.project["id"], request_id="first-design")
        self.assertEqual(first["ref"], "TEST-DES-1")
        self.assertEqual(first, self.store.save("statement", data, self.project["id"], request_id="first-design"))
        self.update("statement", first, archived=True)
        restarted = ProjectStore(self.root)
        restarted.initialize()
        restarted.reset_tickets("RESET ALL TICKETS", self.generation)
        self.assertEqual(restarted.save("statement", data, self.project["id"])["ref"], "TEST-DES-2")
        self.assertEqual(self.save("statement", title="Requirement")["ref"], "TEST-REQ-1")
        other = self.store.save("project", {"key": "GRID", "title": "Another project"})
        self.assertEqual(self.store.save("statement", data, other["id"])["ref"], "GRID-DES-1")
        with self.store.connection("statement", write=True) as conn:
            conn.execute("UPDATE design_counters SET next_number=12324 WHERE project_id=?", (other["id"],))
        self.assertEqual(self.store.save("statement", data, other["id"])["ref"], "GRID-DES-12324")

    def test_v4_design_migration_preserves_requirements_links_and_retries(self):
        req = self.save("statement", title="Requirement")
        data = {"title": "Legacy design", "kind": "design"}
        design = self.store.save("statement", data, self.project["id"], request_id="legacy-design")
        second = self.save("statement", title="Archived design", kind="design", archived=True)
        link = self.store.link("statement", design["id"], "statement", req["id"], "satisfies")
        legacy = {**design, "ref": "TEST-STA-legacydesign"}
        with self.store.connection("statement", write=True) as conn:
            conn.execute("UPDATE statements SET ref=? WHERE id=?", (legacy["ref"], design["id"]))
            conn.execute("UPDATE statements SET ref='TEST-STA-otherdesign' WHERE id=?", (second["id"],))
            conn.execute("UPDATE requests SET response=? WHERE key='legacy-design'", (json.dumps(legacy),))
            conn.execute("UPDATE requirement_counters SET next_number=7 WHERE project_id=?", (self.project["id"],))
            self.store._activity(conn, design["id"], "operator", "updated", legacy)
            conn.execute("DROP TABLE design_counters")
            conn.execute("PRAGMA user_version=4")
        restarted = ProjectStore(self.root)
        restarted.initialize()
        migrated = restarted.get("statement", design["id"])
        self.assertEqual(migrated["ref"], "TEST-DES-1")
        self.assertEqual(migrated["version"], design["version"] + 1)
        self.assertEqual(restarted.get("statement", second["id"])["ref"], "TEST-DES-2")
        self.assertEqual(restarted.get("statement", req["id"]), req)
        self.assertEqual(restarted.save("statement", data, self.project["id"], request_id="legacy-design"), migrated)
        with restarted.connection("statement") as conn:
            self.assertEqual(conn.execute("SELECT source_id FROM links WHERE id=?", (link["id"],)).fetchone()[0], design["id"])
            snapshots = [json.loads(row[0]) for row in conn.execute(
                "SELECT snapshot FROM activity WHERE record_id=?", (design["id"],))]
        self.assertIn(legacy, snapshots)
        self.assertTrue(any(snapshot.get("previous_ref") == legacy["ref"] for snapshot in snapshots))
        restarted.initialize()
        self.assertEqual(restarted.get("statement", design["id"]), migrated)
        self.assertEqual(restarted.save("statement", data, self.project["id"])["ref"], "TEST-DES-3")
        self.assertEqual(restarted.save("statement", {"title": "Next requirement"}, self.project["id"])["ref"], "TEST-REQ-7")

    def test_removed_statement_fields_are_rejected(self):
        for kind in ("requirement", "design"):
            record = self.save("statement", title=kind, kind=kind)
            for field in ("rationale", "acceptance"):
                self.assertNotIn(field, record)
                response = self.client.put(f"/api/records/statement/{record['id']}", json={
                    "data": {field: "Obsolete"}, "expected_version": record["version"],
                })
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("Unknown fields", response.text)
        with self.store.connection("statement") as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(statements)")}
        self.assertFalse({"rationale", "acceptance"} & columns)

    def test_v3_statement_migration_preserves_identity_links_and_history(self):
        data = {"title": "Legacy requirement"}
        first = self.store.save("statement", data, self.project["id"], request_id="legacy-requirement")
        second = self.save("statement", title="Archived requirement")
        second = self.update("statement", second, archived=True)
        design = self.save("statement", title="Design", kind="design")
        link = self.store.link("statement", design["id"], "statement", first["id"], "satisfies")
        legacy = {**first, "ref": "TEST-STA-legacy", "rationale": "Historical rationale", "acceptance": "Historical criteria"}
        with self.store.connection("statement", write=True) as conn:
            conn.execute("ALTER TABLE statements ADD COLUMN rationale TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE statements ADD COLUMN acceptance TEXT NOT NULL DEFAULT ''")
            conn.execute("UPDATE statements SET ref=?,rationale=?,acceptance=? WHERE id=?",
                         (legacy["ref"], legacy["rationale"], legacy["acceptance"], first["id"]))
            conn.execute("UPDATE statements SET ref='TEST-STA-archived' WHERE id=?", (second["id"],))
            conn.execute("UPDATE requests SET response=? WHERE key='legacy-requirement'", (json.dumps(legacy),))
            self.store._activity(conn, first["id"], "operator", "updated", legacy)
            conn.execute("DROP TABLE requirement_counters")
            conn.execute("PRAGMA user_version=3")
        restarted = ProjectStore(self.root)
        restarted.initialize()
        migrated = restarted.get("statement", first["id"])
        self.assertEqual(migrated["id"], first["id"])
        self.assertEqual(migrated["ref"], "TEST-REQ-1")
        self.assertEqual(migrated["version"], first["version"] + 1)
        self.assertNotIn("rationale", migrated)
        self.assertNotIn("acceptance", migrated)
        self.assertEqual(restarted.get("statement", second["id"])["ref"], "TEST-REQ-2")
        self.assertEqual(restarted.get("statement", design["id"])["ref"], design["ref"])
        self.assertEqual(restarted.save("statement", data, self.project["id"], request_id="legacy-requirement"), migrated)
        with restarted.connection("statement") as conn:
            self.assertEqual(conn.execute("SELECT target_id FROM links WHERE id=?", (link["id"],)).fetchone()[0], first["id"])
            snapshots = [json.loads(row[0]) for row in conn.execute("SELECT snapshot FROM activity WHERE record_id=?", (first["id"],))]
        self.assertIn(legacy, snapshots)
        self.assertTrue(any(snapshot.get("previous_ref") == legacy["ref"] for snapshot in snapshots))
        restarted.initialize()
        self.assertEqual(restarted.get("statement", first["id"]), migrated)
        self.assertEqual(restarted.save("statement", {"title": "After upgrade"}, self.project["id"])["ref"], "TEST-REQ-3")

    def test_versions_and_immutable_revision_snapshots(self):
        record = self.save("statement", title="First", state="approved")
        edited = self.update("statement", record, title="Second")
        self.assertEqual("draft", edited["state"])
        with self.assertRaises(ProjectError) as error:
            self.update("statement", record, title="Stale")
        self.assertEqual(409, error.exception.status)
        history = self.store.detail("statement", record["id"])["history"]
        self.assertEqual(2, len(history))
        self.assertEqual("First", json.loads(history[-1]["snapshot"])["title"])
        self.assertEqual("approved", json.loads(history[-1]["snapshot"])["state"])

    def test_concurrent_updates_only_one_wins(self):
        record = self.save("ticket", title="Original")

        def change(title):
            try:
                self.update("ticket", record, title=title)
                return 200
            except ProjectError as exc:
                return exc.status

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual([200, 409], sorted(pool.map(change, ["One", "Two"])))
        self.assertEqual(2, self.store.get("ticket", record["id"])["version"])

    def test_hierarchy_and_cross_project_validation(self):
        parent = self.save("ticket", title="Parent")
        child = self.save("ticket", title="Child", parent_id=parent["id"])
        with self.assertRaisesRegex(ProjectError, "cycles"):
            self.update("ticket", parent, parent_id=child["id"])
        with self.assertRaisesRegex(ProjectError, "cycles"):
            self.update("ticket", parent, parent_id=parent["id"])
        other = self.store.save("project", {"key": "OTHER", "title": "Other"})
        with self.assertRaisesRegex(ProjectError, "same project"):
            self.store.save("ticket", {"title": "Wrong", "parent_id": parent["id"]},
                            other["id"], generation=self.generation)
        self.assertEqual(2, self.store.list("ticket", self.project["id"])["total"])

    def test_relationship_kinds_and_cycles(self):
        req = self.save("statement", title="Req")
        req2 = self.save("statement", title="Req2")
        design = self.save("statement", title="Design", kind="design")
        self.store.link("statement", design["id"], "statement", req["id"], "satisfies")
        self.store.link("statement", req2["id"], "statement", req["id"], "refines")
        with self.assertRaisesRegex(ProjectError, "cycle"):
            self.store.link("statement", req["id"], "statement", req2["id"], "refines")
        with self.assertRaisesRegex(ProjectError, "kinds"):
            self.store.link("statement", req["id"], "statement", design["id"], "satisfies")
        a = self.save("ticket", title="A")
        b = self.save("ticket", title="B")
        self.store.link("ticket", a["id"], "ticket", b["id"], "depends_on", generation=self.generation)
        with self.assertRaisesRegex(ProjectError, "cycle"):
            self.store.link("ticket", b["id"], "ticket", a["id"], "depends_on", generation=self.generation)
        link = self.store.link("ticket", a["id"], "statement", req["id"], "addresses", generation=self.generation)
        self.assertIn(link["id"], [item["id"] for item in self.store.detail("statement", req["id"])["links"]])
        self.store.unlink("ticket", link["id"], "operator", self.generation)
        self.assertNotIn(link["id"], [item["id"] for item in self.store.detail("statement", req["id"])["links"]])

    def test_board_default_lanes_move_and_order(self):
        board, lanes = self.board()
        self.assertEqual(["Planned", "In Progress", "In Review", "Done"],
                         [lane["title"] for lane in sorted(lanes.values(), key=lambda lane: lane["position"])])
        ticket = self.save("ticket", title="Work")
        moved = self.update("ticket", ticket, lane_id=lanes["In Progress"]["id"], position=7)
        self.assertEqual(7, moved["position"])
        self.assertEqual(lanes["In Progress"]["id"], moved["lane_id"])
        self.assertNotIn("status", moved)
        self.assertNotIn("type", moved)
        with self.assertRaisesRegex(ProjectError, "Move active tickets"):
            self.update("lane", lanes["In Progress"], archived=True)
        other, other_lanes = self.board()
        moved = self.update("ticket", moved, lane_id=other_lanes["Planned"]["id"])
        self.assertEqual(other_lanes["Planned"]["id"], moved["lane_id"])
        self.assertNotEqual(board["id"], other["id"])

    def test_v2_lane_migration_merges_ready_and_preserves_work(self):
        board, lanes = self.board()
        lanes["Planned"] = self.update("lane", lanes["Planned"], archived=True)
        ready_data = {"title": "Ready", "board_id": board["id"], "category": "open", "position": 1}
        ready = self.store.save("lane", ready_data, self.project["id"],
                                request_id="ready-lane", generation=self.generation)
        data = {"title": "Ready ticket", "lane_id": ready["id"]}
        ticket = self.store.save("ticket", data, self.project["id"],
                                 request_id="ready-ticket", generation=self.generation)
        child = self.save("ticket", title="Archived child", lane_id=ready["id"],
                          parent_id=ticket["id"], archived=True)
        backlog = self.save("ticket", title="Backlog ticket")
        active = self.save("ticket", title="Active", lane_id=lanes["In Progress"]["id"])
        extra = self.save("lane", title="Verification", board_id=board["id"], category="active", position=8)
        lane_comment = self.store.comment("lane", ready["id"], "Lane discussion", "operator", self.generation)
        ticket_comment = self.store.comment("ticket", ticket["id"], "Ticket discussion", "operator", self.generation)
        link = self.store.link("ticket", ticket["id"], "ticket", backlog["id"], "relates_to",
                               generation=self.generation)
        custom_board, _ = self.board()
        custom_ready = self.save("lane", title="Ready", board_id=custom_board["id"], category="open", position=6)
        with self.store.connection("ticket", write=True) as conn:
            for title, old, position in (("Planned", "Inbox", 0), ("In Progress", "In progress", 2),
                                         ("In Review", "Review", 3), ("Done", "Done", 4)):
                conn.execute("UPDATE lanes SET title=?,position=? WHERE id=?",
                             (old, position, lanes[title]["id"]))
            conn.execute("PRAGMA user_version=2")
        with patch.object(self.store, "_activity", side_effect=RuntimeError("Migration interrupted")):
            with self.assertRaisesRegex(RuntimeError, "Migration interrupted"):
                self.store.initialize()
        with closing(sqlite3.connect(self.root / "tickets.db")) as conn:
            self.assertEqual(2, conn.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual("Inbox", conn.execute("SELECT title FROM lanes WHERE id=?",
                                                  (lanes["Planned"]["id"],)).fetchone()[0])
            self.assertEqual(ready["id"], conn.execute("SELECT lane_id FROM tickets WHERE id=?",
                                                       (ticket["id"],)).fetchone()[0])
        restarted = ProjectStore(self.root)
        restarted.initialize()
        migrated_lanes = [r for r in restarted.list("lane", self.project["id"])["items"] if r["board_id"] == board["id"]]
        self.assertEqual(["Planned", "In Progress", "In Review", "Done", "Verification"],
                         [r["title"] for r in sorted(migrated_lanes, key=lambda r: r["position"])])
        planned = restarted.get("lane", lanes["Planned"]["id"])
        self.assertFalse(planned["archived"])
        self.assertEqual(lanes["Planned"]["version"] + 1, planned["version"])
        migrated = restarted.get("ticket", ticket["id"])
        self.assertEqual(planned["id"], migrated["lane_id"])
        self.assertEqual(ticket["ref"], migrated["ref"])
        self.assertEqual(ticket["version"] + 1, migrated["version"])
        moved_child = restarted.get("ticket", child["id"])
        self.assertEqual(planned["id"], moved_child["lane_id"])
        self.assertEqual(ticket["id"], moved_child["parent_id"])
        self.assertTrue(moved_child["archived"])
        self.assertEqual(backlog, restarted.get("ticket", backlog["id"]))
        self.assertEqual(active, restarted.get("ticket", active["id"]))
        self.assertEqual(extra, restarted.get("lane", extra["id"]))
        self.assertEqual(custom_ready, restarted.get("lane", custom_ready["id"]))
        detail = restarted.detail("ticket", ticket["id"])
        self.assertIn(ticket_comment, detail["comments"])
        self.assertIn(link, detail["links"])
        self.assertIn(ticket, [json.loads(entry["snapshot"]) for entry in detail["history"]])
        self.assertIn({**lane_comment, "record_id": planned["id"]}, restarted.detail("lane", planned["id"])["comments"])
        self.assertEqual(migrated, restarted.save("ticket", data, self.project["id"],
                                                 request_id="ready-ticket", generation=self.generation))
        self.assertEqual(planned, restarted.save("lane", ready_data, self.project["id"],
                                                request_id="ready-lane", generation=self.generation))
        self.assertEqual(self.generation, restarted.ticket_generation())
        with self.assertRaises(ProjectError):
            restarted.get("lane", ready["id"])
        restarted.initialize()
        self.assertEqual(migrated, restarted.get("ticket", ticket["id"]))
        self.assertEqual(planned, restarted.get("lane", planned["id"]))

    def test_closing_parent_requires_explicit_confirmation(self):
        _, lanes = self.board()
        parent = self.save("ticket", title="Parent")
        child = self.save("ticket", title="Child", parent_id=parent["id"])
        with self.assertRaisesRegex(ProjectError, "Unfinished children"):
            self.update("ticket", parent, lane_id=lanes["Done"]["id"])
        self.store.save("ticket", {"lane_id": lanes["Done"]["id"]}, record_id=parent["id"],
                        expected_version=parent["version"], generation=self.generation, confirm_children=True)
        self.assertIsNone(self.store.get("ticket", child["id"])["lane_id"])

    def test_board_page_redirect_moves_and_comments(self):
        board, lanes = self.board()
        ticket = self.save("ticket", title="Board ticket", owner="Owner", priority="high")
        route = f"/ui/tickets?view=boards&project={self.project['id']}&board={board['id']}"
        redirected = self.client.get(route, follow_redirects=False)
        self.assertEqual(307, redirected.status_code)
        self.assertEqual(
            f"http://testserver/ui/boards?project={self.project['id']}&board={board['id']}",
            redirected.headers["location"],
        )
        self.assertIn('id="board-editor-dialog"', self.client.get(redirected.headers["location"]).text)
        url = f"/api/records/ticket/{ticket['id']}"
        move = {"data": {"lane_id": lanes["In Progress"]["id"]}, "expected_version": ticket["version"],
                "generation": self.generation, "confirm_children": False}
        moved = self.client.put(url, json=move)
        self.assertEqual(200, moved.status_code, moved.text)
        record = moved.json()
        self.assertEqual(ticket["ref"], record["ref"])
        self.assertEqual(ticket["owner"], record["owner"])
        self.assertEqual(ticket["priority"], record["priority"])
        self.assertEqual(lanes["In Progress"]["id"], record["lane_id"])
        self.assertEqual(409, self.client.put(url, json=move).status_code)
        comment = self.client.post(url + "/comments",
                                   json={"body": "Board discussion", "generation": self.generation})
        self.assertEqual(201, comment.status_code, comment.text)
        self.assertEqual("Board discussion", self.client.get(url).json()["comments"][0]["body"])
        move["data"]["lane_id"] = None
        move["expected_version"] = record["version"]
        self.assertIsNone(self.client.put(url, json=move).json()["lane_id"])
        move["generation"] = "stale-generation"
        self.assertEqual(409, self.client.put(url, json=move).status_code)

    def test_reset_preserves_definitions_and_rejects_stale_retry(self):
        req = self.save("statement", title="Req")
        design = self.save("statement", title="Design", kind="design")
        self.store.link("statement", design["id"], "statement", req["id"], "satisfies")
        self.store.comment("statement", req["id"], "Durable rationale", "operator")
        before = self.store.detail("statement", req["id"])
        self.board()
        ticket = self.store.save("ticket", {"title": "Work"}, self.project["id"],
                                 request_id="retry-1", generation=self.generation)
        self.store.comment("ticket", ticket["id"], "Disposable", "operator", self.generation)
        self.store.link("ticket", ticket["id"], "statement", req["id"], "addresses", generation=self.generation)
        with self.assertRaises(ProjectError):
            self.store.reset_tickets("yes", self.generation)
        reset = self.store.reset_tickets("RESET ALL TICKETS", self.generation)
        self.assertNotEqual(self.generation, reset["generation"])
        self.assertEqual(before, self.store.detail("statement", req["id"]))
        for kind in ("ticket", "board", "lane"):
            self.assertEqual(0, self.store.list(kind, self.project["id"])["total"])
        with self.assertRaises(ProjectError) as error:
            self.store.save("ticket", {"title": "Work"}, self.project["id"],
                            request_id="retry-1", generation=self.generation)
        self.assertEqual(409, error.exception.status)
        newer = self.store.save("ticket", {"title": "Work"}, self.project["id"],
                                request_id="retry-1", generation=reset["generation"])
        self.assertNotEqual(ticket["id"], newer["id"])
        self.assertNotEqual(ticket["ref"], newer["ref"])
        with self.assertRaises(ProjectError):
            self.store.get("ticket", ticket["id"])

    def test_idempotent_creation(self):
        args = dict(kind="ticket", data={"title": "Once"}, project_id=self.project["id"],
                    request_id="unique", generation=self.generation)
        first = self.store.save(**args)
        self.assertEqual(first, self.store.save(**args))
        with self.assertRaises(ProjectError) as error:
            self.store.save(**{**args, "data": {"title": "Different"}})
        self.assertEqual(409, error.exception.status)
        self.assertEqual(1, self.store.list("ticket", self.project["id"])["total"])

    def test_missing_and_corrupt_ticket_store_leave_definitions_readable(self):
        statement = self.save("statement", title="Durable")
        (self.root / "tickets.db").unlink()
        restarted = ProjectStore(self.root)
        restarted.initialize()
        self.assertFalse((self.root / "tickets.db").exists())
        self.assertIsNotNone(restarted.detail("statement", statement["id"])["ticket_store_error"])
        self.assertEqual(503, self.client.get("/status").status_code)
        self.assertEqual(200, self.client.get(f"/api/records/statement/{statement['id']}").status_code)
        (self.root / "tickets.db").write_bytes(b"not a sqlite database")
        with self.assertLogs("koreproject", level="ERROR"):
            restarted.initialize()
        self.assertIsNotNone(restarted.detail("statement", statement["id"])["ticket_store_error"])
        self.assertEqual(statement, restarted.get("statement", statement["id"]))

    def test_api_validation_and_native_skill_parity(self):
        response = self.client.post("/api/skills/project_save/invoke", json={"arguments": {
            "kind": "statement", "project_id": self.project["id"],
            "data": {"title": "Agent proposal", "state": "approved"},
        }})
        self.assertEqual(403, response.status_code)
        response = self.client.post("/api/skills/project_save/invoke", json={"arguments": {
            "kind": "statement", "project_id": self.project["id"],
            "data": {"title": "Agent proposal"},
        }})
        self.assertEqual(200, response.status_code)
        record = response.json()["result"]
        detail = self.client.get(f"/api/records/statement/{record['id']}").json()
        self.assertEqual("agent", detail["history"][0]["actor"])
        conflict = self.client.put(f"/api/records/statement/{record['id']}",
                                   json={"data": {"title": "Overwrite"}, "expected_version": 2})
        self.assertEqual(409, conflict.status_code)
        invalid = self.client.post("/api/records/ticket", json={"project_id": self.project["id"],
                                   "generation": self.generation, "data": {"title": "Typed", "type": "bug"}})
        self.assertEqual(422, invalid.status_code)
        read = self.client.post("/api/skills/project_read/invoke", json={"arguments": {
            "kind": "statement", "record_id": record["id"],
        }})
        self.assertEqual(detail["history"], read.json()["result"]["history"])
        self.assertEqual(self.generation, read.json()["result"]["generation"])

    def test_native_ticket_writes_need_generation_and_cannot_reset(self):
        response = self.client.post("/api/skills/project_save/invoke", json={"arguments": {
            "kind": "ticket", "project_id": self.project["id"], "data": {"title": "Stale"},
        }})
        self.assertEqual(409, response.status_code)
        response = self.client.post("/api/skills/project_reset/invoke", json={"arguments": {}})
        self.assertEqual(404, response.status_code)

    def test_filters_pagination_and_input_validation(self):
        self.save("statement", title="One")
        self.save("statement", title="Two", body="Needle")
        self.assertEqual(2, self.store.list("statement", self.project["id"], limit=1)["total"])
        page = self.store.list("statement", self.project["id"], offset=1, limit=1)
        self.assertEqual("Two", page["items"][0]["title"])
        self.assertEqual(1, self.store.list("statement", self.project["id"], q="needle")["total"])
        for data in ({"title": ""}, {"title": "X", "position": -1}, {"title": "X", "parent_id": 12}):
            with self.assertRaises(ProjectError):
                self.save("ticket", **data)
        self.assertEqual(422, self.client.get("/api/records/statement?limit=9999").status_code)

    def test_shared_shell_manifest_and_cross_origin_guard(self):
        for url in ("/ui", "/static/project.js", "/ui-elements/assets/css/chrome.css", "/suite-config.js", "/__endpoint_manifest"):
            self.assertEqual(200, self.client.get(url).status_code, url)
        denied = self.client.post("/api/records/project", headers={"Origin": "https://other.invalid"},
                                  json={"data": {"title": "Forged", "key": "BAD"}})
        self.assertEqual(403, denied.status_code)
        path = ROOT / "KoreProject" / "skill_registration.json"
        registration = _build_registration(json.loads(path.read_text()), manifest_path=path,
                                           service_base_url="http://127.0.0.1:29619")
        tools = registration["skills"][0]["tools"]
        self.assertEqual({"project_read", "project_save", "project_link", "project_comment"},
                         {tool["name"] for tool in tools})
        self.assertTrue(all(tool["invoke_url"].startswith("http://127.0.0.1:29619/api/skills/") for tool in tools))

    def test_project_delete_removes_only_selected_project(self):
        folder = self.save("section", title="System")
        child_folder = self.save("section", title="Storage", parent_id=folder["id"])
        requirement = self.save("statement", title="Requirement", section_id=child_folder["id"])
        design = self.save("statement", title="Design", kind="design")
        self.store.link("statement", design["id"], "statement", requirement["id"], "satisfies")
        board, lanes = self.board()
        parent = self.save("ticket", title="Parent", lane_id=lanes["Planned"]["id"])
        child = self.save("ticket", title="Child", parent_id=parent["id"])
        self.store.link("ticket", child["id"], "statement", requirement["id"], "addresses",
                        generation=self.generation)
        for kind, record in (("project", self.project), ("statement", requirement), ("section", folder),
                             ("board", board), ("lane", lanes["Planned"]), ("ticket", child)):
            self.store.comment(kind, record["id"], "Remove this comment", "operator", self.generation)
        self.store.save("statement", {"title": "Retry"}, self.project["id"], request_id="statement-retry")
        self.store.save("ticket", {"title": "Retry"}, self.project["id"],
                        request_id="ticket-retry", generation=self.generation)
        other = self.store.save("project", {"key": "KEEP", "title": "Keep"})
        other_statement = self.store.save("statement", {"title": "Keep requirement"}, other["id"])
        other_ticket = self.store.save("ticket", {"title": "Keep ticket"}, other["id"], generation=self.generation)
        before = {
            "project": self.store.detail("project", other["id"]),
            "statement": self.store.detail("statement", other_statement["id"]),
            "ticket": self.store.detail("ticket", other_ticket["id"]),
        }
        response = self.client.request("DELETE", f"/api/projects/{self.project['id']}", json={
            "expected_version": self.project["version"], "confirmation": "DELETE TEST",
        })
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(self.project["id"], response.json()["deleted"])
        with self.assertRaises(ProjectError):
            self.store.get("project", self.project["id"])
        for kind in ("statement", "ticket", "board", "lane", "section"):
            self.assertEqual(0, self.store.list(kind, self.project["id"])["total"])
        for kind, record in (("project", other), ("statement", other_statement), ("ticket", other_ticket)):
            self.assertEqual(before[kind], self.store.detail(kind, record["id"]))
        for kind in ("project", "ticket"):
            with self.store.connection(kind) as conn:
                for table in ("links", "comments", "requests"):
                    self.assertEqual(0, conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                ids = {row[0] for row in conn.execute("SELECT record_id FROM activity")}
                expected = {other["id"], other_statement["id"]} if kind == "project" else {other_ticket["id"]}
                self.assertEqual(expected, ids)
        self.assertEqual(self.generation, self.store.ticket_generation())
        with self.store.connection("ticket") as conn:
            self.assertEqual([other["id"]], [row[0] for row in conn.execute("SELECT project_id FROM ticket_counters")])

    def test_project_delete_requires_confirmation_version_and_available_stores(self):
        url = f"/api/projects/{self.project['id']}"
        for body, status in (
            ({"expected_version": 1, "confirmation": "yes"}, 422),
            ({"expected_version": 2, "confirmation": "DELETE TEST"}, 409),
            ({"confirmation": "DELETE TEST"}, 422),
        ):
            response = self.client.request("DELETE", url, json=body)
            self.assertEqual(status, response.status_code)
        (self.root / "tickets.db").unlink()
        response = self.client.request("DELETE", url, json={"expected_version": 1, "confirmation": "DELETE TEST"})
        self.assertEqual(503, response.status_code)
        self.assertFalse(self.store.get("project", self.project["id"])["deletion_pending"])

    def test_pending_project_delete_blocks_writes_and_resumes_after_restart(self):
        statement = self.save("statement", title="Durable until completion")
        ticket = self.save("ticket", title="Remove")
        with patch.object(self.store, "_finish_project_deletion", side_effect=sqlite3.OperationalError("locked")), \
                self.assertLogs("koreproject", level="ERROR"):
            with self.assertRaises(ProjectError) as error:
                self.store.delete_project(self.project["id"], 1, "DELETE TEST")
        self.assertEqual(503, error.exception.status)
        self.assertTrue(self.store.list("project")["items"][0]["deletion_pending"])
        with self.assertRaisesRegex(ProjectError, "pending"):
            self.update("project", self.project, title="Cannot edit")
        with self.assertRaisesRegex(ProjectError, "pending"):
            self.save("statement", title="Cannot add")
        with self.assertRaisesRegex(ProjectError, "pending"):
            self.store.comment("ticket", ticket["id"], "Cannot comment", "operator", self.generation)
        with self.assertRaisesRegex(ProjectError, "pending"):
            self.store.link("ticket", ticket["id"], "statement", statement["id"], "addresses",
                            generation=self.generation)
        # Simulate interruption after the first store committed but before definitions were removed.
        with self.store.connection("ticket", write=True) as conn:
            conn.execute("DELETE FROM activity WHERE record_id=?", (ticket["id"],))
            conn.execute("DELETE FROM tickets WHERE project_id=?", (self.project["id"],))
        restarted = ProjectStore(self.root)
        restarted.initialize()
        self.assertEqual(0, restarted.list("project")["total"])
        self.assertEqual(0, restarted.list("statement", self.project["id"])["total"])
        with restarted.connection("ticket") as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0])

    def test_project_schema_upgrade_preserves_existing_records(self):
        record = self.save("statement", title="Before upgrade")
        with self.store.connection("project", write=True) as conn:
            conn.execute("DROP TABLE project_deletions")
            conn.execute("PRAGMA user_version=1")
        self.store.initialize()
        self.assertEqual(record, self.store.get("statement", record["id"]))
        self.assertFalse(self.store.get("project", self.project["id"])["deletion_pending"])

    def test_section_delete_recursively_purges_contents_and_preserves_unrelated_records(self):
        for kind in ("requirement", "design"):
            with self.subTest(kind=kind):
                root = self.store.save("section", {"title": "Remove", "kind": kind}, self.project["id"],
                                       request_id=f"{kind}-section")
                child = self.save("section", title="Nested", kind=kind, parent_id=root["id"])
                grandchild = self.save("section", title="Deep", kind=kind, parent_id=child["id"])
                archived = self.save("section", title="Archived", kind=kind, parent_id=root["id"], archived=True)
                statement = self.store.save(
                    "statement", {"title": "Remove content", "kind": kind, "section_id": grandchild["id"]},
                    self.project["id"], request_id=f"{kind}-statement",
                )
                direct = self.save("statement", title="Direct content", kind=kind, section_id=root["id"])
                self.update("statement", direct, archived=True)
                sibling = self.save("section", title="Keep", kind=kind)
                survivor = self.save("statement", title="Keep content", kind=kind, section_id=sibling["id"])
                ticket = self.save("ticket", title="Keep ticket")
                before_statement = self.store.detail("statement", survivor["id"])
                before_ticket = self.store.detail("ticket", ticket["id"])
                self.store.link("statement", survivor["id"], "statement", statement["id"], "relates_to")
                link = self.store.link("ticket", ticket["id"], "statement", statement["id"], "addresses",
                                       generation=self.generation)
                self.store.unlink("ticket", link["id"], "operator", self.generation)
                self.store.link("ticket", ticket["id"], "statement", statement["id"], "addresses",
                                generation=self.generation)
                for record_kind, record in (("section", root), ("section", child), ("statement", statement)):
                    self.store.comment(record_kind, record["id"], "Remove comment", "operator")
                response = self.client.request("DELETE", f"/api/records/section/{root['id']}",
                                               json={"expected_version": root["version"]})
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual({"deleted": root["id"]}, response.json())
                deleted_ids = {record["id"] for record in (root, child, grandchild, archived, statement, direct)}
                for record_kind, record in (("section", root), ("section", child), ("section", grandchild),
                                            ("section", archived), ("statement", statement), ("statement", direct)):
                    self.assertEqual(404, self.client.get(f"/api/records/{record_kind}/{record['id']}").status_code)
                self.assertEqual(before_statement, self.store.detail("statement", survivor["id"]))
                self.assertEqual(before_ticket, self.store.detail("ticket", ticket["id"]))
                self.assertEqual(sibling, self.store.get("section", sibling["id"]))
                for store_kind in ("statement", "ticket"):
                    with self.store.connection(store_kind) as conn:
                        for table in ("activity", "comments", "links", "requests"):
                            rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
                            for deleted_id in deleted_ids:
                                self.assertNotIn(deleted_id, json.dumps(rows))
                with self.store.connection("section") as conn:
                    self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM section_deletions").fetchone()[0])
                    self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())
                next_statement = self.save("statement", title="Next", kind=kind)
                self.assertGreater(int(next_statement["ref"].rsplit("-", 1)[1]),
                                   int(survivor["ref"].rsplit("-", 1)[1]))
                restarted = ProjectStore(self.root)
                restarted.initialize()
                with self.assertRaises(ProjectError):
                    restarted.get("section", root["id"])

    def test_section_delete_checks_version_project_state_and_storage(self):
        section = self.save("section", title="Keep")
        url = f"/api/records/section/{section['id']}"
        for body, status in (({}, 422), ({"expected_version": 0}, 422), ({"expected_version": 2}, 409)):
            self.assertEqual(status, self.client.request("DELETE", url, json=body).status_code)
        project = self.update("project", self.project, archived=True)
        self.assertEqual(422, self.client.request("DELETE", url, json={"expected_version": 1}).status_code)
        self.update("project", project, archived=False)
        (self.root / "tickets.db").unlink()
        self.assertEqual(503, self.client.request("DELETE", url, json={"expected_version": 1}).status_code)
        self.assertEqual(section, self.store.get("section", section["id"]))
        with self.store.connection("section") as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM section_deletions").fetchone()[0])

    def test_section_delete_interruption_blocks_writes_and_resumes(self):
        section = self.save("section", title="Remove")
        statement = self.save("statement", title="Remove", section_id=section["id"])
        ticket = self.save("ticket", title="Keep")
        self.store.link("ticket", ticket["id"], "statement", statement["id"], "addresses",
                        generation=self.generation)
        with self.store.connection("statement", write=True) as conn:
            conn.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON statements "
                         "BEGIN SELECT RAISE(ABORT, 'Simulated deletion failure'); END")
        with self.assertLogs("koreproject", level="ERROR"):
            response = self.client.request("DELETE", f"/api/records/section/{section['id']}",
                                           json={"expected_version": 1})
        self.assertEqual(503, response.status_code)
        self.assertEqual(section, self.store.get("section", section["id"]))
        self.assertEqual(statement, self.store.get("statement", statement["id"]))
        with self.assertRaisesRegex(ProjectError, "pending"):
            self.save("statement", title="Blocked while deleting")
        with self.assertRaisesRegex(ProjectError, "pending"):
            self.update("ticket", ticket, title="Blocked while deleting")
        with self.store.connection("statement", write=True) as conn:
            conn.execute("DROP TRIGGER fail_delete")
        restarted = ProjectStore(self.root)
        restarted.initialize()
        with self.assertRaises(ProjectError):
            restarted.get("section", section["id"])
        self.assertEqual(ticket, restarted.get("ticket", ticket["id"]))
        self.assertEqual([], restarted.detail("ticket", ticket["id"])["links"])
        self.save("statement", title="Writes available again")
        with restarted.connection("section") as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM section_deletions").fetchone()[0])

    def test_v6_section_deletion_migration_preserves_existing_content(self):
        section = self.save("section", title="Existing")
        statement = self.save("statement", title="Existing", section_id=section["id"])
        before = self.store.detail("statement", statement["id"])
        with self.store.connection("section", write=True) as conn:
            conn.execute("DROP TABLE section_deletions")
            conn.execute("PRAGMA user_version=6")
        restarted = ProjectStore(self.root)
        restarted.initialize()
        self.assertEqual(before, restarted.detail("statement", statement["id"]))
        self.assertEqual(section, restarted.get("section", section["id"]))
        with restarted.connection("section") as conn:
            self.assertEqual(7, conn.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM section_deletions").fetchone()[0])

    def test_section_hierarchy_and_requirement_membership(self):
        root = self.save("section", title="System")
        child = self.save("section", title="Storage", parent_id=root["id"], position=2)
        req = self.save("statement", title="Persist intent", section_id=child["id"])
        with self.assertRaisesRegex(ProjectError, "cycles"):
            self.update("section", root, parent_id=child["id"])
        with self.assertRaisesRegex(ProjectError, "cycles"):
            self.update("section", root, parent_id=root["id"])
        with self.assertRaisesRegex(ProjectError, "Move statements"):
            self.update("section", child, archived=True)
        with self.assertRaisesRegex(ProjectError, "Move statements"):
            self.update("section", root, archived=True)
        with self.assertRaisesRegex(ProjectError, "kinds must match"):
            self.save("statement", title="Design", kind="design", section_id=child["id"])
        with self.assertRaisesRegex(ProjectError, "section_id"):
            self.save("statement", title="Invalid", section_id=123)
        other = self.store.save("project", {"key": "OTHER", "title": "Other"})
        with self.assertRaisesRegex(ProjectError, "same project"):
            self.store.save("statement", {"title": "Cross-project", "section_id": root["id"]}, other["id"])
        with self.assertRaisesRegex(ProjectError, "same project"):
            self.store.save("section", {"title": "Cross-project", "parent_id": root["id"]}, other["id"])
        moved = self.update("statement", req, section_id=None)
        self.assertEqual(req["id"], moved["id"])
        self.assertEqual(req["ref"], moved["ref"])
        self.assertIsNone(moved["section_id"])
        self.update("section", child, archived=True)
        with self.assertRaisesRegex(ProjectError, "archived"):
            self.save("statement", title="Archived section", section_id=child["id"])

    def test_sections_survive_reset_and_work_without_ticket_storage(self):
        section = self.save("section", title="Durable section")
        req = self.save("statement", title="Requirement", section_id=section["id"])
        design_section = self.save("section", title="Design section", kind="design")
        design = self.save("statement", title="Design", kind="design", section_id=design_section["id"])
        before = self.store.detail("section", section["id"])
        self.store.reset_tickets("RESET ALL TICKETS", self.generation)
        self.assertEqual(before, self.store.detail("section", section["id"]))
        self.assertEqual(req, self.store.get("statement", req["id"]))
        self.assertEqual(design, self.store.get("statement", design["id"]))
        self.assertEqual(design_section, self.store.get("section", design_section["id"]))
        (self.root / "tickets.db").unlink()
        response = self.client.post("/api/skills/project_save/invoke", json={"arguments": {
            "kind": "section", "project_id": self.project["id"],
            "data": {"title": "Child", "parent_id": section["id"]},
        }})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("agent", self.store.detail("section", response.json()["result"]["id"])["history"][0]["actor"])
        response = self.client.get(f"/api/statement-links?project_id={self.project['id']}")
        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["items"])

    def test_requirements_page_and_paginated_links(self):
        req = self.save("statement", title="Requirement")
        other = self.save("statement", title="Refinement")
        design = self.save("statement", title="Design", kind="design")
        self.store.link("statement", other["id"], "statement", req["id"], "refines")
        self.store.link("statement", design["id"], "statement", req["id"], "satisfies")
        for url in ("/ui", "/ui/requirements", "/ui/design", "/ui/tickets", "/ui/boards", "/ui/settings",
                    "/static/requirements.js", "/static/requirements.css", "/static/client.js"):
            self.assertEqual(200, self.client.get(url).status_code, url)
        page = self.client.get("/ui/requirements").text
        self.assertEqual(
            ["Unique ID", "Title", "Requirement", "Design links"],
            re.findall(r'<th\b[^>]*scope="col"[^>]*>([^<]+)</th>', page),
        )
        self.assertEqual(1, page.count('id="save-all"'))
        self.assertNotIn('id="show-archived"', page)
        self.assertNotIn('id="requirement-details"', page)
        self.assertNotIn("Use Save above", page)
        self.assertNotIn("Requirements and their linked context", page)
        self.assertIn('aria-labelledby="table-title"', page)
        url = f"/api/statement-links?project_id={self.project['id']}&limit=1"
        first = self.client.get(url).json()
        second = self.client.get(url + "&offset=1").json()
        self.assertEqual(2, first["total"])
        self.assertEqual(1, len(first["items"]))
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        self.assertEqual({"refines", "satisfies"}, {first["items"][0]["relation"], second["items"][0]["relation"]})
        self.assertEqual(422, self.client.get(url + "&offset=-1").status_code)

    def test_requirement_text_save_preserves_implicit_membership_and_metadata(self):
        section = self.save("section", title="Table")
        requirement = self.save("statement", title="Requirement", body="Original",
                                section_id=section["id"], owner="Existing owner", state="in_review", archived=True)
        response = self.client.put(f"/api/records/statement/{requirement['id']}", json={
            "data": {"title": "Updated title", "body": "Updated text"},
            "expected_version": requirement["version"],
        })
        self.assertEqual(200, response.status_code, response.text)
        saved = response.json()
        self.assertEqual("Updated text", saved["body"])
        for field in ("section_id", "owner", "state", "archived", "ref"):
            self.assertEqual(requirement[field], saved[field], field)

    def test_design_section_tree_is_independent_and_kind_is_immutable(self):
        requirement_section = self.save("section", title="Requirements")
        root = self.save("section", title="Architecture", kind="design")
        child = self.save("section", title="Storage", kind="design", parent_id=root["id"])
        design = self.save("statement", title="SQLite design", kind="design", section_id=child["id"])
        self.assertEqual(child["id"], design["section_id"])
        for statement_kind, section in (("requirement", child), ("design", requirement_section)):
            with self.assertRaisesRegex(ProjectError, "kinds must match"):
                self.save("statement", title="Wrong table", kind=statement_kind, section_id=section["id"])
        with self.assertRaisesRegex(ProjectError, "kinds must match"):
            self.save("section", title="Wrong tree", kind="design", parent_id=requirement_section["id"])
        with self.assertRaisesRegex(ProjectError, "kinds must match"):
            self.update("section", child, parent_id=requirement_section["id"])
        with self.assertRaisesRegex(ProjectError, "permanent"):
            self.store.save("section", {"kind": "requirement"}, record_id=root["id"], expected_version=root["version"])
        with self.assertRaisesRegex(ProjectError, "cycles"):
            self.update("section", root, parent_id=child["id"])
        with self.assertRaisesRegex(ProjectError, "Move statements"):
            self.update("section", child, archived=True)
        response = self.client.post("/api/skills/project_save/invoke", json={"arguments": {
            "kind": "section", "project_id": self.project["id"],
            "data": {"title": "Agent design section", "kind": "design", "parent_id": root["id"]},
        }})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual("design", response.json()["result"]["kind"])

    def test_v5_section_migration_preserves_membership_and_cached_creation(self):
        data = {"title": "Legacy table"}
        section = self.store.save("section", data, self.project["id"], request_id="legacy-section")
        requirement = self.save("statement", title="Requirement", section_id=section["id"])
        design = self.save("statement", title="Unfiled design", kind="design")
        legacy = {key: value for key, value in section.items() if key != "kind"}
        with self.store.connection("section", write=True) as conn:
            conn.execute("ALTER TABLE sections DROP COLUMN kind")
            conn.execute("UPDATE requests SET response=? WHERE key='legacy-section'", (json.dumps(legacy),))
            conn.execute("PRAGMA user_version=5")
        self.store.initialize()
        self.assertEqual(section, self.store.get("section", section["id"]))
        self.assertEqual(section, self.store.save("section", data, self.project["id"], request_id="legacy-section"))
        self.assertEqual(requirement, self.store.get("statement", requirement["id"]))
        self.assertEqual(design, self.store.get("statement", design["id"]))
        design_section = self.save("section", title="Design table", kind="design")
        updated = self.update("statement", design, section_id=design_section["id"])
        self.assertEqual(design["ref"], updated["ref"])
        self.assertEqual(design_section["id"], updated["section_id"])

    def test_version_two_migration_keeps_old_requirements_unfiled(self):
        req = self.save("statement", title="Existing requirement")
        with self.store.connection("project", write=True) as conn:
            conn.execute("DROP INDEX statements_section")
            conn.execute("ALTER TABLE statements DROP COLUMN section_id")
            conn.execute("DROP TABLE sections")
            conn.execute("PRAGMA user_version=2")
        self.store.initialize()
        self.assertEqual(req, self.store.get("statement", req["id"]))
        section = self.save("section", title="Adopted")
        updated = self.update("statement", req, section_id=section["id"])
        self.assertEqual(req["ref"], updated["ref"])


if __name__ == "__main__":
    unittest.main()
