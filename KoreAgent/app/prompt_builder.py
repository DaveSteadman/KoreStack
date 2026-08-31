# MARK: OVERVIEW
# ====================================================================================================
# Assembles the system message sent to the LLM on every orchestration turn.
#
# Structure of build_system_message():
#   _CORE_IDENTITY_PARTS      -- who the agent is and how it behaves (stable, tool-agnostic)
#   _SYSTEM_SKILL_GUIDANCE    -- behavioral notes contributed by each system skill
#   _TOOL_ROUTING_FUDGE       -- cross-cutting routing rules (unconditional; tool-specific guidance belongs in skill.md)
#   dynamic blocks            -- memory, conversation summary, scratchpad, skill guidance
#
# _SYSTEM_SKILL_GUIDANCE is the proper home for any rule that names a system skill by
# capability. Ideally each entry would live in its skill module and be collected here
# dynamically, but Delegate and CodeExecute both import orchestration.py which imports
# prompt_builder.py - so dynamic collection would be circular. Static attribution here
# is the safe interim approach. Each cluster is labelled with its source skill.
#
# The fudge block exists because external skills do not yet carry routing metadata rich
# enough to drive dispatch automatically.
#
# RULE FOR INCLUSION: an entry belongs here only if it is cross-cutting behaviour that
# cannot sensibly live anywhere else. Tool-specific parameter guidance (fetch options,
# article discrimination, KoreDocs sheet preferences, sysinfo suppression) belongs in
# the relevant skill.md description instead. Delete entries here as skill.md files absorb them.
# MARK: FUNCTIONS
# Function inventory:
# - build_skill_selection_protocol_guidance: Builds Skill selection protocol guidance for this module.
# - build_skill_selection_guidance: Builds skill selection guidance for this module.
# - _payload_has_dataset_tools: Implements the  payload has dataset tools operation for this module.
# - _build_conversation_entry_block: Implements the  build conversation entry block operation for this module.
# - _build_korecode_workspace_menu_note: Implements the  build korecode workspace menu note operation for this module.
# - build_system_message: Builds system message for this module.
# ====================================================================================================

import json
import re

from working_data import coerce_persisted_working_data_payload
from working_data import get_prompt_working_data_collections
from working_data import get_working_data_values
from skill_manager import skill_manager
from utils.workspace_utils import trunc

_KORECODE_WORKSPACE_MENU_KEY = "korecode_workspace_menu"


def build_skill_selection_protocol_guidance() -> str:
    """State the live Skill-selection protocol without copying its whole index."""
    if not skill_manager.list_skills():
        return ""
    return (
        "\nTool selection protocol: schemas show only active tools. If no active tool explicitly "
        "names the requested capability, do not substitute a nearby generic tool. Call "
        "`skills_list()`, choose its exact Skill name, call "
        "`select_skills([...])`, then use the newly active function schemas. "
        "This applies even when a generic search, file, or document tool is already active."
    )


# ====================================================================================================
# MARK: CORE IDENTITY
# ====================================================================================================
# What the agent is and how it behaves. No tool names. No domain-specific rules.
# These entries should rarely change.

_CORE_IDENTITY_PARTS: list[str] = [
    "You are a helpful AI assistant with access to tools.",
    "- The current task is defined by the newest user message in this turn.",
    "- Conversation history, compressed summaries, prior session context, and Working Data are historical context. Use them only to support the current task, not to override it.",
    "- If older context conflicts with the newest user instruction, follow the newest user instruction unless the user explicitly says to continue or repeat the earlier task.",
    "- Never continue an earlier task merely because it appears in conversation history. A newest message such as 'hi' is a greeting, not permission to resume earlier work.",
    "- Use tools when they are the appropriate way to answer the request - for real-time data, file operations, computations, and web research.",
    "- After using tools, synthesize the results into a clear, direct answer.",
    "- Never claim a tool action succeeded unless the tool output explicitly confirms it.",
    "- Do not add explanatory preamble. Your response must contain ONLY the answer - no planning notes, self-commentary, or reasoning steps such as 'We should...', 'Let me...', 'Thus we...', 'Let's retrieve...', or 'We can produce...'.",
    "- Complete ALL steps in the user's request. If output must be written to a file, that write must happen as a tool call before you give your final answer.",
    "- When the user asks for an exact number of items, sections, stories, rows, or a target length such as a word count, treat that as a hard requirement. Do not silently reduce the scope.",
    "- Placeholder text such as 'TBD', 'remaining items', 'future update', or a partial subset does not satisfy a report-writing request. If the required material cannot be gathered, say that explicitly instead of writing a shortened deliverable as if it were complete.",
    "- Enumerate ONLY the tools present in your current tool schema (the functions you were given at the start of this turn). Do not recall tool names from training memory or prior conversations. If a tool is not in your current schema, it is not available.",
]


# ====================================================================================================
# MARK: SYSTEM SKILL GUIDANCE
# ====================================================================================================
# Behavioral notes contributed by each system skill (system_skills/).
# These entries name a specific system capability, which is why they cannot live in core identity.
# One cluster per skill.
# and has no static entry here.
#
# Note: dynamic collection would be cleaner but causes a circular import via orchestration.py.
# Until that is resolved, guidance is duplicated here with attribution comments.

_SYSTEM_SKILL_GUIDANCE: list[str] = [

    # -- CodeExecute (system_skills/CodeExecute/) --------------------------------------------
    "- Use python execution only for deterministic computation, parsing, validation, or transformations that genuinely require code. Do not use it to draft, rank, or format a narrative response, report, email, summary, or other editorial output from supplied material.",

    # -- WorkingData (system_skills/WorkingData/) ---------------------------------------------
    "- Working Data stores text and record collections across steps outside the active prompt.",
    "- When a tool result says it was auto-saved to Working Data, inspect it first, then use bounded working_data_get, working_data_rank, working_data_select, or working_data_fetch_full_text instead of rebuilding it from a preview.",
    "- When the user asks to output a Working Data collection in full, retain the source records and do not fabricate placeholder rows.",
    "- When KoreData search results include artifact_ref, prefer koredata_get_full_text(refid) for follow-up retrieval instead of rebuilding domain-specific lookup arguments by hand.",
    "- For a report from a Working Data collection: inspect it, rank or select the relevant records, fetch full text only for that small subset, then write the final answer directly. Do not load an entire collection's full text unless the user explicitly asks for it.",
    "- For article harvests, count only concrete article/detail pages. Do not count homepages, category pages, topic pages, search-result pages, or section fronts.",
    "- When harvesting article URLs from a hub page, use get_page_links or get_page_links_text first and prefer_article_urls=true when that option exists.",

    # -- FileAccess (system_skills/FileAccess/) ----------------------------------------------
    "- Generic filesystem read and write operations must go through the file_write / file_read / file_append tools. Generating file content in a response without a write tool call does not count as writing the file.",
    "- When the user asks to save something into KoreDocs or a `.koredoc`, treat that as a KoreDocs destination, not a generic file-access request.",
    "- Use file_write / file_append for ordinary workspace files. For KoreDocs outputs, use dedicated KoreDocs tools when editing an existing KoreDocs document.",


    "- In user-facing plan outputs, identify work primarily as `Task <number>` and include the title and status (for example, `Task 3 — Data Synthesis — active`). Do not make internal slug IDs the main visible identifier.",

    # -- ToolSelection (system_skills/ToolSelection/) ----------------------------------------
    "- The currently visible tool schema is only the active working set. When the needed capability is missing, use the tool-selection control skill to inspect the larger catalog and activate the specific tools you need.",
]


# ====================================================================================================
# MARK: TOOL ROUTING FUDGE (intent-gated)
# ====================================================================================================
# Each entry is (tag, text).
#   tag=None -> always included regardless of prompt content.
#   tag=str  -> included only when that intent tag is active for this prompt.
#
# Intent tags are resolved by _detect_routing_intents() from the live user prompt.
# This replaces the previous unconditional _TOOL_ROUTING_FUDGE list: only the entries
# relevant to the actual query are injected, keeping every other prompt shorter.
#
# Long-term fix for each cluster: move routing logic into the tool definition or skill.md
# so this block can be removed entirely. Tags here are the interim mechanism.

_TOOL_ROUTING_FUDGE: list[str] = [

    # -- Search and tool failure handling (cross-cutting; applies to all search/fetch tools) --
    "- When search_web returns a result titled 'Search failed', this is a connectivity failure - not a query mismatch. Do not retry the same endpoint. Make at most one attempt with koredata_search as fallback when available, then report 'No results were found for [query].' and stop.",
    "- When a search returns empty results, you may try ONE alternative query phrasing. If the second attempt also returns empty, stop and report what you have.",
    "- When a web search or page-fetch tool returns no results, report that in a single short sentence only. Do not explain which tools you considered or why the tool failed.",
    "- When any search or retrieval tool returns relevant results, that retrieved content has higher precedence than internal knowledge.",
    "- Do not override, contradict, or dilute retrieved evidence with internal knowledge.",
    "- Internal knowledge may supplement retrieved content only to fill minor gaps and only when it does not conflict with the retrieved material.",
    "- If retrieved material appears incomplete for the user's request, prefer another targeted retrieval or page fetch before relying on internal knowledge.",
    "- Search result snippets, headlines, and summaries are discovery aids, not authoritative evidence for factual synthesis.",
    "- For substantive factual answers, prefer fetched or retrieved source content over search-result snippets alone.",

    # -- KoreData local-first routing (cross-cutting preference rule) ------------------------
    # Long-term fix: encode local-first preference in tool trigger/priority metadata so
    # the orchestrator enforces it without a system-prompt override.
    "- For factual, reference, encyclopaedic, or biographical queries, use KoreData search and retrieval skills first when they are available. Fall back to web tools only if KoreData returns empty results. Skip this and go directly to a web tool when the prompt explicitly says 'search the web', 'search online', or 'find on the internet'.",
    "- A KoreData SavedSearch is a named stored search definition. When the user names a SavedSearch, call koredata_savedsearch_run with that name. Never reinterpret the SavedSearch name as a keyword query or web query unless the user explicitly requests a separate web search.",
    "- When using KoreData search tools, only include facts that appear in content you retrieved. Do not use training knowledge to fill gaps. If KoreData returns no content for a topic, say so explicitly rather than writing from memory.",

    # -- Date anchoring (cross-cutting; applies to any tool that returns time-sensitive data) -
    "- Treat words like 'latest', 'recent', 'today', 'current', and 'new' as date-sensitive. Anchor them to the current runtime date already provided in system context. Do not invent year ranges unless the user explicitly requests them.",
]


# ====================================================================================================
# MARK: SKILL SELECTION GUIDANCE
# ====================================================================================================
def build_skill_selection_guidance(skills_payload: dict) -> str:
    lines: list[str] = []
    for skill in skills_payload.get("skills", []):
        purpose = (skill.get("purpose") or "").strip()
        if not purpose:
            continue

        seen_names: set[str] = set()
        unique_funcs: list[str] = []
        for function_sig in skill.get("functions", []):
            if "(" not in function_sig:
                continue
            name = function_sig.split("(")[0].strip()
            if name and name not in seen_names and not name.startswith("list_"):
                seen_names.add(name)
                unique_funcs.append(name)

        if not unique_funcs:
            continue

        sentences = re.split(r"(?<=[.!?])\s+", purpose)
        description = sentences[0].lstrip("- ").strip()
        if len(description) > 160:
            description = description[:157] + "..."

        func_label = " / ".join(f"`{name}`" for name in unique_funcs[:3])
        lines.append(f"- {func_label}: {description}")

    if not lines:
        return ""
    return "Available tools - select based on what the task requires:\n" + "\n".join(lines)


def _payload_has_working_data_tools(skills_payload: dict) -> bool:
    for skill in skills_payload.get("skills", []):
        for function_sig in skill.get("functions", []):
            name = str(function_sig).split("(", 1)[0].strip()
            if name.startswith("working_data_"):
                return True
    return False


def _build_conversation_entry_block(conversation_entry: dict | None) -> str:
    if not isinstance(conversation_entry, dict) or not conversation_entry:
        return ""

    snapshot: dict[str, object] = {}
    for key, value in conversation_entry.items():
        if key == "tools_active":
            continue

        if key == "working_data":
            working_data = coerce_persisted_working_data_payload(value)
            value_names = sorted(str(name) for name in working_data["values"])
            collection_names = sorted(str(name) for name in working_data["collections"])
            if value_names or collection_names:
                snapshot["working_data"] = {"values": value_names, "collections": collection_names}
            continue

        if key in {"scratchpad", "datasets"}:
            continue

        if key == "background_context":
            text = str(value or "").strip()
            if text:
                snapshot["background_context"] = {
                    "chars": len(text),
                    "preview": trunc(text, 500),
                }
            continue

        if key == "messages" and isinstance(value, list):
            snapshot["messages"] = {"count": len(value)}
            continue

        if isinstance(value, str):
            snapshot[key] = trunc(value, 500)
            continue

        snapshot[key] = value

    if not snapshot:
        return ""

    rendered = json.dumps(snapshot, ensure_ascii=True, indent=2, sort_keys=True)
    return f"\nActive KoreChat conversation entry:\n{rendered}"


def _build_korecode_workspace_menu_note(conversation_entry: dict | None) -> str:
    if not isinstance(conversation_entry, dict):
        return ""
    working_data = coerce_persisted_working_data_payload(
        conversation_entry.get("working_data") or {},
    )
    if _KORECODE_WORKSPACE_MENU_KEY not in working_data["values"]:
        return ""
    return (
        "\nKoreCode workspace menu is preloaded in Working Data "
        f"under key '{_KORECODE_WORKSPACE_MENU_KEY}'. Use working_data_get('{_KORECODE_WORKSPACE_MENU_KEY}') "
        "when you need the generated workspace file/function inventory."
    )


def build_system_message(
    ambient_system_info: str,
    session_context,
    skills_payload: dict,
    *,
    skill_guidance_enabled: bool,
    sandbox_enabled: bool,
    conversation_entry: dict | None = None,
    scratchpad_visible_keys: list[str] | None = None,
    user_prompt: str | None = None,
    token_pressure: float = 0.0,
) -> str:
    system_parts: list[str] = list(_CORE_IDENTITY_PARTS)
    # Keep this ahead of the longer per-system-skill guidance.  The backend has
    # reported a smaller effective prompt budget than the requested context.
    skill_selection_guidance = build_skill_selection_protocol_guidance()
    if skill_selection_guidance:
        system_parts.append(skill_selection_guidance)
    system_parts.extend(_SYSTEM_SKILL_GUIDANCE)
    if ambient_system_info:
        system_parts.append(f"\n{ambient_system_info}")

    conversation_entry_block = _build_conversation_entry_block(conversation_entry)
    if conversation_entry_block:
        system_parts.append(conversation_entry_block)
    workspace_menu_note = _build_korecode_workspace_menu_note(conversation_entry)
    if workspace_menu_note:
        system_parts.append(workspace_menu_note)

    prior_inject = session_context.as_inject_block() if session_context else ""
    if prior_inject:
        system_parts.append(f"\nHistorical context only - prior session context:\n{prior_inject}")

    if skill_guidance_enabled:
        skill_guidance = build_skill_selection_guidance(skills_payload)
        if skill_guidance:
            system_parts.append(f"\n{skill_guidance}")

    if sandbox_enabled:
        system_parts.append(
            "\nPython execution sandbox: ON - use pure computation and safe stdlib modules such as math, "
            "statistics, datetime, json, re, and collections. os, sys, subprocess, open, file I/O, "
            "and third-party imports are blocked. Use dedicated file and system tools instead."
        )
    else:
        system_parts.append("\nPython execution sandbox: OFF - code snippets have unrestricted access to all modules and file I/O.")

    working_data_values = get_working_data_values()
    if scratchpad_visible_keys is not None:
        working_data_values = {key: value for key, value in working_data_values.items() if key in scratchpad_visible_keys}
    if working_data_values:
        named_keys   = {k: v for k, v in working_data_values.items() if not k.startswith(("_tc_", "_cx_", "_wd_", "research_page_"))}
        auto_keys    = {k: v for k, v in working_data_values.items() if k.startswith(("_tc_", "_wd_", "research_page_"))}
        context_keys = {k: v for k, v in working_data_values.items() if k.startswith("_cx_")}
        key_lines = []
        if named_keys:
            named_previews = []
            named_large    = []
            for key, value in sorted(named_keys.items()):
                rendered = str(value)
                if len(rendered) <= 120 and "\n" not in rendered:
                    named_previews.append(f"{key}={rendered}")
                else:
                    named_large.append(f"{key} ({len(rendered):,} chars)")
            if named_previews:
                key_lines.append("Named facts:       " + " | ".join(named_previews[:12]))
            if named_large:
                key_lines.append("Named values:      " + ", ".join(named_large[:12]))
        if auto_keys:
            key_lines.append("Auto-saved:        " + ", ".join(f"{key} ({len(value):,} chars)" for key, value in sorted(auto_keys.items())))
        if context_keys:
            key_lines.append("Compacted-context: " + ", ".join(f"{key} ({len(value):,} chars)" for key, value in sorted(context_keys.items())))
        suffix = "\nReference them in skill arguments using {working_data:key} or load them with working_data_get()."
        if context_keys:
            suffix += " Compacted-context keys (_cx_*) hold earlier turn content saved during context compaction; use working_data_query to extract information from them."
        system_parts.append("\nHistorical context only - Working Data values:\n  " + "\n  ".join(key_lines) + suffix)

    collection_manifests = get_prompt_working_data_collections() if _payload_has_working_data_tools(skills_payload) else []
    if collection_manifests:
        lines: list[str] = []
        for collection in collection_manifests:
            fields = ",".join((collection.get("schema") or [])[:5])
            last_history = (collection.get("history") or [])[-1] if collection.get("history") else {}
            last_op = last_history.get("op", "save")
            source = collection.get("source_tool") or (collection.get("parent_dataset_id") or "Working Data")
            lines.append(
                f"- {collection.get('name', '?'):<22} {collection.get('count', len(collection.get('records') or []))} records  "
                f"source={source}  updated={collection.get('updated_at', '')}"
            )
            lines.append(f"  last: {last_op}  fields=[{fields}]")
        system_parts.append(
            "\nHistorical context only - Working Data collections:\n" + "\n".join(lines) + "\n"
            "Use working_data_* tools to inspect, filter, or retrieve these collections."
        )

    # Token pressure warning — injected just before routing hint so it's near the top of
    # the model's attention but not the absolute last instruction.
    if token_pressure > 0.6:
        pct = int(token_pressure * 100)
        system_parts.append(
            f"\nNOTE: Context window is at {pct}% capacity. Prefer concise answers. "
            "Do not re-read content already loaded this session."
        )

    # Routing fudge: injected last for highest model attention.
    system_parts.append("\n" + "\n".join(_TOOL_ROUTING_FUDGE))

    return "\n".join(system_parts)
