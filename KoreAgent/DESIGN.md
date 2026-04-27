# MiniAgentFramework - Design and Requirements

## Purpose
The is a requirements document, of defining statements around the functionality of MiniAgentFramework.

Each section names a component, states its intent, and lists verifiable behavioral claims.
A claim is verifiable when it is unambiguously true or false from reading the source.
Use this file to check whether the system matches its stated design, or to identify gaps.

---

## Table of contents

- [Architecture overview](#architecture-overview)
- [Server - LLM client](#server---llm-client)
- [Server - Orchestration pipeline](#server---orchestration-pipeline)
- [Server - Context control](#server---context-control)
- [Server - Skill system](#server---skill-system)
- [Server - Scheduler](#server---scheduler)
- [Server - API layer](#server---api-layer)
- [Server - Exit behavior](#server---exit-behavior)
- [Server - Slash commands](#server---slash-commands)
- [Server - Session persistence](#server---session-persistence)
- [Server - Testing](#server---testing)
- [UI - Layout and panels](#ui---layout-and-panels)
- [UI - Log panel](#ui---log-panel)
- [UI - Chat panel](#ui---chat-panel)
- [UI - Schedule timeline](#ui---schedule-timeline)
- [UI - Prompt input](#ui---prompt-input)

---

## Feature List

A list of the headline functional areas that define the project:
- Offline Agent Framework - a tool-calling agent pipeline with skill plugins, scratchpad memory, and delegate sub-tasks, all running locally without cloud dependencies.
- Ollama integration - thin HTTP wrapper over the Ollama REST API supporting local, LAN-hosted, and Ollama Cloud endpoints, with model resolution, health caching, and runtime host switching.
- Slash Commands - runtime control surface for model selection, context tuning, log management, session naming, and task control, dispatched from a registry and available in all input modes.
- Endless Chat - conversation persistence with rolling-window compaction that summarises older turns via an isolated LLM call, giving the model continuity across sessions of unlimited length.
- Built in test - automated regression runner that replays JSON prompt sequences, writes dated result files, and reports pass/fail trends across runs via `/testtrend`.

---

## Architecture overview

The system is divided into two layers with a clean interface between them.

- `KoreAgent/` is stateless with respect to sessions, users, and file I/O (except the scratchpad store, which is process-lifetime).
- `input_layer/` owns session identity, file persistence, and request routing.
- `KoreAgent/orchestration.py` is the single entry point: it accepts parameters and returns results; it never reads from files or knows what session it is in.

**Claims:**
- `orchestrate_prompt` accepts `conversation_history` as a parameter; it does not fetch it internally.
- `orchestrate_prompt` accepts `session_context` as a parameter; it does not create or persist one internally.
- No file I/O for session state occurs inside `KoreAgent/` (scratchpad dump is opt-in and explicitly controlled).
- The scratchpad store is a module-level dict shared across all calls within a process lifetime, including delegate children.

---

## Server - LLM client

**Files:** `code/KoreAgent/llm_client.py` (routing facade), `code/KoreAgent/llm_client_openai.py` (shared state and OpenAI-compatible core), `code/KoreAgent/llm_client_ollama.py` (Ollama-specific)

**Intent:** Multi-backend LLM client supporting Ollama and LM Studio. Manages host configuration, backend selection, model resolution, health checking, and raw LLM calls. All other modules import through the facade (`llm_client.py`); none make direct HTTP requests to the inference server.

**Claims:**
- Active host and backend are set at startup via `configure_host()` or `configure_server()`; all subsequent calls use the configured values.
- A health-check cache (`_ollama_health_cache`) avoids a round-trip on every call; TTL is 30 seconds. Shared by both backends.
- `resolve_model_name(token, available)` matches partial tokens against installed model names using a word-boundary regex; hyphen-separated components are treated as separate tokens.
- `call_llm_chat` is the sole function used by the tool-calling pipeline; it sends the full message list and tool definitions in one request via the OpenAI-compatible `/v1/chat/completions` endpoint.
- Supports local Ollama, LAN-hosted Ollama, Ollama Cloud, and LM Studio (local or remote); the host URL and backend flag are the only differences.
- `get_active_model()` and `get_active_num_ctx()` are module globals; skills call these instead of accepting model as a parameter.
- Ollama-specific operations (`/api/tags`, `/api/generate`, `/api/ps`, `ollama serve`) are isolated in `llm_client_ollama.py` and not available in LM Studio mode.

---

## Server - Orchestration pipeline

**File:** `code/KoreAgent/orchestration.py`

**Intent:** Stateless tool-calling pipeline. Accepts a prompt, config, and optional history; runs a tool loop until the model produces a plain-text final answer; returns the answer and token metrics.

**Claims:**
- `orchestrate_prompt` is the single entry point; all execution modes call it.
- The tool loop runs for at most `config.max_iterations` rounds; a final synthesis is forced if rounds are exhausted without a plain-text answer.
- Each LLM call receives the full message thread including all prior tool results.
- `resolve_tokens(user_prompt)` is applied once at the start of each run, resolving `{today}`, `{scratch:key}`, etc.
- `ConversationHistory` is a rolling window capped at `max_turns` (default 10) complete turns; older turns are dropped silently.
- `ConversationHistory.add()` asserts that the turn count is even before appending (parity check).
- `OrchestratorConfig` is a dataclass; it is mutable by slash commands at runtime.
- A `delegate_depth` counter prevents runaway recursion; maximum delegate depth is 2.
- Thread-local state (`_delegate_tls`) passes the active logger and config to `delegate_subrun` without function parameters.
- The system prompt is rebuilt on every orchestration run; it is not cached across turns.
- `_strip_cot_preamble` removes planning prose from the final response only when a structured content marker exists lower in the text.

---

## Server - Context control

**Files:** `code/KoreAgent/scratchpad.py`, `code/KoreAgent/system_skills/Delegate/`, `code/KoreAgent/prompt_tokens.py`

**Intent:** Three complementary mechanisms for keeping large data out of the LLM context window and for isolating sub-task reasoning.

### Scratchpad store

**Claims:**
- `_STORE` is a module-level `dict[str, str]`; it persists for the process lifetime only.
- Keys are validated: lowercased, alphanumeric and underscore only, non-empty.
- `scratch_save` overwrites silently on duplicate key.
- `scratch_query(key, query, save_result_key, instructions)` runs the query in an isolated single-turn LLM call (no tools, clean message thread). The raw stored content never enters the caller's context window.
- When `instructions` is non-empty, it replaces the default "precise extractor" system prompt entirely, enabling synthesis and generation tasks.
- When `save_result_key` is provided, the extracted answer is also saved to that key.
- `scratch_peek(key, substring, context_chars)` returns a windowed view with `>>>match<<<` highlighting; it does not load the full value into the response.
- Large tool results (>= 600 chars) are automatically saved to `_tc_r{round}_{funcname}` keys by the orchestration loop; the message thread receives a truncated version with a key reference.
- Auto-saved keys are prefixed `_tc_` to distinguish them from user-named keys.
- `scratch_clear()` is called at session reset; it removes all keys including auto-saved ones.

### Token substitution

**Claims:**
- `resolve_tokens(text)` resolves `{today}`, `{yesterday}`, `{longdate}`, `{month}`, `{year}`, `{week}`, and `{scratch:key}` in a single pass.
- Resolution is non-recursive: the substituted value is never re-scanned, preventing prompt injection via stored content.
- Applied to the user prompt once per orchestration run, and to string skill arguments by `skill_executor` before each skill call.

### Delegate

**Claims:**
- `delegate(prompt, instructions, max_iterations, output_key, scratchpad_visible_keys, tools_allowlist)` spawns a full isolated child orchestration run.
- The child has no parent conversation history and no parent session context.
- The child's scratchpad key listing is controlled by `scratchpad_visible_keys`; when provided, only those keys appear in the child's system prompt.
- The child's available tools are controlled by `tools_allowlist`; when provided, only listed skills are available.
- The Delegate skill is removed from the child's toolset by default (prevents runaway recursion).
- When `output_key` is provided and the child run succeeds, the final answer is also saved to that scratchpad key.
- Child returns a dict: `{status, answer, delegate_prompt, depth, max_iterations}`.
- `delegate_subrun` reads active logger and config from thread-local state; it cannot be called outside an orchestration run.

---

## Server - Skill system

**Files:** `code/KoreAgent/skills/`, `code/KoreAgent/skill_executor.py`, `code/KoreAgent/skills_catalog_builder.py`

**Intent:** Each skill is a self-contained directory with a `skill.md` definition and a Python module. The catalog builder parses all `skill.md` files into a JSON schema used by orchestration. The executor loads and calls skill functions dynamically.

### Skill catalog

**Claims:**
- `skills_catalog_builder.py` scans `code/KoreAgent/skills/` recursively for `skill.md` files.
- The catalog is written to `skills_summary.md` in the skills root.
- Catalog building can be LLM-assisted or local (deterministic regex fallback via `--no-llm`).
- The orchestration layer hot-reloads the catalog automatically when `skills_summary.md` is modified on disk.
- The catalog drives the JSON Schema tool definitions sent to the model on every LLM call.

### Skill executor

**Claims:**
- `execute_tool_call` validates each call against an allow-list derived from the catalog before loading any module.
- Skill modules are loaded dynamically with `importlib`; loaded callables are cached to avoid re-executing module-level code.
- `resolve_tokens` is applied to all string arguments before the skill function is called.
- `scratch_*` skill calls are exempt from auto-scratch (the result is not re-saved to a new key).

### Available skills (current)

System skills (bundled in `system_skills/`, woven into the framework):
- `CodeExecute` - Python sandbox execution
- `Delegate` - isolated child orchestration
- `FileAccess` - read/write workspace files
- `Memory` - persistent cross-session key-value memory
- `Scratchpad` - session-scoped named-value store
- `TaskManagement` - scheduled task queue control

External skills (bolt-on, in `skills/`):
- `DateTime` - current date/time
- `SystemInfo` - host hardware and OS info
- `WebFetch` - single-page fetch and extraction
- `WebNavigate` - multi-step browser-like page traversal
- `WebResearch` - multi-source research traversal
- `WebSearch` - web search results
- `WebWikipedia` - live Wikipedia article lookup

MCP connections (configured external tool providers):
- `KoreData` - reference search and retrieval across feeds, encyclopedia, library, and internal documents.
- `KoreDocs` - documentation storage, creation, and editing.

**Claims:**
- Each skill directory contains exactly one `skill.md` that defines its public interface for the catalog builder.
- Skills do not import from each other directly; inter-skill data flows through the scratchpad.
- Skill functions that take large values as arguments support `{scratch:key}` token substitution as an alternative to passing inline text.
- MCP connections are declared in `default.json` under `mcp_connections`; the older `mcp_servers` key remains a backwards-compatible alias.
- MCP connections can use `transport: "streamable_http"` (default) or `transport: "sse"` for classic MCP SSE endpoints.
- MCP tool names are owned by the MCP server. The framework exposes the names returned by `list_tools()` unchanged, optionally validating them with `expected_prefix`.
- Per-connection `allowed_tools` and `blocked_tools` lists can restrict which server-published tools are exposed to the model.
- Duplicate MCP tool names are ignored from later connections during enumeration so one server cannot silently replace another server's tool route.
- MCP calls are routed by the framework's tool index to the configured connection that supplied the tool definition.

---

## Server - Scheduler

**File:** `code/KoreAgent/scheduler/scheduler.py`

**Intent:** Sequential task queue that serialises all LLM calls. Also loads and evaluates schedule definitions from JSON files.

**Claims:**
- `task_queue` is a module-level singleton; all execution modes share the same queue.
- Tasks execute one at a time; the worker thread holds `run_lock` for the duration of each task.
- Enqueueing a name that is already queued or active is a no-op (returns False); deduplication prevents backlog from repeated schedule triggers.
- Two schedule types are supported: `interval` (every N minutes) and `daily` (once at a fixed wall-clock time).
- Schedule files live in `datacontrol/schedules/*.json`; each must have a top-level `"tasks"` list.
- Queue state is written to `datacontrol/task_queue.json` on every enqueue and dequeue so the web UI can poll it.
- The scheduler loop lives in `api_mode.py`, not in `scheduler.py`; `scheduler.py` provides utilities only.

---

## Server - API layer

**File:** `code/input_layer/api.py`

**Intent:** FastAPI application that exposes the engine as a REST + SSE service. Owns session identity, conversation persistence, and run-event streaming.

**Claims:**
- CORS is restricted to localhost origins; requests from external sites are blocked.
- Session IDs are validated against `^[A-Za-z0-9_-]+$` before use.
- Each prompt is enqueued on `task_queue`; the endpoint returns `run_id` immediately without blocking.
- `/stoprun` is handled immediately outside the queue so it can cancel the currently running and pending tasks.
- Slash commands are routed through `handle_slash` inside the queue worker, not in the endpoint handler.
- History and session context are loaded at task execution time, not at enqueue time, to prevent race conditions between rapid back-to-back requests.
- Run events are streamed per-run via SSE (`/runs/{id}/stream`); the stream is closed with a `None` sentinel when the run completes.
- Log lines are streamed globally via SSE (`/logs/stream`); a separate endpoint (`/logs/file`) tails a specific file.
- `_MAX_CHAT_HISTORY = 10` turns are retained in memory per session.

---

## Server - Exit behavior

**Files:** `code/main.py`, `code/input_layer/api_mode.py`

**Intent:** The API server must start and stop predictably across Windows and Linux, release its listen port on shutdown, and avoid leaving behind orphaned listener processes after an interrupt or termination request.

**Claims:**
- Startup is cross-platform; no shutdown or lifecycle requirement depends on a Windows-only mechanism.
- The API mode shutdown path is signal-driven rather than tied to a specific key description.
- User-facing startup text must describe shutdown generically as an interrupt/termination action, not rely on a single mandated keystroke.
- On shutdown request, the server stop path sets shared shutdown state before waiting for worker threads to exit.
- On shutdown request, the uvicorn server is asked to exit cleanly via its normal stop flag before any stronger fallback is considered.
- The main thread waits for the server thread to finish for a bounded period; shutdown must not hang indefinitely.
- If graceful shutdown times out, the process must still unwind through a normal Python exit path rather than using an unconditional hard process kill.
- A successful clean shutdown releases the bound API port so a fresh process can bind the same port immediately afterward.
- If startup cannot bind the configured port because another listener already owns it, startup fails fast with a clear message rather than emitting a raw socket traceback.

---

## Server - Slash commands

**File:** `code/input_layer/slash_commands.py`

**Intent:** Shared command processor for all input modes. Commands are dispatched from a registry dict; adding a new command requires only a handler function and a registry entry.

**Commands (current):**

| Command | Effect |
|---|---|
| `/help` | list available commands |
| `/models` | list installed Ollama models |
| `/model <name>` | switch active model (partial name resolved) |
| `/ctx <N>` | set context window size |
| `/rounds <N>` | set max tool-calling iterations |
| `/timeout <N>` | set LLM request timeout in seconds |
| `/stopmodel` | unload the active model from Ollama |
| `/stoprun` | cancel the current and pending queued runs |
| `/llmserver <url>` | switch LLM server host and backend |
| `/newchat` | clear conversation history and session context |
| `/clearmemory` | delete the persistent memory store |
| `/reskill` | rebuild the skills catalog |
| `/sandbox <on/off>` | toggle Python execution sandbox |
| `/deletelogs` | delete log files |
| `/test <file>` | run a test prompt sequence |
| `/testtrend` | show pass/fail trend for recent test runs |
| `/tasks` | list scheduled tasks and their next-fire times |
| `/task <name>` | trigger a scheduled task immediately |
| `/version` | display version string |
| `/session name <alias>` | rename the current webchat conversation in KoreConversation |
| `/session list` | list resumable webchat conversations from KoreConversation |
| `/session resume <name>` | switch to a KoreConversation-backed webchat session and replay its history in the UI |
| `/session resumecopy <old> <new>` | clone a KoreConversation, including message history and metadata, and resume the copy |
| `/session park` | leave the current conversation in place and start a fresh unnamed webchat session ID |
| `/session delete <name\|all>` | delete one or all webchat conversations from KoreConversation |
| `/session info` | show session ID, conversation ID, status, turn count, and token estimate |

**Claims:**
- Only input entered through the main web chat panel is eligible for slash-command interception.
- In that web chat path, any input whose first non-whitespace character is `/` is routed to the slash processor.
- Regular web chat messages are appended to KoreConversation and answered through the KoreConversation event flow.
- Slash-like text arriving through other KoreConversation conversations is treated as plain message content, not as a control command.
- `handle()` returns `True` if the input was consumed as a slash command, `False` otherwise.
- `SlashCommandContext` carries `config`, `output`, `clear_history`, `session_id`, `switch_session`, and `rename_session`; commands do not access global state directly.
- `switch_session(new_id, name)` fires an SSE `switch_session` event causing the browser to update its session ID and replay history.
- `rename_session(new_id, name)` fires an SSE `rename_session` event updating the browser ID and panel title in-place without a history replay.
- `/newchat` clears browser-visible history and session context for the active web session; the next prompt continues against the same session ID unless `/session park` or `/session resume` changes it.
- `/session delete` uses exact name matching (case-insensitive) to prevent accidental multi-session deletion.
- Deleting the currently active session automatically parks to a new unnamed session via `switch_session`.

---

## Server - Session persistence

**File:** `code/input_layer/api.py` (`_load_session`, `_save_session`, `_compact_old_turns`, `_build_summary_block`)

**Intent:** Reconstruct browser-session continuity from KoreConversation state while keeping runtime scratchpad and orchestration state transient between requests.

**Claims:**
- Browser session IDs map to KoreConversation `external_id` values using the `webchat_<session_id>` convention.
- `_load_session()` rebuilds `ConversationHistory` from KoreConversation messages on every request, pairing inbound and outbound messages in chronological order.
- `_load_session()` restores the persisted KoreConversation scratchpad into the active runtime scratch store before orchestration.
- `thread_summary` is exposed back into the runtime as a synthetic summary block so long conversations can still be compressed without keeping raw history in local files.
- `_save_session()` is now a compatibility shim. It no longer writes chat JSON files; it only flushes scratchpad state back into KoreConversation.
- `_flush_scratch_to_session()` patches the linked KoreConversation scratchpad with the current runtime scratch store.
- `DELETE /sessions/{id}` deletes the linked KoreConversation and clears in-memory scratch for that web session.
- Test-prompt runs (`/test`) still use isolated `ConversationHistory` objects and never write browser session state.

### Named sessions

**Claims:**
- `/session name <alias>` patches the current conversation subject in KoreConversation; it does not change the browser session ID.
- `/session resume <name>` resolves a webchat conversation by display name, derives its session ID from `external_id`, and emits a `switch_session` event so the browser reloads history from the API.
- `/session resumecopy <old> <new>` creates a new webchat conversation, replays the source message history into it, copies summary/scratchpad counters, and switches the browser into the new session.
- `/session park` simply switches the browser to a fresh `web_<timestamp>` session ID. The backing conversation is created lazily when the next prompt is sent.
- `/session delete all` removes all webchat KoreConversation records, not a local `named/` directory.
- `GET /completions` returns `{ sessions, test_files, task_names, models }` so the browser tab-complete feature has live named-session names without a separate polling loop.

---

## Server - Testing

**Files:** `code/KoreAgent/testing/`, `datacontrol/test_prompts/`

**Intent:** Automated regression testing via prompt sequences. Each test file contains a list of prompts and expected response patterns. Results are written to dated directories and compared against prior runs.

**Claims:**
- Test prompt files are JSON, stored in `datacontrol/test_prompts/`.
- Each run creates a new results file in `datacontrol/test_results/<YYYY-MM-DD/>`.
- Each test turn uses its own isolated `ConversationHistory`; test runs do not share context with the live chat session.
- `/testtrend` reads result files and reports pass/fail changes across runs.
- The test runner serialises through `task_queue.llm_lock` so test runs and live prompts do not overlap.

---

## UI - Layout and panels

**Files:** `code/input_layer/ui/index.html`, `code/input_layer/ui/app.js`, `code/input_layer/ui/style.css`

**Intent:** Single-page web UI served as static files by the FastAPI app. Three resizable panels: Schedule timeline (left), Log stream (centre), Chat (right), with a shared input bar at the bottom.

**Claims:**
- Panel widths are controlled by two vertical splitters (`splitter-v1`, `splitter-v2`) and one horizontal splitter (`splitter-h1`).
- Panel size ratios are persisted to `localStorage` and restored on reload.
- A "default layout" button in the header resets all ratios to the built-in defaults.
- Splitter drag is pointer-captured so the mouse can move outside the splitter element without losing the drag.
- Layout fractions are re-applied on window resize to preserve ratios.

---

## UI - Log panel

**Files:** `code/input_layer/ui/app.js` (MARK: LOG STREAM)

**Intent:** Live-tail display of the active log file. Colour-coded by line type. Scrolls smoothly to the bottom while live mode is engaged; pauses on user scroll.

**Claims:**
- Log lines are received via SSE (`/logs/stream`); the stream reconnects automatically after a 3-second delay on error.
- Lines are colour-classified by content: separator, tool-round, title, progress, thinking, scheduler, error, success.
- Maximum `MAX_LOG_LINES` lines are retained; oldest are removed when the cap is exceeded.
- **Live mode** (`_logLive = true`): each new line triggers `_scrollLogSmooth()`, a rAF decay-loop that eases to the bottom at 30% per frame.
- Upward wheel scroll exits live mode; re-entry is only via the [live] button (no auto-reselect).
- Scrollbar grab (pointer down right of `clientWidth`) also exits live mode.
- The [live] button shows the `wrap-active` CSS class when live mode is engaged.
- Clicking [live] when off snaps instantly to the bottom and resumes the latest log file.
- Panel resize snaps instantly to the bottom (does not use the smooth loop) so that `_isLogNearBottom()` stays accurate and live mode is not inadvertently exited.
- Log navigation buttons ([up] / [down]) step through section separators in the log body.
- The [wrap] button toggles `nowrap` CSS on the log body; the view is re-anchored after reflow so the same content stays at the same screen position.

---

## UI - Chat panel

**Files:** `code/input_layer/ui/app.js` (MARK: CHAT, MARK: RUN STREAM)

**Intent:** Conversation display. Each prompt and response is a message bubble. Streams response events in real time via a per-run SSE connection.

**Claims:**
- Each submitted prompt opens a `RunStream` SSE connection to `/runs/{id}/stream`.
- The `RunStream` receives typed events: `start`, `response`, `log_file`, `progress`, `test_agent_response`, `test_agent_metrics`, `test_complete`, `error`, `rename_session`, `switch_session`, `done`.
- A `response` event populates the agent message bubble; prior bubbles are not modified.
- A `rename_session` event updates `_sessionId` and the panel title in-place; no history is replayed.
- A `switch_session` event updates `_sessionId`, sets the panel title, draws a divider line, and calls `_loadSessionHistory()` to replay the new session's turns.
- **Chat live mode** (`_chatLive`): new messages call `_scrollChatSmooth()`, the same rAF decay-loop used by the log panel.
- Upward scroll in the chat panel disengages auto-scroll; reaching the bottom re-engages it automatically (unlike the log panel, which requires the [live] button).
- Token count and tokens-per-second are displayed as metadata below each agent response.
- The [sandbox] button in the panel header calls `POST /sandbox` to toggle the Python execution sandbox; its CSS class reflects current state.
- The [wrap] button toggles `nowrap` on the chat body with the same re-anchor logic as the log panel.
- On startup, the chat panel loads prior messages from `GET /sessions/{id}/history` and renders them.

---

## UI - Schedule timeline

**Files:** `code/input_layer/ui/app.js` (MARK: TIMELINE, MARK: QUEUE STATUS)

**Intent:** Visual display of scheduled tasks as a horizontal time axis plus a queue status area showing pending and active tasks.

**Claims:**
- The timeline polled via `GET /tasks` every 5 seconds.
- The queue is polled via `GET /queue` every 2 seconds.
- Each scheduled task is rendered as a marker on the timeline relative to its next-fire time.
- The current-time indicator advances in real time using `requestAnimationFrame` or `setInterval`.
- The timeline recentres when the queue subpanel changes height (ResizeObserver).
- Pending tasks are shown with a label and kind; the active task is highlighted.

---

## UI - Prompt input

**Files:** `code/input_layer/ui/app.js` (MARK: SUBMIT PROMPT, MARK: KEYBOARD HANDLER)

**Intent:** Multi-line textarea for prompt entry. Enter submits; Shift+Enter inserts a newline. Prompt is also appended to the input history list.

**Claims:**
- Enter (without Shift) submits the current prompt.
- Shift+Enter inserts a newline without submitting.
- Empty or whitespace-only prompts are rejected without submission.
- On submit, the prompt text is appended to the server-side input history via `POST /history`.
- The input history (up-arrow recall) loads from `GET /history` on startup.
- `/stoprun` submitted via the input box is handled immediately server-side without joining the task queue.
- The session ID is a module-level `let` variable initialised to `web_{Date.now()}` on page load; it is updated in-place by `rename_session` and `switch_session` SSE events.
- Tab-completing a `/` prefix opens a dropdown driven by `GET /completions`; Tab cycles candidates, ArrowUp/Down navigates, Escape closes, Enter selects. Completing a command or sub-command chains immediately to the next argument slot.
