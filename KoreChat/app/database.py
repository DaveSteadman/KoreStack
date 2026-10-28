# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Database helpers for KoreChat/app.
# Owns persistence access patterns, schema-facing helpers, and storage utilities for this component.
# MARK: FUNCTIONS
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

from app.config import cfg
from app.db import CLAIM_TIMEOUT_SECS
from app.db import clear_pending_response_needed_events
from app.db import clear_stale_outbound_ready
from app.db import conversation_append_input_history
from app.db import conversation_clear_history
from app.db import conversation_append_turn
from app.db import conversation_counts
from app.db import conversation_create
from app.db import conversation_cull_default_inactive
from app.db import conversation_delete
from app.db import conversation_get
from app.db import conversation_get_by_external_id
from app.db import conversation_get_detail
from app.db import conversation_get_input_history
from app.db import conversation_get_turns_by_external_id
from app.db import conversation_get_with_messages
from app.db import conversation_has_unanswered_inbound
from app.db import conversation_list
from app.db import conversation_set_input_history
from app.db import conversation_update
from app.db import ensure_response_needed_event
from app.db import event_claim_next
from app.db import event_complete
from app.db import event_counts
from app.db import event_create
from app.db import event_list
from app.db import get_db_path
from app.db import init_db
from app.db import message_append
from app.db import message_list
from app.db import message_update
from app.db import release_stale_claims
from app.db import reset_runtime_state

from app.db.common import _claimable_event_types_for_consumer
from app.db.common import _conn
from app.db.common import _decode_json_value
from app.db.common import _decode_session_state_fields
from app.db.common import _default_profile
from app.db.common import _is_protected_subject
from app.db.common import _now
from app.db.common import _row_to_dict
from app.db.messages import _conversation_has_unanswered_inbound_tx
from app.db.messages import _latest_message_tx

__all__ = [
    "CLAIM_TIMEOUT_SECS",
    "cfg",
    "clear_pending_response_needed_events",
    "clear_stale_outbound_ready",
    "conversation_append_input_history",
    "conversation_append_turn",
    "conversation_clear_history",
    "conversation_counts",
    "conversation_create",
    "conversation_cull_default_inactive",
    "conversation_delete",
    "conversation_get",
    "conversation_get_by_external_id",
    "conversation_get_detail",
    "conversation_get_input_history",
    "conversation_get_turns_by_external_id",
    "conversation_get_with_messages",
    "conversation_has_unanswered_inbound",
    "conversation_list",
    "conversation_set_input_history",
    "conversation_update",
    "ensure_response_needed_event",
    "event_claim_next",
    "event_complete",
    "event_counts",
    "event_create",
    "event_list",
    "get_db_path",
    "init_db",
    "message_append",
    "message_list",
    "message_update",
    "release_stale_claims",
    "reset_runtime_state",
    "_claimable_event_types_for_consumer",
    "_conn",
    "_conversation_has_unanswered_inbound_tx",
    "_decode_json_value",
    "_decode_session_state_fields",
    "_default_profile",
    "_is_protected_subject",
    "_latest_message_tx",
    "_now",
    "_row_to_dict",
]
