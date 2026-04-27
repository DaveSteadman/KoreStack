# MARK: OVERVIEW
# ====================================================================================================
# Assembles the system message sent to the LLM on every orchestration turn.
#
# Structure of build_system_message():
#   _CORE_IDENTITY_PARTS      -- who the agent is and how it behaves (stable, tool-agnostic)
#   _SYSTEM_SKILL_GUIDANCE    -- behavioral notes contributed by each system skill
#   _TOOL_ROUTING_FUDGE       -- per-tool routing workarounds (temporary scaffolding)
#   dynamic blocks            -- memory, conversation summary, scratchpad, skill guidance
#
# _SYSTEM_SKILL_GUIDANCE is the proper home for any rule that names a system skill by
# capability. Ideally each entry would live in its skill module and be collected here
# dynamically, but Delegate and CodeExecute both import orchestration.py which imports
# prompt_builder.py - so dynamic collection would be circular. Static attribution here
# is the safe interim approach. Each cluster is labelled with its source skill.
#
# The fudge block exists because external skills do not yet carry routing metadata rich
# enough to drive dispatch automatically. Each cluster is annotated with its intended
# destination. Delete entries here as the corresponding tool definitions absorb them.
# ====================================================================================================

import re

from KoreAgent.scratchpad import get_store as get_scratchpad_store


# ====================================================================================================
# MARK: CORE IDENTITY
# ====================================================================================================
# What the agent is and how it behaves. No tool names. No domain-specific rules.
# These entries should rarely change.

_CORE_IDENTITY_PARTS: list[str] = [
    "You are a helpful AI assistant with access to tools.",
    "- Use tools when they are the appropriate way to answer the request - for real-time data, file operations, task management, computations, and web research.",
    "- After using tools, synthesize the results into a clear, direct answer.",
    "- Never claim a tool action succeeded unless the tool output explicitly confirms it.",
    "- Do not add explanatory preamble. Your response must contain ONLY the answer - no planning notes, self-commentary, or reasoning steps such as 'We should...', 'Let me...', 'Thus we...', 'Let's retrieve...', or 'We can produce...'.",
    "- Complete ALL steps in the user's request. If output must be written to a file, that write must happen as a tool call before you give your final answer.",
        "- When the user asks for an exact number of items, sections, stories, rows, or a target length such as a word count, treat that as a hard requirement. Do not silently reduce the scope.",
        "- Placeholder text such as 'TBD', 'remaining items', 'future update', or a partial subset does not satisfy a report-writing request. If the required material cannot be gathered, say that explicitly instead of writing a shortened deliverable as if it were complete.",
]


# ====================================================================================================
# MARK: SYSTEM SKILL GUIDANCE
# ====================================================================================================
# Behavioral notes contributed by each system skill (system_skills/).
# These entries name a specific system capability, which is why they cannot live in core identity.
# One cluster per skill. Memory contributes dynamically (top_facts / recalled_memories blocks)
# and has no static entry here.
#
# Note: dynamic collection would be cleaner but causes a circular import via orchestration.py.
# Until that is resolved, guidance is duplicated here with attribution comments.

_SYSTEM_SKILL_GUIDANCE: list[str] = [

    # -- Delegate (system_skills/Delegate/) --------------------------------------------------
    "- For complex requests with multiple independent sub-problems, decompose at planning time: decide the breakdown first, then fire delegate calls for each part and synthesise the results. Each delegate gets its own isolated context and tool budget.",

    # -- CodeExecute (system_skills/CodeExecute/) --------------------------------------------
    "- The python execution tool is more reliable for calculations than internal model arithmetic.",

    # -- Scratchpad (system_skills/Scratchpad/) ----------------------------------------------
    "- The scratchpad tool can store intermediate results across steps.",

    # -- FileAccess (system_skills/FileAccess/) ----------------------------------------------
    "- Filesystem read and write operations must go through the file_write / file_read / file_append tools. Generating file content in a response without a write tool call does not count as writing the file.",

    # -- TaskManagement (system_skills/TaskManagement/) --------------------------------------
    "- Creating, listing, updating, or deleting scheduled tasks requires calling the task_* tools. Do not generate task JSON by hand.",
]


# ====================================================================================================
# MARK: TOOL ROUTING FUDGE
# ====================================================================================================
# Workarounds for the absence of proper routing metadata on individual tool definitions.
# Each cluster below is labelled with its intended long-term destination.
# As tools gain accurate descriptions and trigger signals, delete entries from here.

_TOOL_ROUTING_FUDGE: list[str] = [

    # -- reference MCP tools / research_traverse: factual and biographical content -----------
    # Long-term fix: add a "requires_lookup" flag to skill.md for these tools so the
    # orchestrator can enforce the lookup-before-answer rule without a system-prompt override.
    "- When a prompt asks about a person, place, event, concept, or historical figure - always call a research or lookup skill first. Never generate biographical, historical, or factual content from model knowledge.",

    # -- KoreData MCP tools: local-first reference routing -----------------------------------
    # Long-term fix: KoreData tool descriptions and a routing layer that checks
    # query intent before selecting between local and web tools.
    "- When the prompt explicitly names 'KoreData' as the target (e.g. 'search KoreData for', 'find in KoreData', 'KoreData library', 'KoreRAG'), use the available KoreData MCP search/retrieval tools first. Do not call search_web or research_traverse for these prompts.",
    "- For factual, reference, news, book, internal document, or encyclopaedic queries that do not explicitly say 'search the web' or 'search online', use KoreData MCP search/retrieval tools first when they are available. Fall back to web tools only if KoreData returns empty results.",
    "- When a prompt says 'search the web for', 'search online for', or 'find on the internet', call a web tool directly - skip KoreData.",
    "- When a prompt says 'search for', 'find information about', or 'look up' without specifying the web, use KoreData MCP search/retrieval tools first when they are available. Fall back to search_web only on empty results.",

    # -- KoreDocs MCP tools: generated documentation persistence -----------------------------
    # Long-term fix: KoreDocs tool descriptions should make create/update/storage intent
    # self-routing without these prompt-level reminders.
    "- When the user asks to create, save, store, publish, or update a document in KoreDocs, KoreFile, KoreFiles, or the documentation store, first produce the requested content, then call the appropriate KoreDocs MCP document tool. Do not use filesystem file_write or folder_create for these destinations unless the user explicitly asks for a plain filesystem path.",
    "- When the user asks for a spreadsheet, sheet, table, schedule, worksheet, compounding model, amortization model, or labelled-value update in KoreDocs, prefer the KoreDocs sheet tools end-to-end. If a folder is needed for that artifact, create it with KoreDocs rather than mixing filesystem folder tools with KoreDocs file creation.",
    "- For spreadsheet tasks, prefer semantic KoreDocs sheet tools such as sheet description, headers, row find/update, labelled values, and table creation before dropping to raw cell writes or whole-file replacement.",
    "- When the destination mentions KoreFiles or KoreFile, treat that as the KoreDocs storage surface. Prefer koredocs_create_folder, koredocs_create_koredoc, koredocs_create_file, and related koredocs_* tools over generic filesystem tools.",
    "- Do not use KoreDocs for general factual lookup unless the prompt is specifically about stored documentation. Use KoreData for reference retrieval and KoreDocs for documentation storage or editing.",

    # -- Date-sensitive queries (search_web, KoreData MCP tools) -----------------------------
    # Long-term fix: inject current date into each search tool call automatically so the
    # model never needs to be reminded to anchor to runtime date.
    "- Treat words like 'latest', 'recent', 'today', 'current', and 'new' as date-sensitive. Anchor them to the current runtime date already provided in system context. Do not invent year ranges unless the user explicitly requests them.",

    # -- search_web / search_web_text: article vs hub-page discrimination --------------------
    # Long-term fix: expose page_kind filtering in search_web so the tool itself filters
    # hub pages before returning results.
    "- When the user asks for article URLs, treat only concrete article/detail pages as valid results. Do not count homepages, category pages, topic pages, or search-result pages.",
    "- If search results are hub/listing pages, use get_page_links or get_page_links_text to extract concrete article URLs before calling fetch_page_text.",
    "- For article-harvest tasks, use prefer_article_urls=true on search_web when available, and inspect each result's page_kind field before treating it as an article.",

    # -- search_web / search_web_text: failure handling --------------------------------------
    # Long-term fix: tools should return structured error objects rather than a
    # 'Search failed' title so the orchestrator can handle failures without prompt instructions.
    "- When search_web returns a result titled 'Search failed', this is a connectivity failure - not a query mismatch. Do not retry the same endpoint. Make at most one attempt with KoreData MCP search as fallback when available, then report 'No results were found for [query].' and stop.",
    "- When a search returns empty results, you may try ONE alternative query phrasing. If the second attempt also returns empty, stop and report what you have.",
    "- When a web search or page-fetch tool returns no results, report that in a single short sentence only. Do not explain which tools you considered or why the tool failed.",

    # -- fetch_page_text: parameter guidance -------------------------------------------------
    # Long-term fix: move these into fetch_page_text's skill.md parameter descriptions
    # so they appear inline in the tool schema the model sees.
    "- If fetch_page_text returns HTTP 401/403, or only a bare title from a topic page, treat the URL as blocked and move on to a better candidate.",
    "- When using fetch_page_text for a narrow fact lookup, set the query parameter to your specific question.",
    "- For tasks requiring a complete list or many-year table scan, use fetch_page_text with a generous max_words value (2000-4000). Large fetches are auto-saved to the scratchpad - use scratch_query or scratch_peek on the saved key instead of repeating shallow fetches.",

    # -- research_traverse: invocation routing -----------------------------------------------
    # Long-term fix: add 'research', 'investigate', 'deep dive' to research_traverse
    # trigger list so the skill selection guidance handles dispatch without this override.
    "- When a prompt says 'research', 'investigate', 'look into', 'find evidence', or 'deep dive into', you MUST call research_traverse. Never answer these prompts from training data.",
    "- After research_traverse, prefer page scratch keys from best_pages/page_manifest. Use scratch_query or scratch_peek on specific research_page_* entries instead of scratch_load on the full bundle.",

    # -- KoreData MCP tools: grounding -------------------------------------------------------
    # Long-term fix: this should be enforced structurally - e.g. requiring source URL citations
    # so the model cannot include a fact without a matching retrieved URL.
    "- When using KoreData MCP search tools, only include facts that appear in content you retrieved with a matching KoreData retrieval tool. Do not use training knowledge to fill gaps. If KoreData returns no content for a topic, say so explicitly rather than writing from memory.",

    # -- system info: suppress redundant tool call -------------------------------------------
    # Long-term fix: add a guard in get_system_info_dict that returns cached data when
    # system info is already present in the prompt, making this instruction unnecessary.
    "- The current runtime system info (RAM, disk, OS, etc.) is already provided in context - do not call get_system_info_dict unless the user explicitly asks to refresh it.",
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


def build_system_message(
    ambient_system_info: str,
    session_context,
    skills_payload: dict,
    *,
    skill_guidance_enabled: bool,
    sandbox_enabled: bool,
    scratchpad_visible_keys: list[str] | None = None,
    conversation_summary: str | None = None,
    top_facts: str | None = None,
    recalled_memories: str | None = None,
) -> str:
    system_parts: list[str] = list(_CORE_IDENTITY_PARTS) + list(_SYSTEM_SKILL_GUIDANCE) + list(_TOOL_ROUTING_FUDGE)
    if ambient_system_info:
        system_parts.append(f"\n{ambient_system_info}")
    if top_facts:
        system_parts.append(f"\nKnown facts about this user and environment:\n{top_facts}")
    if conversation_summary:
        system_parts.append(f"\nPrior conversation summary (oldest exchanges, compressed):\n{conversation_summary}")

    prior_inject = session_context.as_inject_block() if session_context else ""
    if prior_inject:
        system_parts.append(f"\nPrior session context:\n{prior_inject}")

    if skill_guidance_enabled:
        skill_guidance = build_skill_selection_guidance(skills_payload)
        if skill_guidance:
            system_parts.append(f"\n{skill_guidance}")

    if recalled_memories:
        system_parts.append(f"\nMemories relevant to this prompt:\n{recalled_memories}")

    if not sandbox_enabled:
        system_parts.append("\nPython execution sandbox: OFF - code snippets have unrestricted access to all modules and file I/O.")

    scratch_store = get_scratchpad_store()
    if scratchpad_visible_keys is not None:
        scratch_store = {key: value for key, value in scratch_store.items() if key in scratchpad_visible_keys}
    if scratch_store:
        named_keys = {key: value for key, value in scratch_store.items() if not key.startswith("_tc_")}
        auto_keys = {key: value for key, value in scratch_store.items() if key.startswith("_tc_")}
        key_lines = []
        if named_keys:
            key_lines.append("Named:      " + ", ".join(f"{key} ({len(value):,} chars)" for key, value in sorted(named_keys.items())))
        if auto_keys:
            key_lines.append("Auto-saved: " + ", ".join(f"{key} ({len(value):,} chars)" for key, value in sorted(auto_keys.items())))
        system_parts.append("\nScratchpad keys currently stored:\n  " + "\n  ".join(key_lines) + "\nReference them in skill arguments using {scratch:key} or load them with scratch_load().")

    return "\n".join(system_parts)
