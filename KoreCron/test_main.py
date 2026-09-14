# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Regression coverage for KoreCron's fresh-conversation lifecycle and scheduled KoreTest-run
# definitions. The test classes follow those two product responsibilities.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from __future__ import annotations

import unittest
from datetime import date
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from KoreCron import main
from KoreCron.output_contracts import OutputContractResult, validate_output_contract


# ====================================================================================================
# MARK: FRESH CONVERSATION TESTS
# ====================================================================================================
class FreshConversationTests(unittest.TestCase):
    def test_deletes_all_name_matches_before_creating(self) -> None:
        calls: list[tuple[str, str, dict | None]] = []
        conversations = [
            {"id": 9, "subject": "Nightly Research", "external_id": "legacy_9"},
            {"id": 3, "subject": "nightly research", "external_id": "legacy_3"},
            {"id": 7, "subject": "Other", "external_id": "webchat_cron_Other"},
        ]

        def fake_http(method: str, url: str, body: dict | None = None):
            calls.append((method, url, body))
            if method == "GET":
                return conversations
            if method == "POST":
                return {"id": 12, **(body or {})}
            return None

        with patch.object(main, "_service_url", return_value="http://chat"), patch.object(main, "_http", side_effect=fake_http):
            created = main._fresh_conversation({"chat_name": "Nightly Research"})

        self.assertEqual(created["id"], 12)
        self.assertEqual(
            [(method, url) for method, url, _body in calls],
            [
                ("GET", "http://chat/api/conversations?limit=500&offset=0"),
                ("DELETE", "http://chat/api/conversations/3"),
                ("DELETE", "http://chat/api/conversations/9"),
                ("POST", "http://chat/api/conversations"),
            ],
        )
        self.assertEqual(calls[-1][2], {
            "channel_type": "webchat",
            "subject": "Nightly Research",
            "external_id": "webchat_cron_Nightly_Research",
        })

    def test_deletes_stale_external_id_even_if_chat_was_renamed(self) -> None:
        deleted: list[str] = []

        def fake_http(method: str, url: str, body: dict | None = None):
            if method == "GET":
                return [{"id": 5, "subject": "Renamed", "external_id": "webchat_cron_Daily"}]
            if method == "DELETE":
                deleted.append(url)
                return None
            return {"id": 6, **(body or {})}

        with patch.object(main, "_service_url", return_value="http://chat"), patch.object(main, "_http", side_effect=fake_http):
            main._fresh_conversation({"chat_name": "Daily"})

        self.assertEqual(deleted, ["http://chat/api/conversations/5"])

# ====================================================================================================
# MARK: SCHEDULED TEST-RUN TESTS
# ====================================================================================================
class ScheduledTestRunTests(unittest.TestCase):
    def test_test_run_definition_accepts_only_a_daily_time(self) -> None:
        definition = main._test_run_definition({"time": "09:30"})

        self.assertEqual(definition["id"], "test_run:09:30")
        self.assertEqual(definition["kind"], "test_run")
        self.assertEqual(definition["schedule"], {"type": "daily", "time": "09:30"})

        with self.assertRaises(Exception):
            main._test_run_definition({"time": "30"})

    def test_test_run_queues_the_full_koretest_suite(self) -> None:
        with patch.object(main, "_service_url", return_value="http://test"), patch.object(main, "_http") as http:
            main._run({"kind": "test_run", "id": "test_run:09:30"})

        http.assert_called_once_with("POST", "http://test/api/runs/queue", {"suite": "all"})

    def test_test_runs_are_stored_separately_from_cronprompts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store_file = Path(temp_dir) / "cronprompts.json"
            state_file = Path(temp_dir) / "scheduler_state.json"
            with patch.object(main, "STORE_FILE", store_file), patch.object(main, "STATE_FILE", state_file):
                created = main.create_test_run({"time": "09:30"})
                listed = main.list_test_runs()

                self.assertEqual(created["id"], "test_run:09:30")
                self.assertEqual([item["id"] for item in listed["test_runs"]], ["test_run:09:30"])
                self.assertEqual(main._definitions(), [])

                main.delete_test_run("test_run:09:30")
                self.assertEqual(main.list_test_runs()["test_runs"], [])


class SchedulerRunHistoryTests(unittest.TestCase):
    def test_records_a_failed_attempt_with_its_error(self) -> None:
        with TemporaryDirectory() as temp_dir:
            history_file = Path(temp_dir) / "scheduler_run_history.json"
            definition   = {"id": "daily-news", "name": "Daily News"}
            attempted_at = main.datetime(2026, 9, 7, 7, 20)
            with patch.object(main, "RUN_HISTORY_FILE", history_file):
                main._record_run(
                    definition,
                    attempted_at=attempted_at,
                    succeeded=False,
                    error="agent could not produce a valid response",
                )

            recorded = main._read(history_file, {})["daily-news"][-1]
            self.assertEqual("2026-09-07T07:20:00", recorded["attempted_at"])
            self.assertFalse(recorded["succeeded"])
            self.assertEqual("agent could not produce a valid response", recorded["error"])

    def test_marks_a_failed_run_command_as_a_failed_cron_prompt(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "script command failed"):
            main._require_successful_reply(
                {"name": "AI News"},
                "/run ./scripts/ainews_articles.py",
                {"content": "/run failed: ainews_articles.py exited with code 1."},
            )


# ====================================================================================================
# MARK: OUTPUT CONTRACT TESTS
# ====================================================================================================
class OutputContractTests(unittest.TestCase):
    def test_markdown_contract_counts_each_topic_not_the_whole_file(self) -> None:
        topic_body = " ".join(["evidence"] * 200)
        content    = "# AI News Update - 2026-09-12\n\n" + "\n\n".join(
            f"## Topic {index}\n\n{topic_body}"
            for index in range(1, 7)
        )
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "briefing.md"
            path.write_text(content, encoding="utf-8")
            with patch("KoreCron.output_contracts.resolve_datauser_path", return_value=path):
                result = validate_output_contract({
                    "type":                "markdown_sections",
                    "path":                "AINewsFile/{date}.md",
                    "required_title":      "AI News Update -",
                    "min_items":           6,
                    "max_items":           8,
                    "min_words_per_item":  200,
                    "max_words_per_item":  300,
                }, run_date=date(2026, 9, 12))

        self.assertTrue(result.ok, result.errors)

    def test_markdown_contract_reports_underlength_topic(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "briefing.md"
            path.write_text("# AI News Update - 2026-09-12\n\n## Short topic\n\nOnly a few words.", encoding="utf-8")
            with patch("KoreCron.output_contracts.resolve_datauser_path", return_value=path):
                result = validate_output_contract({
                    "type":               "markdown_sections",
                    "path":               "AINewsFile/{date}.md",
                    "min_items":          6,
                    "max_items":          8,
                    "min_words_per_item": 200,
                }, run_date=date(2026, 9, 12))

        self.assertFalse(result.ok)
        self.assertIn("Markdown topics count is 1; expected 6 to 8.", result.errors)
        self.assertIn("Topic 'Short topic' has 4 words; requires at least 200.", result.errors)

    def test_json_contract_rejects_wrong_schema_and_short_content(self) -> None:
        content = '[{"title": "Topic", "summary": "brief", "content": "too short", "tags": []}]'
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "briefing.json"
            path.write_text(content, encoding="utf-8")
            with patch("KoreCron.output_contracts.resolve_datauser_path", return_value=path):
                result = validate_output_contract({
                    "type":               "json_topics",
                    "path":               "AINewsFile/{date}.json",
                    "min_items":          1,
                    "max_items":          1,
                    "min_words_per_item": 200,
                    "min_summary_words":  60,
                    "required_fields":    ["title", "summary", "content", "tags"],
                }, run_date=date(2026, 9, 12))

        self.assertFalse(result.ok)
        self.assertIn("JSON topic 1 content has 2 words; requires at least 200.", result.errors)
        self.assertIn("JSON topic 1 summary has 1 word; requires at least 60.", result.errors)
        self.assertIn("JSON topic 1 tags must be an array of non-empty strings.", result.errors)

    def test_cron_retries_a_failed_contract_before_advancing(self) -> None:
        contract = {
            "type":                "markdown_sections",
            "path":                "AINewsFile/{date}.md",
            "min_items":           6,
            "max_items":           8,
            "min_words_per_item":  200,
            "max_repair_attempts": 1,
        }
        results = iter([
            OutputContractResult(path=Path("briefing.md"), errors=("Topic 'One' has 40 words; requires at least 200.",)),
            OutputContractResult(path=Path("briefing.md"), errors=()),
        ])
        definition = {"name": "AINewsFile", "chat_name": "AINewsFile", "prompts": [{"prompt": "Write briefing", "output_contract": contract}]}

        with patch.object(main, "_fresh_conversation", return_value={"id": 1}), \
             patch.object(main, "_service_url", return_value="http://chat"), \
             patch.object(main, "_send_prompt", return_value={"tags": []}) as send_prompt, \
             patch.object(main, "validate_output_contract", side_effect=results):
            main._run(definition, run_date=date(2026, 9, 12))

        self.assertEqual(send_prompt.call_count, 2)
        self.assertEqual(send_prompt.call_args_list[0].args[2], "Write briefing")
        self.assertIn("did not pass validation", send_prompt.call_args_list[1].args[2])

    def test_definition_preserves_valid_output_contract(self) -> None:
        definition = main._cronprompt_definition({
            "name":      "Briefing",
            "chat_name": "Briefing",
            "schedule":  "08:00",
            "prompts": [{
                "prompt": "Write a briefing.",
                "output_contract": {
                    "type":      "markdown_sections",
                    "path":      "Briefings/{date}.md",
                    "min_items": 1,
                    "max_items": 3,
                },
            }],
        })

        self.assertEqual(definition["prompts"][0]["output_contract"]["path"], "Briefings/{date}.md")

if __name__ == "__main__":
    unittest.main()
