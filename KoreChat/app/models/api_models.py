# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# api models module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Primary types: ConversationCreateRequest, ConversationPatchRequest, DefaultChatCullRequest, InputHistoryAppendRequest, TurnAppendRequest, MessageAppendRequest, MessagePatchRequest, EventCreateRequest, EventCompleteRequest.
# Function inventory: this module exposes package or declarative configuration only.
# ====================================================================================================

from pydantic import BaseModel
from pydantic import Field


class ConversationCreateRequest(BaseModel):
    channel_type: str
    subject: str | None = None
    protected: bool | None = None
    background_context: str = ""
    profile: str | None = None
    external_id: str | None = None
    tools_active: list[str] | None = None


class ConversationPatchRequest(BaseModel):
    channel_type: str | None = None
    status: str | None = None
    subject: str | None = None
    protected: bool | None = None
    thread_summary: str | None = None
    scratchpad: dict | None = None
    datasets: dict | None = None
    working_data: dict | None = None
    tools_active: list[str] | None = None
    file_cwd: str | None = None
    background_context: str | None = None
    token_estimate: int | None = None
    turn_count: int | None = None


class DefaultChatCullRequest(BaseModel):
    max_default_chat_age_days: int = 7


class InputHistoryAppendRequest(BaseModel):
    text: str


class TurnAppendRequest(BaseModel):
    inbound_content:             str
    outbound_content:            str
    inbound_sender:              str = ""
    outbound_sender:             str = "agent"
    token_estimate:              int | None = None
    outbound_metadata:           dict | None = None
    inbound_tags:                list[str] = Field(default_factory=list)
    outbound_tags:               list[str] = Field(default_factory=list)
    outbound_delivery_eligible: bool = True


class MessageAppendRequest(BaseModel):
    direction: str
    content: str
    sender_display: str = ""
    status: str = "received"
    delivery_eligible: bool = True
    response_payload: dict | None = None
    metadata: dict | None = None
    tags: list[str] = Field(default_factory=list)


class MessagePatchRequest(BaseModel):
    status: str | None = None
    tags: list[str] | None = None


class EventCreateRequest(BaseModel):
    conversation_id: int | None = None
    event_type: str
    priority: int = 0
    payload: dict = Field(default_factory=dict)


class EventCompleteRequest(BaseModel):
    status: str = "completed"
