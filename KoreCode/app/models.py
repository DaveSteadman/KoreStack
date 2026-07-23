from __future__ import annotations

from pydantic import BaseModel


class WriteBody(BaseModel):
    content: str
    expected_modified_at: int | None = None
    expected_modified_at_ns: int | None = None
    expected_hash: str | None = None


class RootBody(BaseModel):
    root: str = ''


class SlashCommandBody(BaseModel):
    text:                      str
    current_mode:              str  = "chat"
    workspace_context_enabled: bool = True
    thread_path:               str  = "__workspace__"
    has_last_user_message:     bool = False


class SlashCommandCompleteBody(BaseModel):
    text:                      str
    current_mode:              str  = "chat"
    workspace_context_enabled: bool = True
    thread_path:               str  = "__workspace__"
    has_last_user_message:     bool = False
    limit:                     int  = 12


class ChatSendBody(BaseModel):
    path:                      str = "__workspace__"
    visible_text:              str
    prompt_override:           str
    mode:                      str = "chat"
    conversation_external_id:  str | None = None
    workspace_context_enabled: bool = True


class ChatFollowupBody(BaseModel):
    path:                      str = "__workspace__"
    prompt:                    str
    visible_text:              str = ""
    mode:                      str = "chat"
    conversation_external_id:  str | None = None
    outbound_sender_display:   str | None = None
    workspace_context_enabled: bool = True


class ChatRunCreateBody(BaseModel):
    mode:                      str = "chat"
    user_text:                 str
    thread_path:               str = "__workspace__"
    active_path:               str = "."
    selection:                 str | None = None
    cursor:                    dict | None = None
    conversation_external_id:  str | None = None
    workspace_context_enabled: bool = True
    max_mention_count:         int = 4
    max_mention_file_chars:    int = 7000
    work_item_id:              str | None = None


class WorkItemCreateBody(BaseModel):
    title:       str
    description: str = ""
    scope:       list[str] = []
    constraints: list[str] = []


class WorkItemUpdateBody(BaseModel):
    title:       str | None = None
    description: str | None = None
    status:      str | None = None
    scope:       list[str] | None = None
    constraints: list[str] | None = None
    plan:        list | None = None
    evidence:    list | None = None
    outcome:     str | None = None


class ContinueRunCreateBody(BaseModel):
    thread_path:               str = "__workspace__"
    active_path:               str
    prefix:                    str
    suffix:                    str = ""
    offset:                    int = 0
    conversation_external_id:  str | None = None
    workspace_context_enabled: bool = True


class ChatWorkspaceContextBody(BaseModel):
    conversation_external_id: str | None = None
    enabled: bool = True


class ChatPromptBuildBody(BaseModel):
    mode:                      str = "chat"
    user_text:                 str
    path:                      str = "."
    selection:                 str | None = None
    cursor:                    dict | None = None
    workspace_context_enabled: bool = True
    max_mention_count:         int = 4
    max_mention_file_chars:    int = 7000


class ChatToolFollowupPromptBody(BaseModel):
    mode:               str = "chat"
    path:               str = "."
    user_text:          str
    previous_response:  str
    tool_results:       list = []
    execution_contract: dict | None = None


class ChatToolExecuteBody(BaseModel):
    tool_requests:             list[dict] = []
    active_path:               str | None = None
    workspace_context_enabled: bool = True
    run_id:                    str | None = None


class PythonExecutionBody(BaseModel):
    path:            str
    mode:            str = "run"
    timeout_seconds: int | None = None


class EditProposalCreateBody(BaseModel):
    edits:   list[dict] = []
    run_id:  str | None = None
    source:  str = "assistant"
    summary: str = ""


class PythonFunctionReplaceBody(BaseModel):
    path:          str
    symbol:        str
    replacement:   str
    expected_hash: str


class PythonFunctionInsertBody(BaseModel):
    path:          str
    source:        str
    after_symbol:  str | None = None
    into_class:    str | None = None
    expected_hash: str
