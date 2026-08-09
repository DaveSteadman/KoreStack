"""Classic email adapter using IMAP or POP3 for inbound mail and SMTP for delivery."""
from __future__ import annotations

import email
import html
import imaplib
import json
import logging
import poplib
import re
import smtplib
import ssl
import time
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import parseaddr

from app import crypto, database as db
from app.interfaces.common.base import BaseInterface

logger = logging.getLogger(__name__)

_SECRET_KEYS = ("incoming_password", "outgoing_password")
_HTML_TAG_RE = re.compile(r"</?(?:article|blockquote|body|br|div|h[1-6]|html|li|ol|p|table|td|th|tr|ul)\b", re.IGNORECASE)
_HTML_BREAK_RE = re.compile(r"</?(?:br|div|h[1-6]|li|p|tr)\b[^>]*>", re.IGNORECASE)
_HTML_STRIP_RE = re.compile(r"<[^>]+>")


class ClassicEmailInterface(BaseInterface):
    """Bridge a standards-based mailbox into KoreComms."""

    def _config(self) -> dict:
        raw = json.loads(self.config.get("config_json", "{}"))
        result = {}
        for key, value in raw.items():
            if key in _SECRET_KEYS and value:
                try:
                    result[key] = crypto.decrypt(value)
                except Exception:
                    result[key] = value
            else:
                result[key] = value
        return result

    @staticmethod
    def _text(value: str | None) -> str:
        if not value:
            return ""
        return str(make_header(decode_header(value)))

    @staticmethod
    def _looks_like_html(content: str) -> bool:
        return bool(_HTML_TAG_RE.search(content))

    @staticmethod
    def _html_to_text(content: str) -> str:
        text = _HTML_BREAK_RE.sub("\n", content)
        text = _HTML_STRIP_RE.sub("", text)
        return html.unescape(text).strip()

    @classmethod
    def _body(cls, message: Message) -> str:
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    return cls._body(part)
            return ""
        payload = message.get_payload(decode=True) or b""
        return payload.decode(message.get_content_charset() or "utf-8", errors="replace").strip()

    @staticmethod
    def _message_data(message_id: str, raw: bytes) -> dict:
        message = email.message_from_bytes(raw)
        sender = ClassicEmailInterface._text(message.get("From"))
        return {
            "external_message_id": message_id,
            "external_thread_id": message.get("Message-ID") or message_id,
            "sender": sender,
            "subject": ClassicEmailInterface._text(message.get("Subject")) or "(no subject)",
            "content": ClassicEmailInterface._body(message),
            "channel_type": "email",
        }

    @classmethod
    def _is_self_sent(cls, message: Message, cfg: dict) -> bool:
        """Ignore mail emitted by this connection to prevent mailbox feedback loops."""
        if message.get("X-KoreComms-Auto-Reply") == "1":
            return True
        sender = parseaddr(cls._text(message.get("From")))[1].casefold()
        own_addresses = {
            str(cfg.get(key) or "").strip().casefold()
            for key in ("incoming_username", "outgoing_username", "outgoing_from")
        }
        own_addresses.discard("")
        return bool(sender and sender in own_addresses)

    def _imap_messages(self, cfg: dict) -> list[dict]:
        client = imaplib.IMAP4_SSL(cfg["incoming_host"], int(cfg["incoming_port"]))
        try:
            client.login(cfg["incoming_username"], cfg["incoming_password"])
            client.select(cfg.get("incoming_mailbox") or "INBOX")
            search = (
                "OR UNSEEN SINCE " + time.strftime("%d-%b-%Y", time.localtime(time.time() - 86400))
                if cfg.get("import_recent_read")
                else "UNSEEN"
            )
            status, message_ids = client.search(None, search)
            if status != "OK":
                raise RuntimeError("IMAP unread-message search failed")
            messages: list[dict] = []
            cutoff = time.time() - 86400
            for sequence_id in message_ids[0].split():
                status, parts = client.fetch(sequence_id, "(UID FLAGS INTERNALDATE RFC822)")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                metadata = parts[0][0]
                if isinstance(metadata, str):
                    metadata = metadata.encode()
                if cfg.get("import_recent_read") and b"\\Seen" in metadata:
                    internal_date = imaplib.Internaldate2tuple(metadata)
                    if internal_date is None or time.mktime(internal_date) < cutoff:
                        continue
                uid = metadata.decode(errors="replace").split()[2]
                message_id = f"imap:{self.interface_id}:{uid}"
                message = email.message_from_bytes(parts[0][1])
                if self._is_self_sent(message, cfg):
                    logger.info("Ignoring self-sent IMAP message %s", message_id)
                    continue
                if not db.external_message_exists(message_id):
                    messages.append(self._message_data(message_id, parts[0][1]))
            return messages
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def _pop3_messages(self, cfg: dict) -> list[dict]:
        client = poplib.POP3_SSL(cfg["incoming_host"], int(cfg["incoming_port"]))
        try:
            client.user(cfg["incoming_username"])
            client.pass_(cfg["incoming_password"])
            messages: list[dict] = []
            for number, uid in client.uidl()[1]:
                message_id = f"pop3:{self.interface_id}:{uid.decode()}"
                if db.external_message_exists(message_id):
                    continue
                raw = b"\n".join(client.retr(int(number))[1]) + b"\n"
                if self._is_self_sent(email.message_from_bytes(raw), cfg):
                    logger.info("Ignoring self-sent POP3 message %s", message_id)
                    continue
                messages.append(self._message_data(message_id, raw))
            return messages
        finally:
            try:
                client.quit()
            except Exception:
                pass

    def poll(self) -> list[dict]:
        cfg = self._config()
        required = ("incoming_host", "incoming_port", "incoming_username", "incoming_password")
        if not all(cfg.get(key) for key in required):
            logger.warning("Classic email interface %d is missing incoming settings", self.interface_id)
            return []
        try:
            return self._imap_messages(cfg) if cfg.get("incoming_protocol") == "imap" else self._pop3_messages(cfg)
        except (imaplib.IMAP4.error, OSError, poplib.error_proto) as exc:
            logger.error("Classic email poll failed for interface %d: %s", self.interface_id, exc)
            return []

    def _send(self, recipient: str, subject: str, content: str, *, in_reply_to: str = "") -> str:
        cfg = self._config()
        required = ("outgoing_host", "outgoing_port", "outgoing_username", "outgoing_password")
        if not all(cfg.get(key) for key in required):
            raise RuntimeError("Classic email interface is missing SMTP settings")
        message = EmailMessage()
        message["From"] = cfg.get("outgoing_from") or cfg["outgoing_username"]
        message["To"] = recipient
        message["Subject"] = subject
        message["X-KoreComms-Auto-Reply"] = "1"
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
            message["References"] = in_reply_to
        if self._looks_like_html(content):
            message.set_content(self._html_to_text(content))
            message.add_alternative(content, subtype="html")
        else:
            message.set_content(content)
        context = ssl.create_default_context()
        if cfg.get("outgoing_security") == "ssl":
            client = smtplib.SMTP_SSL(cfg["outgoing_host"], int(cfg["outgoing_port"]), context=context, timeout=30)
        else:
            client = smtplib.SMTP(cfg["outgoing_host"], int(cfg["outgoing_port"]), timeout=30)
        try:
            client.ehlo()
            if cfg.get("outgoing_security") == "starttls":
                client.starttls(context=context)
                client.ehlo()
            client.login(cfg["outgoing_username"], cfg["outgoing_password"])
            client.send_message(message)
        finally:
            client.quit()
        return message["Message-ID"] or "smtp:sent"

    def route_reply(self, conversation_id: int, content: str) -> None:
        conv = db.conversation_get(conversation_id)
        inbound = db.external_message_get_last_inbound(conversation_id)
        if conv is None:
            raise RuntimeError(f"Conversation {conversation_id} not found")
        if conv.get("delivery_enabled") and conv.get("delivery_list_id"):
            recipients = db.distribution_list_members(int(conv["delivery_list_id"]))
            if not recipients:
                raise RuntimeError("Delivery list has no recipients")
            for recipient in recipients:
                self._send(recipient["email"], conv.get("delivery_subject") or "KoreComms report", content)
            return
        if conv.get("delivery_enabled") and conv.get("delivery_recipient"):
            self._send(conv["delivery_recipient"], conv.get("delivery_subject") or "KoreComms report", content)
            return
        if inbound is None:
            raise RuntimeError("Classic email reply has no inbound message to address")
        recipient = parseaddr(inbound["sender_display"])[1] or inbound["sender_display"]
        subject = conv.get("korechat_id") or "(no subject)"
        self._send(recipient, subject if subject.lower().startswith("re:") else f"Re: {subject}", content, in_reply_to=conv.get("external_thread_id") or "")

    def send_new(self, recipient: str, subject: str, content: str) -> dict:
        message_id = self._send(recipient, subject, content)
        return {"external_thread_id": message_id, "external_message_id": message_id}
