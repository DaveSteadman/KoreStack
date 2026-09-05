# KoreStack Change Log

This file records dated implementation changes, review findings, validation results, and follow-up decisions. Root and subsystem READMEs describe current functionality, configuration, and operation.

For future entries, place the newest date first. State the problem, the change made, the verification performed, and any unresolved limitations. Distinguish implemented changes from proposed work. Update follow-up decisions when later reviews resolve or supersede them. Keep entries concise and avoid separate temporary review documents.

The project favours simple, clear code and small, cohesive changes. Prefer removing duplication and unnecessary mechanisms. Verify changes using existing checks or transient checks where appropriate, without retaining working or temporary test files.

## UI consistency and tool-runtime cleanup: 5 September 2026

### Implemented changes

- Consolidated dialog buttons into the shared `forms.css` theme. Removed the competing generic button definitions from `dialogs.css`; shared dialogs and KoreDocs file dialogs now use the same base classes and central variants.
- Corrected default-button selector precedence so primary, danger, warning, and quiet variants can apply consistently. Added central toolbar sizing, surface, success, and filled variants, along with shared keyboard-focus and disabled states.
- Migrated KoreAgent's Send button, KoreCode's find/send/continue actions, and KoreDocs text-editor actions onto those shared classes. Kept page layout rules, including the agent's stretched Send button and the code toolbar's 32px height. Removed their redundant button skins and unreferenced KoreCode button CSS.
- Removed copied formatting and recovery implementations from the tool loop. The existing `formatting.py` and `recovery.py` modules now own those behaviours; existing import paths remain available. Removed obsolete auto-activation branches and verbose generated function inventories from these modules.
- Canonicalised tool-call fingerprints across dictionary arguments and JSON strings, so whitespace or object-key order cannot evade duplicate detection.
- Fixed unresolved tool recovery falling through into an empty tool round. Recovery now stops unsuccessfully after its bounded reminder. Repeating an invalid call no longer clears pending recovery, and final synthesis cannot turn unresolved recovery into success.
- Removed the substitution of model reasoning for missing answer content. Reasoning remains available for diagnostics, but no longer counts as a completed answer.

### Verification

Twenty-eight existing focused agent checks passed: publication (4), Ollama process behaviour (7), sampling (4), conversation input (12), and skill loading (1). Transient in-memory checks covered shared helper identity, equivalent/different argument fingerprints, bounded failed recovery, duplicate invalid calls, synthesis after unresolved recovery, and reasoning-only responses. All agent Python files compiled successfully.

Browser verification used an in-memory preview serving the actual shared CSS. Colour variants, legacy class compatibility, shared typography, toolbar/icon dimensions, keyboard focus, and disabled states were checked. The configured KoreAgent service was not running, so this does not certify every live page layout or live model behaviour. The preview process was stopped after verification; no preview or test files were retained. Diff whitespace checks passed.

### Remaining review findings

- Some domain-specific button styling remains, particularly spreadsheet controls and graph showcase controls. Future passes should move reusable visual states into UIElements while leaving editor/canvas layout local.
- Shared interactive tags and icon controls are already centrally defined. Reuse those definitions when reviewing remaining page-specific controls.
- The earlier event-ownership, delivery-reconciliation, mutable run configuration, and obsolete existing-test findings remain open. Consolidating the tool loop addresses duplication and specific failures; it does not make model task completion deterministic.

## Reliability review: 5 September 2026

The review found concrete runtime defects that can turn modest model-output or configuration differences into failed runs. These are code-level findings, not a measured attribution of historical day-to-day model quality. The implementation changes below were verified offline; live model quality and external delivery were not exercised.

### Implemented corrections

| Boundary | Finding and correction |
|---|---|
| LLM startup and health | The routing facade inferred LM Studio from port 1234 even after explicit backend selection. Health, model listing, and reports now honour the active backend. The dependency monitor now checks LM Studio model availability and respects the Ollama autostart opt-in. See [llm_client.py](KoreAgent/app/llm_client.py) and [startup.py](KoreAgent/app/api/startup.py). |
| Streaming | Ollama streams previously accepted EOF without a completion marker and ignored in-band errors. Incomplete streams now fail before their tool calls can be dispatched. Partial text already emitted to the UI remains provisional. See [llm_client_ollama.py](KoreAgent/app/llm_client_ollama.py). |
| Tool arguments | Dictionary arguments crashed duplicate detection; null, array, and scalar arguments escaped the parser's exception handling. Ollama conversion also silently replaced non-dictionary arguments with an empty object. The adapter preserves arguments, and the loop validates object shape and returns invalid-argument errors to the model without execution. See [loop.py](KoreAgent/app/agent/tool_runtime/loop.py). |
| Tool schema refresh | Cache keys tracked object identity and tool names, so changed descriptions or parameters could leave the agent using stale schemas until cache eviction or restart. Keys now include catalog contents and, for active runtime state, the web-tools flag. See [tool_catalog.py](KoreAgent/app/sessions/tool_catalog.py). |
| Service error propagation | Structured failures were marked successful because only text prefixes counted as errors. Explicit `status: error`, `isError`, `is_error`, and `success: false` now produce failed tool results. Partial results remain usable. See [skill_executor.py](KoreAgent/app/skill_executor.py). |
| Scheduled email completion | Exhausting tool rounds entered a synthesis path that bypassed required publication. The final result now remains unsuccessful whenever explicit delivery was required but unconfirmed. This does not establish exactly-once email delivery. |

### Remaining priorities

1. **P1: Make the regression baseline trustworthy.** Existing agent discovery has six failures and five errors, reproduced against the original HEAD implementation. Several tests reference removed `datasets_pkg` or `sessions.tool_sets` interfaces, obsolete web-search internals, and older scratchpad/dataset prompt contracts. Migrate these tests to current behaviour while retaining their behavioural assertions; do not simply suppress them. Some context/model-resolution tests use pytest functions and are not collected by the unittest runner. Neither inspected Python environment includes pytest, and requirements do not declare it.
2. **P1: Give long-running KoreChat work renewable ownership.** [events.py](KoreChat/app/db/events.py) claims work atomically, which is good. However, claims expire after 600 seconds and completion updates only by event ID. A slow worker can outlive its lease, another worker can reclaim the event, and the old worker can still complete it. Add claim tokens, renewal, and ownership-checked completion together, with a two-worker regression. Increasing the timeout alone does not solve ownership.
3. **P1: Reconcile external sends before retrying.** In [poller.py](KoreComms/app/poller.py), `route_reply` can succeed before publication recording or `mark_message_sent` fails. The next polling pass sees the draft and can send it again. Introduce durable per-recipient delivery attempts and receipt reconciliation, including an explicit uncertain state. Test send success followed by acknowledgement failure and partial multi-recipient delivery. SMTP delivery cannot be made exactly-once merely by retrying.
4. **P1: Define one run outcome and settings snapshot.** [ChatCallResult.response](KoreAgent/app/llm_client_openai.py) can substitute reasoning for a missing answer, and the loop often equates nonempty text with success. Separate completed, failed, cancelled, incomplete, and needs-recovery outcomes; distinguish tool success from objective completion. Freeze backend, model, context, sampling, and catalog revision for a run rather than consulting separately mutable global values between rounds.
5. **P2: Make model comparisons reproducible.** Record model identity/digest, backend version, effective context, sampling options, prompt revision, and schema fingerprint with each run. The checked-in config requests a 100,000-token context but Windows native Ollama requests omit per-request context unless opted in; a configured budget therefore does not establish the loaded model's actual context. Temperature and seed overrides are also disabled in checked-in configuration. Measure repeated fixed tasks before changing these settings; do not enable larger Windows contexts blindly because the adapter documents runner instability.
6. **P2: Preserve KoreData's explicit partial-failure contract.** [gateway_search.py](KoreData/KoreDataGateway/app/gateway_search.py) already distinguishes per-domain errors, partial results, and empty results. Extend fault-injection coverage through the agent's working-data storage and final response so failed sources are not mistaken for evidence of no matches. Keep stable artifact references and retrieval provenance as shared contracts.
7. **P2: Reduce change sensitivity across services.** Consolidate duplicated recovery/formatting logic between `loop.py` and its neighbouring helper modules. Establish a tested dependency constraints file instead of relying solely on open-ended minimum versions. Run isolated service contract tests on each change before live-model evaluations.

### Verification performed

At the time of the review, 16 focused runtime checks passed. Existing focused suites also passed: KoreChat 6, KoreComms email threads 4, and KoreData gateway 14. The broad agent suite retained six failures and five errors, reproduced against the original implementation. These results do not certify all services or live model performance.

### Review follow-up

At the project owner's request, the review was moved out of README.md into this file. The newly added runtime test file and offline regression runner were removed to avoid retaining working test artifacts. The runtime fixes remain. The verification results above are historical results, not a claim that the removed checks remain available.

The remaining priorities above are review proposals. Future implementation should favour simplicity and clarity, reuse existing verification where useful, and avoid adding permanent test scaffolding by default.
