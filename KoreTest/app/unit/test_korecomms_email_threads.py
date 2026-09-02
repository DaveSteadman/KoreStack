# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Tests lazy, per-sender email reply conversations for newsletter deliveries.
# ====================================================================================================

from __future__ import annotations

import queue
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch


COMMS_ROOT = Path(__file__).resolve().parents[3] / "KoreComms"
if str(COMMS_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMS_ROOT))

from app import database
from app import poller
from app.interfaces.classic_email.adapter import ClassicEmailInterface


def _close_pool() -> None:
    while True:
        try:
            database._CONNECTION_POOL.get_nowait().close()
        except queue.Empty:
            return


class KoreCommsEmailThreadTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self._old_data_dir = database.cfg["data_dir"]
        _close_pool()
        database.cfg["data_dir"] = self._directory.name
        database._DB_PATH = None
        database._SCHEMA_READY = False

    def tearDown(self) -> None:
        _close_pool()
        database.cfg["data_dir"] = self._old_data_dir
        database._DB_PATH = None
        database._SCHEMA_READY = False
        self._directory.cleanup()

    def test_html_only_email_body_is_used(self) -> None:
        message = EmailMessage()
        message.set_content("<p>Please send details about Acme.</p>", subtype="html")

        self.assertEqual(ClassicEmailInterface._body(message), "Please send details about Acme.")

    def test_reply_reference_keeps_the_original_thread_identifier(self) -> None:
        raw = (
            b"From: Person <person@example.com>\r\n"
            b"Subject: Re: Newsletter\r\n"
            b"Message-ID: <new@example.com>\r\n"
            b"References: <original@example.com> <agent@example.com>\r\n"
            b"In-Reply-To: <agent@example.com>\r\n"
            b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
            b"Please tell me more."
        )

        data = ClassicEmailInterface._message_data("imap:1:1", raw)

        self.assertEqual(data["external_thread_id"], "<original@example.com>")
        self.assertEqual(data["reply_references"], ["<original@example.com>", "<agent@example.com>", "<agent@example.com>"])

    def test_newsletter_replies_create_one_conversation_per_sender(self) -> None:
        interface_id = database.interface_create("classic_email", "Mail", {})
        source_id = database.conversation_create(interface_id, chat_name="daily-newsletter")
        publication_id = database.email_publication_create(
            source_conversation_id = source_id,
            interface_id           = interface_id,
            subject                = "Daily newsletter",
            body                   = "Original newsletter content",
            deliveries             = [{"recipient": "alice@example.com", "message_id": "<newsletter-1@example.com>"}],
        )
        assert publication_id is not None

        delivery = database.email_delivery_find_by_references(interface_id, ["<newsletter-1@example.com>"])
        assert delivery is not None
        alice, alice_created = database.email_reply_conversation_get_or_create(interface_id, delivery, "Alice <alice@example.com>")
        alice_again, alice_again_created = database.email_reply_conversation_get_or_create(interface_id, delivery, "alice@example.com")
        bob, bob_created = database.email_reply_conversation_get_or_create(interface_id, delivery, "bob@example.com")

        self.assertTrue(alice_created)
        self.assertFalse(alice_again_created)
        self.assertTrue(bob_created)
        self.assertEqual(alice["id"], alice_again["id"])
        self.assertNotEqual(alice["id"], bob["id"])
        self.assertTrue(alice["external_thread_id"].startswith("newsletter-thread:"))
        self.assertEqual(database.email_reply_anchor(alice["id"]), "<newsletter-1@example.com>")
        self.assertEqual(delivery["body"], "Original newsletter content")

        database.external_message_create(alice["id"], "imap:1:reply", "inbound", "Alice <alice@example.com>")
        adapter = ClassicEmailInterface(interface_id, "Mail", {})
        with patch.object(adapter, "_send", return_value="<agent-1@example.com>") as send:
            adapter.route_reply(alice["id"], "Here are the details.")
        self.assertEqual(send.call_args.kwargs["in_reply_to"], "<newsletter-1@example.com>")

    def test_first_reply_seeds_original_newsletter_once(self) -> None:
        interface_id = database.interface_create("classic_email", "Mail", {})
        source_id = database.conversation_create(interface_id, chat_name="daily-newsletter")
        database.email_publication_create(
            source_conversation_id = source_id,
            interface_id           = interface_id,
            subject                = "Daily newsletter",
            body                   = "Original newsletter content",
            deliveries             = [{"recipient": "alice@example.com", "message_id": "<newsletter-1@example.com>"}],
        )
        messages = [
            {
                "external_message_id": "imap:1:1",
                "external_thread_id": "<newsletter-1@example.com>",
                "reply_references": ["<newsletter-1@example.com>"],
                "sender": "Alice <alice@example.com>",
                "subject": "Re: Daily newsletter",
                "content": "Please send more details.",
            },
            {
                "external_message_id": "imap:1:2",
                "external_thread_id": "<newsletter-1@example.com>",
                "reply_references": ["<newsletter-1@example.com>"],
                "sender": "Alice <alice@example.com>",
                "subject": "Re: Daily newsletter",
                "content": "And please include pricing.",
            },
        ]
        appended: list[dict] = []

        def append_message(**kwargs):
            appended.append(kwargs)
            return {"id": len(appended)}

        with patch.object(poller, "_resolve_kc_conversation", return_value={"id": 12}), \
             patch.object(poller.kc_client, "append_message", side_effect=append_message):
            for message in messages:
                poller._forward_message({"id": interface_id, "type": "classic_email"}, message)

        self.assertEqual([message["content"] for message in appended], [
            "Original newsletter content",
            "Please send more details.",
            "And please include pricing.",
        ])
        self.assertEqual(appended[0]["tags"], ["newsletter_context"])
