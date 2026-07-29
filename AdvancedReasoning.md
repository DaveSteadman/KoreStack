# Advanced reasoning: durable objective loops

## Purpose

KoreStack can turn long work into an inspectable production line when task layers exchange
durable artefacts rather than conversational memory. The objective is not an agent told to
"keep going". It is a recoverable loop:

```text
objective -> plan -> one bounded action -> verify evidence -> assess -> next action or finish
```

A new chat, restart, or human reviewer must be able to establish what happened from the plan,
attempt records, and referenced artefacts. A compressed chat summary is helpful navigation, not
proof or the system of record.

## Long-running task control plane

A long-running task is not simply a slow prompt. It is an objective whose progress cannot safely
live only in one chat context. It needs a durable identity, bounded iterations, and explicit
reasons to start, continue, wait, retry, or stop.

```text
task_id -> start criteria -> eligible iteration -> evidence -> assess -> next start or exit
```

Every task should have a control record, stored durably alongside its objective plan:

```json
{
  "task_id": "task_...",
  "plan_ref": "koredoc:...",
  "objective": "Keep a market briefing current and evidence-backed.",
  "state": "active",
  "iteration": 7,
  "start_criteria": {
    "schedule": "weekly on Monday at 08:00 Europe/London",
    "events": ["new source_register matching the task metadata"],
    "manual_start_allowed": true,
    "not_before": "2026-08-03T08:00:00+01:00"
  },
  "exit_criteria": {
    "complete_when": ["All plan success criteria have verified evidence."],
    "pause_when": ["A human decision or missing external input is required."],
    "stop_after": {"max_iterations": 20, "max_failures": 3, "expires_at": "2026-12-31"}
  },
  "last_assessment_ref": "koredoc:...",
  "next_eligible_at": "2026-08-03T08:00:00+01:00"
}
```

`task_id` is stable across plan revisions and worker attempts. An iteration is one bounded pass
through the runner, not an unbounded conversation. Start criteria determine when that pass may
run: a schedule, an incoming artefact, a failed-worker retry window, a freshness deadline, or a
human request. Exit criteria determine when it must finish, pause, or request review; they include
success evidence, budget and retry limits, expiry, and authority boundaries.

The task control record should use a specific state machine: `active`, `waiting_for_worker`,
`waiting_for_input`, `retry_scheduled`, `needs_review`, `blocked`, `complete`, and `cancelled`.
Each iteration appends an immutable event record with its attempt IDs, inputs, worker chat IDs,
artefacts, assessment decision, and the computed `next_eligible_at`. The control record is the
current state; the event journal explains how it got there.

This turns indefinite work into governed recurrence. A task does not run merely because it exists:
it runs only when its start criteria are met and it has not met its exit criteria.

## Boundaries between the components

| Component | Use it for | Do not use it for |
| --- | --- | --- |
| Scratchpad | Exact intermediate tool values within one chat. | Hand-off between independent task layers. |
| Datasets | Structured working sets and transformations within a session. | The sole record of a durable project stage. |
| KoreDocs | Durable human-readable reports, embedded metadata, lineage, and metadata search. | Queue state or execution leases. |
| WorkerChats | Isolated, constrained execution with a single explicit result. | Ordinary linear work that a current chat can do directly. |
| Objective plan | Declaring steps, contracts, acceptance checks, and state. | Evidence that a step really completed. |
| Assessment | Deciding whether recorded evidence satisfies a contract. | Broadening objectives or silently lowering standards. |

The durable interface between task layers is KoreDocs, KoreData, or another explicit stored
artefact reference. Scratchpad and session datasets are chat-local conveniences; they are not
the architecture for a long-running objective.

## WorkerChats: isolated execution, not inherited context

WorkerChats replaces the old Delegate skill. A parent can create a configured worker without
switching away from its current conversation:

```text
Parent chat
  └─ chat_spawn(...)
       └─ Worker chat
            ├─ own session and scratchpad
            ├─ exact prompt and structured inputs
            ├─ narrow tool allowlist
            ├─ queued / running / completed / failed lifecycle
            └─ one durable result object
```

The public interface is:

```text
chat_spawn(prompt, tools_allowlist, result_target, result_format, max_iterations, inputs)
chat_status(chat_id)
chat_result(chat_id)
```

Each worker has a `chat_...` ID, an isolated `worker_chat_...` session, a queue entry, a log,
and a durable record under `datacontrol/koreagent/worker_chats`. Its result has a concise summary,
artefact references, saved keys/datasets, execution metadata, and an error field. The parent does
not inherit the worker's private chat history or hidden reasoning. It reads `chat_result` just as
it would read a KoreDoc result.

This distinction is intentional. A worker's context is an implementation detail useful for
debugging; its explicit result contract is the inter-layer interface.

### Appropriate WorkerChat use

- A bounded research or extraction stage with a clear output contract.
- An isolated document or dataset generation step with a deliberately limited tool set.
- Several independent analyses which may later be compared by a controller.
- A plan step that benefits from separate context, a different model configuration, or queueing.

### Inappropriate WorkerChat use

- A direct calculation or single tool call.
- A vague instruction such as "investigate this" without expected outputs or checks.
- Passing private conversational knowledge as the worker's only input.
- Replacing normal sequential prompts merely because a task has more than one step.

For ordinary linear work, subsequent prompts in the same chat are simpler and more observable.
WorkerChats are a job primitive, not pseudo-conversation branching.

## Proposed Objective Plan skill

An Objective Plan skill should author and validate a durable JSON plan, normally stored in a
KoreDoc with metadata such as `artefact_type: objective_plan`. It should make the objective,
inputs, outputs, success criteria, and step states explicit before substantial work begins.

```json
{
  "schema_version": 1,
  "plan_id": "obj_...",
  "objective": "Produce an evidence-backed UK product-market briefing.",
  "success_criteria": [
    "A published briefing KoreDoc exists.",
    "Each material claim has source artefact references.",
    "Market-sizing assumptions and uncertainty are stated."
  ],
  "inputs": [
    { "ref": "koredoc:...", "role": "brief", "required": true }
  ],
  "steps": [
    {
      "id": "collect_sources",
      "purpose": "Collect a bounded primary-source register.",
      "depends_on": [],
      "execution": {
        "mode": "worker_chat",
        "tools_allowlist": ["search_web", "fetch_page_text", "koredocs_doc_create"],
        "max_iterations": 6
      },
      "outputs": [
        {
          "role": "source_register",
          "metadata_filter": {
            "artefact_type": "source_register",
            "plan_id": "obj_...",
            "step_id": "collect_sources"
          }
        }
      ],
      "acceptance": [
        "At least three primary sources exist.",
        "Every source records a URL and retrieval date."
      ],
      "state": "pending"
    }
  ],
  "state": "active",
  "revision": 1
}
```

Plans contain references, not copied source text. A durable reference should be a KoreDoc ID,
metadata query, KoreData reference, or a versioned dataset artefact. Scratchpad keys are valid
only for explicitly session-local steps.

### Required invariants

- Outputs record `plan_id`, `step_id`, `attempt_id`, producer, and `input_refs` metadata.
- A step becomes `completed` only when its recorded acceptance evidence is present.
- Retries create new attempt records; they do not silently overwrite an earlier result.
- Plan revision and produced artefact IDs are recorded together.
- Success criteria change only through an explicit, attributable revision.

## Task runner

The runner performs one eligible plan step at a time. It is deliberately not a free-running
"do everything" loop.

1. Load and validate the plan KoreDoc.
2. Select one pending or recoverable step whose dependencies are complete.
3. Resolve and verify each required durable input.
4. Create an immutable attempt record containing plan revision, selected tools, model, expected
   outputs, acceptance checks, timestamps, and an idempotency key.
5. Use direct execution for deterministic small work, or `chat_spawn` for an isolated worker.
   The worker receives durable references and a narrow allowlist; persist its `chat_id` in the
   attempt record.
6. In a later runner operation, call `chat_status` / `chat_result`, validate the resulting
   artefacts and metadata, and record the evidence.
7. Patch the plan to `completed`, `failed`, `blocked`, or `needs_review` before returning.

WorkerChats is an execution choice inside the runner, not a planning mode. The plan declares a
contract; the runner chooses direct execution or a worker according to isolation, cost, and risk.

## Assessment skill

After a terminal attempt, an assessor reads only the plan, attempt record, and referenced
artefacts. It returns a structured decision:

```json
{
  "decision": "continue",
  "evidence": ["koredoc:..."],
  "completed_steps": ["collect_sources"],
  "next_step": "synthesise_briefing",
  "risks": ["Competitor-price coverage is weak."],
  "plan_patch": [],
  "human_decision_required": false
}
```

Permitted decisions are `continue`, `retry`, `replan`, `blocked`, `needs_review`, and `complete`.
The assessor may recommend a patch, but cannot broaden the objective, relax success criteria,
spend beyond a budget, or publish externally without authority.

Mechanical validation should dominate whenever possible: artefact existence, metadata, lineage,
record counts, citations, and reproducible calculations. Model judgement remains valuable for
relevance, contradictions, and whether the result answers the objective, but it is not independent
verification.

## Where this works

- Research and briefing pipelines with source registers and final reports.
- Market, product, company, and country analysis with reusable intermediate artefacts.
- Data cleaning, extraction, classification, and report generation with record-shaped contracts.
- Code or document workflows where tests, diffs, or rendered output can validate a stage.
- Lower-capability models, where explicit plans and artefacts reduce reliance on one large context.

## Where it fails or becomes expensive

- Vague objectives yield vague plans; no loop can manufacture a testable definition of quality.
- Undisciplined metadata and missing lineage make durable search as unreliable as filenames.
- Multiple model passes can repeat the same false premise; they are not independent evidence.
- Changing external facts require retrieval dates, expiry rules, and rerun decisions.
- WorkerChats add latency, cost, queue contention, and more failure states.
- Concurrent runners need leases and compare-and-swap plan revisions to prevent duplicate work.
- A plan can become bureaucracy for short, low-risk tasks.
- A new chat clears conversational context but does not establish correct durable state by itself.

## Implementation sequence

1. Stabilise WorkerChats and test explicit result contracts across fresh chats/processes.
2. Define and validate the objective-plan JSON schema and KoreDoc metadata conventions.
3. Build read-only plan inspection explaining the next eligible step and why.
4. Add a serial, single-step runner with idempotency and mechanical output checks.
5. Add WorkerChat execution and durable attempt records to that runner.
6. Add the constrained assessment skill and human-review boundary.
7. Add end-to-end tests that inspect saved artefacts and queue/result records, not merely model prose.

The first useful version should be serial, bounded, and inspectable. Parallel workers, autonomous
replanning, and broad background execution should follow only once the artefact and validation
contracts are dependable.
