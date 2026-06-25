# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Database helpers for KoreChat/app.
# Owns persistence access patterns, schema-facing helpers, and storage utilities for this component.
# ====================================================================================================

from app.config import cfg

from .db_common import _claimable_event_types_for_consumer
from .db_common import _conn
from .db_common import _decode_json_value
from .db_common import _decode_session_state_fields
from .db_common import _default_profile
from .db_common import _is_protected_subject
from .db_common import _now
from .db_common import _row_to_dict
from .db_common import CLAIM_TIMEOUT_SECS
from .db_common import get_db_path
from .db_common import reset_runtime_state
from .db_conversations import conversation_append_input_history
from .db_conversations import conversation_counts
from .db_conversations import conversation_create
from .db_conversations import conversation_cull_default_inactive
from .db_conversations import conversation_delete
from .db_conversations import conversation_get
from .db_conversations import conversation_get_by_external_id
from .db_conversations import conversation_get_detail
from .db_conversations import conversation_get_input_history
from .db_conversations import conversation_get_turns_by_external_id
from .db_conversations import conversation_get_with_messages
from .db_conversations import conversation_list
from .db_conversations import conversation_set_input_history
from .db_conversations import conversation_update
from .db_events import clear_stale_outbound_ready
from .db_events import event_claim_next
from .db_events import event_complete
from .db_events import event_counts
from .db_events import event_create
from .db_events import event_list
from .db_events import release_stale_claims
from .db_messages import _conversation_has_unanswered_inbound_tx
from .db_messages import _latest_message_tx
from .db_messages import clear_pending_response_needed_events
from .db_messages import conversation_append_turn
from .db_messages import conversation_has_unanswered_inbound
from .db_messages import ensure_response_needed_event
from .db_messages import message_append
from .db_messages import message_list
from .db_messages import message_update
from .db_schema import init_db


__all__ = [
    "cfg",
    "CLAIM_TIMEOUT_SECS",
    "_conn",
    "_now",
    "_row_to_dict",
    "_decode_json_value",
    "_decode_session_state_fields",
    "_default_profile",
    "_is_protected_subject",
    "_claimable_event_types_for_consumer",
    "_latest_message_tx",
    "_conversation_has_unanswered_inbound_tx",
    "reset_runtime_state",
    "get_db_path",
    "init_db",
    "conversation_create",
    "conversation_get_by_external_id",
    "conversation_get",
    "conversation_get_turns_by_external_id",
    "conversation_get_detail",
    "conversation_get_with_messages",
    "conversation_list",
    "conversation_update",
    "conversation_get_input_history",
    "conversation_set_input_history",
    "conversation_append_input_history",
    "conversation_delete",
    "conversation_cull_default_inactive",
    "conversation_counts",
    "message_append",
    "conversation_append_turn",
    "conversation_has_unanswered_inbound",
    "ensure_response_needed_event",
    "clear_pending_response_needed_events",
    "message_list",
    "message_update",
    "event_create",
    "event_claim_next",
    "event_complete",
    "release_stale_claims",
    "event_list",
    "event_counts",
    "clear_stale_outbound_ready",
]
