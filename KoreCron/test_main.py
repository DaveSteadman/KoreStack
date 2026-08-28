from __future__ import annotations

import unittest
from unittest.mock import patch

from KoreCron import main


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


if __name__ == "__main__":
    unittest.main()
