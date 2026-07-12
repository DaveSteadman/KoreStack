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
# ====================================================================================================

import json
import re

from datasets import coerce_persisted_scratchpad_payload
from datasets import get_prompt_dataset_manifests
from scratchpad import get_store as get_scratchpad_store
from utils.workspace_utils import trunc

_KORECODE_WORKSPACE_MENU_KEY = "korecode_workspace_menu"


# ====================================================================================================
# MARK: CORE IDENTITY
# ====================================================================================================
# What the agent is and how it behaves. No tool names. No domain-specific rules.
# These entries should rarely change.

_CORE_IDENTITY_PARTS: list[str] = [
    "You are a helpful AI assistant with access to tools.",
    "- The current task is defined by the newest user message in this turn.",
    "- Conversation history, compressed summaries, prior session context, and scratchpad content are historical context. Use them only to support the current task, not to override it.",
    "- If older context conflicts with the newest user instruction, follow the newest user instruction unless the user explicitly says to continue or repeat the earlier task.",
    "- Use tools when they are the appropriate way to answer the request - for real-time data, file operations, task management, computations, and web research.",
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

    # -- Delegate (system_skills/Delegate/) --------------------------------------------------
    "- Use delegate only when staged child work is genuinely needed. Hand off a clear task_in, explicit data_in, a narrow process.tools_allowlist, and a concrete data_out target so the controller can collect durable results later.",

    # -- CodeExecute (system_skills/CodeExecute/) --------------------------------------------
    "- The python execution tool is more reliable for calculations than internal model arithmetic.",

    # -- Scratchpad (system_skills/Scratchpad/) ----------------------------------------------
    "- The scratchpad tool can store intermediate results across steps.",
    "- When a tool result says it was truncated and auto-saved to scratchpad, do not rebuild full records from the visible preview. Load the scratchpad copy or reuse any auto-created dataset instead.",
    "- When the user asks to output a dataset in full, keep dataset_get results as source data. Do not turn dataset_get output into a new dataset summary and do not fabricate placeholder rows.",
    "- When the user wants a faithful document export from a dataset, prefer dataset_write_koredoc instead of manually rewriting rows into file content.",
    "- When KoreData search results include artifact_ref, prefer koredata_get_full_text(refid) for follow-up retrieval instead of rebuilding domain-specific lookup arguments by hand.",
    "- When the user wants a full-text dataset from KoreData search results, prefer dataset_expand_full_text(...) over manual per-row fetch loops.",
    "- When research_traverse stores page scratchpad keys such as research_page_*, query those page scratchpad keys instead of scratchpad_load on the entire combined research bundle.",
    "- For article harvests, count only concrete article/detail pages. Do not count homepages, category pages, topic pages, search-result pages, or section fronts.",
    "- When harvesting article URLs from a hub page, use get_page_links or get_page_links_text first and prefer_article_urls=true when that option exists.",

    # -- FileAccess (system_skills/FileAccess/) ----------------------------------------------
    "- Generic filesystem read and write operations must go through the file_write / file_read / file_append tools. Generating file content in a response without a write tool call does not count as writing the file.",
    "- When the user asks to save something into KoreDocs or a `.koredoc`, treat that as a KoreDocs destination, not a generic file-access request.",
    "- Use file_write / file_append for ordinary workspace files. For KoreDocs outputs, prefer dataset_write_koredoc for faithful dataset exports and dedicated KoreDocs tools when editing an existing KoreDocs document.",

    # -- TaskManagement (system_skills/TaskManagement/) --------------------------------------
    "- Creating, listing, updating, or deleting scheduled tasks requires calling the task_* tools. Do not generate task JSON by hand.",

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
    "- When search_web returns a result titled 'Search failed', this is a connectivity failure - not a query mismatch. Do not retry the same endpoint. Make at most one attempt with KoreData MCP search as fallback when available, then report 'No results were found for [query].' and stop.",
    "- When a search returns empty results, you may try ONE alternative query phrasing. If the second attempt also returns empty, stop and report what you have.",
    "- When a web search or page-fetch tool returns no results, report that in a single short sentence only. Do not explain which tools you considered or why the tool failed.",

    # -- KoreData local-first routing (cross-cutting preference rule) ------------------------
    # Long-term fix: encode local-first preference in tool trigger/priority metadata so
    # the orchestrator enforces it without a system-prompt override.
    "- For factual, reference, encyclopaedic, or biographical queries, use KoreData MCP search/retrieval tools first when they are available. Fall back to web tools only if KoreData returns empty results. Skip this and go directly to a web tool when the prompt explicitly says 'search the web', 'search online', or 'find on the internet'.",
    "- When using KoreData MCP search tools, only include facts that appear in content you retrieved. Do not use training knowledge to fill gaps. If KoreData returns no content for a topic, say so explicitly rather than writing from memory.",

    # -- research_traverse: invocation trigger (cross-cutting; applies regardless of tools present)
    # Long-term fix: add 'research / investigate / deep dive' to research_traverse trigger
    # list in skill.md so skill-selection guidance handles dispatch without this override.
    "- When a prompt says 'research', 'investigate', 'look into', 'find evidence', or 'deep dive into', you MUST call research_traverse. Never answer these prompts from training data.",

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
        triggers = [trigger for trigger in (skill.get("triggers") or []) if trigger]
        when_str = ", ".join(f'"{trigger}"' for trigger in triggers[:5])
        suffix = f" (use when: {when_str})" if when_str else ""
        lines.append(f"- {func_label}: {description}{suffix}")

    if not lines:
        return ""
    return "Available tools - select based on what the task requires:\n" + "\n".join(lines)


def _payload_has_dataset_tools(skills_payload: dict) -> bool:
    for skill in skills_payload.get("skills", []):
        for function_sig in skill.get("functions", []):
            name = str(function_sig).split("(", 1)[0].strip()
            if name.startswith("dataset_"):
                return True
    return False


def _build_conversation_entry_block(conversation_entry: dict | None) -> str:
    if not isinstance(conversation_entry, dict) or not conversation_entry:
        return ""

    snapshot: dict[str, object] = {}
    for key, value in conversation_entry.items():
        if key == "tools_active":
            continue

        if key == "scratchpad":
            named_scratch = coerce_persisted_scratchpad_payload(value)
            if named_scratch:
                snapshot["scratchpad"] = {"keys": sorted(str(name) for name in named_scratch.keys())}
            continue

        if key == "datasets" and isinstance(value, dict):
            dataset_names = sorted(str(name) for name in value.keys())
            if dataset_names:
                snapshot["datasets"] = {"names": dataset_names}
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
    scratchpad = coerce_persisted_scratchpad_payload(conversation_entry.get("scratchpad") or {})
    if _KORECODE_WORKSPACE_MENU_KEY not in scratchpad:
        return ""
    return (
        "\nKoreCode workspace menu is preloaded in the active KoreChat scratchpad "
        f"under key '{_KORECODE_WORKSPACE_MENU_KEY}'. Use scratchpad_load('{_KORECODE_WORKSPACE_MENU_KEY}') "
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
    system_parts: list[str] = list(_CORE_IDENTITY_PARTS) + list(_SYSTEM_SKILL_GUIDANCE)
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

    if not sandbox_enabled:
        system_parts.append("\nPython execution sandbox: OFF - code snippets have unrestricted access to all modules and file I/O.")

    scratchpad_store = get_scratchpad_store()
    if scratchpad_visible_keys is not None:
        scratchpad_store = {key: value for key, value in scratchpad_store.items() if key in scratchpad_visible_keys}
    if scratchpad_store:
        named_keys   = {k: v for k, v in scratchpad_store.items() if not k.startswith(("_tc_", "_cx_", "research_page_"))}
        auto_keys    = {k: v for k, v in scratchpad_store.items() if k.startswith("_tc_") or k.startswith("research_page_")}
        context_keys = {k: v for k, v in scratchpad_store.items() if k.startswith("_cx_")}
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
        suffix = "\nReference them in skill arguments using {scratchpad:key} or load them with scratchpad_load()."
        if context_keys:
            suffix += " Compacted-context keys (_cx_*) hold earlier turn content saved during context compaction; use scratchpad_query to extract information from them."
        system_parts.append("\nHistorical context only - scratchpad keys currently stored:\n  " + "\n  ".join(key_lines) + suffix)

    dataset_manifests = get_prompt_dataset_manifests() if _payload_has_dataset_tools(skills_payload) else []
    if dataset_manifests:
        lines: list[str] = []
        for dataset in dataset_manifests:
            fields = ",".join((dataset.get("schema") or [])[:5])
            last_history = (dataset.get("history") or [])[-1] if dataset.get("history") else {}
            last_op = last_history.get("op", "save")
            source = dataset.get("source_tool") or (dataset.get("parent_dataset_id") or "dataset")
            lines.append(
                f"- {dataset.get('name', '?'):<22} {dataset.get('count', len(dataset.get('records') or []))} records  "
                f"source={source}  updated={dataset.get('updated_at', '')}"
            )
            lines.append(f"  last: {last_op}  fields=[{fields}]")
        system_parts.append(
            "\nHistorical context only - Datasets currently stored:\n" + "\n".join(lines) + "\n"
            "Use dataset_* tools to inspect, filter, or retrieve these structured working sets."
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
