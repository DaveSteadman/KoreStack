# Future Changes

Confirmed improvements and longer-term design work. Last reviewed: 2026-04-20.

Done items are removed - see ChangeLog.md for what shipped.

---

## Top 10 Most Serious Issues (2026-04-20)

### 1. Scratchpad read/iterate operations are not lock-protected

`scratch_load`, `scratch_list`, `scratch_dump`, `scratch_search`, and `scratch_clear` all call
`_get_session_store()` then immediately access the returned dict without holding `_STORE_LOCK`.
A concurrent write (`scratch_save`, `scratch_delete`) can cause `RuntimeError: dictionary changed
size during iteration` in list/search/dump, or a stale/torn read in load.
Fix: Wrap the read and iteration bodies of each function with `with _STORE_LOCK:`.

---

### 2. `scratch_unpin_all()` is not called in a `try/finally` block

In `tool_loop.py` the call sits just before the `return` statement outside any `finally` block.
If an unhandled exception propagates out of the synthesis `except` block, or any future code is
added between the loop and the return, pinned keys are never released. Over time (especially in
the scheduler) this prevents auto-key eviction, allowing unbounded store growth.
Fix: Move the call into a `try/finally` that wraps the entire `for round_num in range(...)` loop.

---

### 3. `SessionContext.add_turn()` is not thread-safe

`add_turn()` reads `len(self._turns)`, appends to it, and then calls `_save()` which iterates it
- all without holding any lock. In API mode, two concurrent requests to the same session can
interleave these operations, producing duplicate turn numbers, missing turns, or a corrupted JSON
file on disk.
Fix: Add a `threading.Lock` to `SessionContext.__init__` and take it in both `add_turn()` and
`_save()`.

---

### 4. Silent scratchpad restore failure in `koreconv_input.py`

The loop that restores persisted scratchpad keys into the active session before orchestration
swallows all exceptions silently (`except Exception: pass`). If any key fails validation or
`scratch_save` raises for any reason, that key is silently dropped - the run proceeds without the
expected scratchpad state and the loss is invisible in logs.
File: `input_layer/koreconv_input.py`, inside `_handle_event`.
Fix: Replace with `push_log_line(f"[KORECONV] Could not restore scratchpad key {scratch_key!r}: {exc}")`.

---

### 5. Temp file left on disk when `os.replace()` fails

`SessionContext._save()` writes to a `.tmp` file then calls `os.replace()`. If `os.replace()`
raises (e.g. cross-device link on some filesystems, permissions error), the except block logs
the error but does not delete the `.tmp` file. Repeated failures accumulate `.session_context.tmp`
files in the data directory.
Fix: Add `tmp_path.unlink(missing_ok=True)` in the except handler.

---

### 6. `_request_json()` threading approach is redundant and leaks closures

`llm_client_openai.py`'s `_request_json()` spawns a daemon thread solely to enforce a timeout on
`urlopen`, but `urllib.request.urlopen(request, timeout=timeout)` already enforces a socket-level
timeout without a thread. When the thread times out the function raises `TimeoutError` and returns,
but the thread continues executing, holding `_result`/`_error` list references until it unblocks
- accumulating closure references under sustained timeout conditions.
Fix: Remove the thread wrapper entirely and rely on `urlopen`'s built-in timeout parameter.

---

### 7. `ensure_ollama_running()` has a TOCTOU race that spawns duplicate processes

`is_ollama_running()` is checked and then `start_ollama_server()` is called without any
serialization. Two threads (e.g. scheduler thread + API handler) can both observe `False` and
both call `Popen(["ollama", "serve"])`. The second process fails to bind port 11434, causing
subsequent LLM calls from that thread to fail. The spawned `Popen` object is also never stored,
so neither process can be shut down cleanly on exit.
Fix: Add a module-level `threading.Lock` around the check-and-spawn block in
`ensure_ollama_running()`, and store the returned `Popen` so it can be `.terminate()`'d on exit.

---

### 8. `_build_prompt()` silently drops scratchpad context on JSON decode failure

In `koreconv_input.py`, `_build_prompt()` tries to `json.loads()` the scratchpad string and falls
back to `scratchpad = {}` on failure. If the KC-side scratchpad field is corrupted or partially
written, the prompt is built without any scratchpad context and the model receives no indication
that context was expected. The failure is never logged.
Fix: Log a warning via `push_log_line` before falling back to `{}`.

---

### 9. `assess_compact()` does not validate index alignment between `messages` and `context_map`

`assess_compact()` pops entries from `messages` using `msg_idx` values stored in `context_map`.
If any caller adds a message without a corresponding `context_map` entry (or vice versa), the
offsets become stale and subsequent pops remove the wrong messages silently, corrupting the
conversation thread without any error.
Fix: Add a `RuntimeError` check at the entry to `assess_compact()` verifying that
`len(messages) - 1` equals `max(e["msg_idx"] for e in context_map)`, so misalignment is caught
immediately.

---

### 10. `delegate_runner.py` does not check the stop state before launching a sub-run

`run_delegate()` calls `orchestrate_prompt()` which creates its own per-run stop event. However,
the outer run's stop event is not checked before the delegate call, so if `/stoprun` fires while
a delegate is queued, the outer loop stops but the delegate run starts anyway - using stop state
that has already been cleared by the inner `run_tool_loop`.
Fix: Check `stop_requested()` at the top of `run_delegate()` before calling `orchestrate_prompt()`
and short-circuit with a stopped message if set.

---

## Top 10 Actionable Items

### 1. Non-atomic SessionContext write can corrupt the persisted file

**File:** `code/KoreAgent/orchestration.py` (`SessionContext._save`, ~line 345)

`Path.write_text()` is not atomic. If the process crashes or is killed while the file is
being written (disk full, power loss), the JSON file is left partially written. On the
next load, `json.loads` will raise, and the load path catches that with a bare
`except Exception: pass` (see item 2), silently returning an empty session.

Fix: write to a temp file in the same directory, then call `os.replace()` to rename
atomically. One temp file per session ID to avoid collisions. Python stdlib `os.replace()`
is atomic on POSIX and near-atomic on Windows (replaces in a single kernel call).

---

### 2. SessionContext load silently discards corrupt files with no log

**File:** `code/KoreAgent/orchestration.py` (`SessionContext.__init__`, ~line 228)

When `json.loads` raises on a corrupt or partially-written session file, the bare
`except Exception: pass` block leaves `self._turns = []`. The user resumes the session
with an empty context and no indication that anything was lost. Combined with the
non-atomic write in item 1, this pair makes session state silently fragile.

Fix: replace `pass` with at minimum `log_to_session(f"[session_context] WARNING: ...")`.
Optionally rename the corrupt file to `*.corrupt` before starting fresh so it can be
inspected. This also applies to the structural filter that silently drops turns with
missing keys - log the count of dropped turns.

---

### 3. on_tool_round_complete callback failures are silently swallowed

**File:** `code/KoreAgent/tool_loop.py` (~line 347)

After each tool round, `on_tool_round_complete()` is called inside a bare
`except Exception: pass`. The callback is used for session state flushing and UI updates.
If it raises - for example because the session log path is suddenly read-only - the error
vanishes and subsequent rounds proceed without knowing that state persistence has stopped.

Fix: replace `pass` with `logger.log_file_only(f"[error] on_tool_round_complete: {exc}")`.
No need to propagate - the tool loop should continue - but the failure must be visible
in the session log.

---

### 4. _ollama_health_cache dict is accessed from multiple threads without a lock

**File:** `code/KoreAgent/llm_client_openai.py` (lines 57, 140, 145)

`_ollama_health_cache` is a plain `dict[str, float]` written by `mark_host_healthy()`
and read by `is_host_health_cached()`. In API mode, concurrent request threads call both
functions without synchronisation. Python's GIL does not protect against `RuntimeError`
on dict resize during concurrent modification. Two threads can both see a stale cache hit
and proceed to a failing LLM server, then both try to write the same key simultaneously.

Fix: add `_health_cache_lock = threading.Lock()` and acquire it in both functions.
The critical section is tiny (single dict read or write), so contention is negligible.

---

### 5. _stop_event is process-global - /stoprun affects all concurrent runs

**File:** `code/KoreAgent/orchestration.py` (line 121)

`_stop_event` is a module-level `threading.Event`. In API mode, a scheduler task and an
interactive session run concurrently in different threads. A `/stoprun` from the user
sets the global flag and stops both. Worse, `clear_stop()` is called at the start of
every `run_tool_loop()` - so a scheduler task starting up can silently swallow a user's
stop request that was intended for the interactive session.

Fix: make stop signalling per-session. Pass a `stop_event: threading.Event` parameter to
`orchestrate_prompt` and down into `run_tool_loop`. Each API session creates its own
event. The slash command resolves to the current session's event only. Module-level
`request_stop()` remains as a shutdown hook that sets all active events.

---

### 6. MCP event loop thread is not cleaned up on enumeration timeout

**File:** `code/KoreAgent/mcp_client.py` (`start`, ~line 91)

When `future.result(timeout=...)` raises `TimeoutError`, the function logs a warning and
continues with empty tool lists. The background thread (`_loop_thread`) is never stopped.
`_loop` and `_loop_thread` module globals still hold references to the running thread.
In a long development session where `start()` is re-called (e.g., after a `/reload`),
a new orphaned event loop thread accumulates each time an MCP server is unreachable.

Fix: on timeout, call `_loop.call_soon_threadsafe(_loop.stop)`, then join `_loop_thread`
with a short timeout, then set both globals back to `None` before returning.

---

### 7. ConversationHistory.add() uses assert - disabled by -O and masks root cause

**File:** `code/KoreAgent/orchestration.py` (`ConversationHistory.add`, line 161)

`assert len(self._turns) % 2 == 0` checks the turn parity invariant before adding a new
pair. While the logic is correct, `assert` is silently removed when Python runs with
`-O` optimisation. More critically, the assert fires at the `add()` call site where
the misalignment is *detected*, not where it was *introduced* - making diagnosis hard.

Fix: replace `assert` with an explicit `if` check raising `RuntimeError` with context
(current turn count, the user/assistant strings being added). Add the same check in
`as_list()` so any caller reading history also catches misalignment.

---

### 8. auto_scratch_key variable bleeds between tool calls within the same round

**File:** `code/KoreAgent/tool_loop.py` (~line 335)

`auto_scratch_key` is only assigned inside the `if len(result_content) >= TOOL_MSG_AUTO_SCRATCH_MIN:`
block. When the next tool call's result is too short to trigger auto-saving, the variable
still holds the value from the previous iteration. The `context_map.append(...)` on the
following line then records the *previous* tool call's key as the `auto_key` for the
*current* result. This silently links the wrong tool result to a scratchpad key.

Fix: initialise `auto_scratch_key = None` at the top of the per-tool-call loop body,
before the conditional block. One-line fix with zero functional overhead.

---

### 9. koreconv_input.py bare except:pass on event-completion HTTP calls

**File:** `code/KoreAgent/input_layer/koreconv_input.py` (lines 237, 250, 258, 295)

When the handler for a `compress`, `summarise`, or `respond` event finishes, it calls
`_http_post(.../events/{id}/complete)` to signal the KoreConversation server. Four of
these calls are wrapped in bare `except Exception: pass`. If the HTTP call fails (server
restart, network blip), the event stays in `pending` state permanently. Future polls
pick up the same event repeatedly; the agent processes the same conversation turn in an
infinite loop.

Fix: log the failure with the event ID and status at WARNING level. For completion calls,
implement a best-effort retry (1 retry after 2s). If both attempts fail, log at ERROR
and leave the event for manual cleanup - but never silently discard the failure.

---

### 10. Auto-saved scratchpad keys can be evicted while still referenced in the message thread

**File:** `code/KoreAgent/scratchpad.py` (`scratch_save` eviction), `code/KoreAgent/tool_loop.py` (~line 330)

When a tool result is truncated, the message sent to the LLM reads:
`"full content auto-saved to scratchpad key: _tc_r2_web_search"`. That key is now part of
the active message thread. The eviction algorithm (MAX_AUTO_KEYS=40) evicts oldest keys
on subsequent auto-saves, with no check on whether a key is still referenced in the live
message context. After 40 more large tool results, `_tc_r2_web_search` is evicted. When
the LLM later calls `scratch_load("_tc_r2_web_search")`, it gets an error - with no
explanation in the truncated message.

Fix: when evicting a key, consult the active `context_map` entries (passed in or via
module-level state) to check whether any `auto_key` field matches. If so, skip eviction
for that key. Alternatively, raise the eviction threshold significantly or reference-count
active keys so they are protected until the round is complete.

---

## Longer-Term Design Work

### Search result disk cache

Repeated test runs and scheduler jobs re-fetch the same search queries and URLs. Relevant
because DuckDuckGo rate-limits on repeated calls.

Step 1 - measure waste: instrument `web_search_skill.py` to log how many URLs are returned
per query versus how many are actually fetched in the same session. The ratio establishes
a baseline before any caching is built.

Step 2 - cache: file-based store in `datacontrol/search_cache/` keyed by
`sha256(query_normalised + date)`, stored as JSON, configurable TTL (default 24h).
Time-sensitive queries (containing "today", "latest", "current") use a shorter TTL.

---

### Per-skill retry configuration

Network-bound skills (WebSearch, WebFetch, WebResearch, Wikipedia) fail transiently
but the framework never retries automatically. The DuckDuckGo retry was added directly
to `web_search_skill.py` with no general mechanism.

Design: add `retries` and `retry_delay_seconds` to the `skill.md` spec. When
`execute_tool_call` in `skill_executor.py` receives a recognised error sentinel and the
skill declares retries, re-call the function after the delay before surfacing failure to
the LLM. Makes retry policy per-skill rather than baked into each skill file.

Depends on: item 10 (routing metadata schema extension) establishes the precedent for
adding structured metadata fields to skill.md.

---

### MCP connection policy hardening

MCP tools are now consumed from configured external connections and merged into the tool
list alongside local Python skills. The next useful step is finer-grained policy for
write-capable MCP endpoints.

Possible changes:
- Surface write-capable connections more clearly in `/mcp status`.
- Add tests for duplicate MCP tool names, prefix validation, and disabled connections.
- Consider a confirmation or audit path for destructive document-editing tools.

---

### PerfInsight - agent self-assessment skill

The agent cannot reason about its own effectiveness. Test results live under
`datacontrol/test_results/` as structured CSVs with per-prompt pass/fail and timing, but
no skill reads them.

A `PerfInsight` skill that can:
- load and parse test result CSVs
- compute per-suite PASS rates and median/p95 durations
- surface the five slowest prompts and any failure clusters
- answer prompts like "how did the last test run go?" or "which skill is the slowest?"

The CSV schema is already stable. Candidate implementation: extend `FileAccess` with a
`csv_summarise(path)` function rather than a new skill folder.

---

### KC - thread summary bounding

**File:** `code/KoreConversation/...` (conversation summary logic)

Each compression cycle appends the new summary to the existing `thread_summary` with no
max length. After many rounds the summary itself becomes a significant fraction of the
context budget.

Options:
- Cap `thread_summary` at a fixed character limit and discard the oldest portion.
- Run a meta-compression pass when the summary crosses a threshold.
- Store summary segments as separate records rather than a single growing text field.

---

### KC - SSE subscriber cleanup

**File:** `code/KoreConversation/app/api.py`

Dead SSE clients are only removed from `_kc_subscribers` when their queue fills to
capacity. Subscribers with slow proxies or closed browser tabs sit in the list
indefinitely until they accumulate enough missed events to trigger eviction.

Fix options:
- Heartbeat write - detect send failure and remove immediately.
- WeakRef-based subscriber list - garbage-collected clients are removed automatically.
- Reduce queue `maxsize` so stale subscribers are evicted sooner.

---

### KC - polling backoff on idle

The `korecomms_input` polling loop calls `GET /events/next` every 3 seconds regardless
of queue state. Under idle conditions this is unnecessary HTTP traffic.

Fix: exponential backoff on empty-poll responses (3s -> 6s -> 12s, capped at 30s),
resetting to 3s immediately on a successful claim.








# Test test_results_20260420_222941_all.csv


Here is the full analysis - 8 explicit FAILs across 4 test suites, plus several significant quality observations.

Top 10 Actionable Improvements
1.  DONE
2. DONE

3. DONE

4. Web search blind spot for well-known URLs
Three tests fail (search_hackernews_url, multihop_hackernews, search_ai_coding) because the search tool returns zero results for HN and major tech news sites. Yet a direct fetch of https://news.ycombinator.com returns 74 links cleanly. When a query looks like a URL-discovery request ("search for X and tell me the URL"), add a fallback that attempts a direct fetch of the likely canonical URL if search returns nothing.

5. LLM HTTP 500 XML syntax error (min_prompts and research contexts)
Two separate runs hit HTTP 500: XML syntax error on line 5: element <function> closed by </parameter>. This is a malformed tool schema being sent to the API. The min_prompts.json config (and the research web-search invocations) trigger a code path that produces invalid XML. Audit the tool serialization in llm_client.py for the minimal/research config to find which tool definition is generating the broken <parameter> close tag.

6. Research fails with no retry on connection errors
research_local_inference and search_ai_coding immediately surface "no results / connection error" to the user. Compare with research_ollama_vs_llama, which manually tried different URLs and succeeded. The retry/fallback logic is not consistent. Add 2-3 automatic retries with varied query terms before surfacing a failure.

7. Internal chain-of-thought leaks into final response
research_ollama_vs_llama (marked PASS, 115s) has the agent's entire step-by-step internal monologue as the response: "Could use known info from memory store maybe... Let me try https://raw.githubusercontent.com..." - none of which should be user-visible. The scratchpad reasoning is being returned directly as the final answer. Add a final-response filter that strips raw tool-planning text.

8. Code execution latency is highly variable
Simple code tasks run in 2-5 seconds (fibonacci, factorial, sum to 100). But sin_cos.csv takes 78.9s, sales.csv 54.5s, primes.csv 35.9s. All the slow ones involve file writes. The fast ones are pure computation. Hypothesis: the slow path involves generating code, executing it, then the skill performing a separate read-back and validation loop. Profile whether unnecessary round-trips or file-content verification steps are the bottleneck.

9. DONE

10. Multi-turn assertion cascade: prior FAIL contaminates later turns
Turns 7 and 8 of the compression exchange share the same log file and timestamp - all turns in the exchange are recorded together. Turn 8 (/ctx size 128000) appears to have produced a correct response ("Context window size changed: 512 → 128,000") but is marked FAIL, almost certainly because turn 5 and 7 already failed. The assertion framework should evaluate each turn independently against its own criteria rather than propagating a FAIL across the whole exchange - otherwise it is impossible to tell which turns genuinely produced wrong output.
