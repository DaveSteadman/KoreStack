# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Manual interface adapter — synthetic channel for local testing via the WebUI.
#
# Inbound messages are injected by a human using the WebUI compose form.
# Outbound replies are acknowledged but not transmitted externally; the reply
# content lives in KoreChat and is visible through the chat UI.
#
# poll() is a no-op because no external system needs to be polled.
# route_reply() creates/updates a thread_id in the conversation record and logs the reply.
#
# Related modules:
#   - app/interfaces/common/base.py     -- BaseInterface ABC
#   - app/interfaces/common/registry.py -- registered as type "manual"
#   - app/database.py                   -- conversation and routing record access
# ====================================================================================================
from __future__ import annotations

import uuid

from app.interfaces.common.base import BaseInterface


class ManualInterface(BaseInterface):

    def poll(self) -> list[dict]:
        return []

    def route_reply(self, conversation_id: int, content: str) -> None:
        pass

    def send_new(self, recipient: str, subject: str, content: str) -> dict:
        thread_id = f"manual:{uuid.uuid4()}"
        return {
            "external_thread_id": thread_id,
            "external_message_id": f"{thread_id}:0",
        }