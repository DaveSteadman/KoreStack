# Advanced reasoning: durable objective loops

## Purpose

KoreStack already has many of the parts needed for work that takes longer than one
chat turn: bounded tool execution, delegated workers, a session scratchpad,
structured datasets, and KoreDocs that can hold embedded metadata and lineage.
The missing part is a durable controller which makes the current objective,
evidence, next action, and completion conditions explicit.

The intended result is not an agent that is told to "keep going". It is a
repeatable, inspectable objective loop:

```text
objective -> plan -> execute one bounded step -> verify evidence -> assess -> next step or finish
```

Every transition should be recoverable after a new chat, a restart, or a human
review. A later agent must be able to discover what has already been produced
without trusting a compressed conversation summary.

## Useful pieces already present

| Component | Current value | Limitation for long-running work |
| --- | --- | --- |
| Scratchpad | Holds exact tool results under short keys; supports query, peek, and token substitution. | It is primarily session-scoped and is not a durable project record. A key alone is not meaningful to a later chat. |
| Datasets | Preserve structured record sets, filters, manifests, and lineage within a session. | They need an explicit durable hand-off and stable project-level naming. |
| KoreDocs | Provides human-readable artefacts, embedded JSON headers, metadata search, history, and `input_refs`. | It does not yet define a project plan, execution lease, validation contract, or semantic graph of dependent steps. |
| Delegation | Gives a worker an isolated tool loop, narrow allowlist, explicit input/output contract, and a collectable result. | A controller still has to decide when to delegate, poll and collect the result, make it durable, and handle retries or disagreement. |
| `/chat new` | Separates a later conversation from prior conversational knowledge. | The next chat needs a durable plan and artefact references; otherwise it starts without useful state. Chat-sequence testing does not yet support this transition. |
| Planner and orchestration loop | Selects tools, constrains active tools, performs bounded tool rounds, and records activity. | It is turn-oriented. It has no durable, user-visible objective state machine across separate runs. |

These components should remain distinct. Scratchpad is fast temporary memory;
datasets are structured working sets; KoreDocs are durable artefacts; delegates
are bounded workers. A plan should refer to them rather than duplicate their
contents.

## Observed delegate lifecycle

The current `delegate` implementation is a queue-native, asynchronous child
run. Calling it creates a durable task record under
`datacontrol/koreagent/delegate_tasks`, allocates an isolated session named
`delegate_task_<task_id>`, and appends the child job to the shared serial LLM
task queue. The call returns immediately with `status: "queued"` and a task ID.
The queue then runs the child separately; its result and status are persisted in
the task record. This is a separate orchestration session, not necessarily a
user-visible chat created by `/chat new`.

By default, the parent does not automatically become a new queued continuation
after the child finishes. Its current tool call receives the queued response,
and a later controller action can use `delegate_status` and `delegate_collect`
to observe and consume completion. For plan-runner chains, the delegate
contract now supports an explicit `process.continuation` object. When enabled,
the terminal child task queues one fresh parent-session orchestration run with
the task ID, status, durable output target, child result, and the planner's
continuation instruction. The continuation state and its response are recorded
with the delegate task. It runs only after a successful child unless
`run_on_failure` is explicitly set.

The generic delegate contract currently permits session-local transfer:
`data_in.scratchpad_keys` and `data_in.datasets` are copied from the parent
session to the child, while result targets may write back to parent scratchpad
or dataset storage. That is useful for one-chat delegation, but it is not an
acceptable hand-off between durable task layers. The objective-plan runner
should use a stricter delegate profile:

- Inputs are durable KoreDoc or KoreData references, or metadata searches that
  resolve to those references; no scratchpad keys, session datasets, or inline
  copied source text.
- Outputs are durable artefacts with `plan_id`, `step_id`, `attempt_id`, and
  `input_refs` metadata; no scratchpad or session-dataset result target.
- The runner persists the task ID in its attempt record, then polls, collects,
  validates, and schedules the next eligible step as a separate operation.

This preserves the useful isolation and queueing behaviour of `delegate` while
keeping conversational working memory out of the interface between task layers.

## Proposed capability: an Objective Plan skill

Create a skill that authors and validates a JSON objective plan. The plan is a
durable control document, ideally saved as a `.koredoc` whose embedded metadata
marks it as `artefact_type: objective_plan`.

The skill should create a small plan before substantial work starts, revise it
only through explicit patches, and expose a compact status view. It must not
pretend that a plan is proof that work was done.

### Plan shape

```json
{
  "schema_version": 1,
  "plan_id": "obj_...",
  "objective": "Produce an evidence-backed UK product-market briefing.",
  "success_criteria": [
    "A published briefing KoreDoc exists.",
    "Every material claim has a source artefact reference.",
    "Market sizing assumptions and uncertainties are stated."
  ],
  "inputs": [
    {
      "ref": "artifact:...",
      "role": "brief",
      "required": true
    }
  ],
  "steps": [
    {
      "id": "collect_sources",
      "purpose": "Collect a bounded set of relevant primary sources.",
      "depends_on": [],
      "process": {
        "mode": "delegate",
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
        "At least three sources are present.",
        "Each source has a URL and retrieval date."
      ],
      "state": "pending"
    }
  ],
  "outputs": [
    {
      "role": "final_briefing",
      "required_metadata": {
        "artefact_type": "market_briefing",
        "plan_id": "obj_...",
        "status": "published"
      }
    }
  ],
  "state": "active",
  "revision": 1
}
```

The schema should use references rather than copied text. A `ref` can identify a
KoreDoc artefact ID, a metadata query, a dataset name and revision, or a named
scratchpad key only when the work remains in the same session.

### Required invariants

- Each output declares who produced it, which plan and step produced it, and its
  input artefact IDs.
- A step can become `completed` only after recorded acceptance evidence exists.
- A retry must create a new attempt record; it must not silently overwrite an
  earlier output.
- The plan revision and the resulting artefact IDs must be recorded together.
- The plan's success criteria are stable unless a human or authorised assessor
  records a revision rationale.

## Proposed task runner

The runner executes one eligible plan step at a time. It is deliberately not a
free-running "do everything" loop.

1. Load the plan KoreDoc and validate its schema.
2. Find a `pending` or recoverable `failed` step whose dependencies are complete.
3. Resolve inputs through KoreDoc metadata search and verify that required
   artefacts exist.
4. Create an immutable attempt record with timestamps, selected tools, model,
   plan revision, and expected output contract.
5. Execute locally for deterministic work, or call `delegate` for an isolated
   bounded worker. Give the worker only durable references and a narrow tool
   allowlist; persist the returned delegate task ID in the attempt record.
6. In a later runner operation, poll and collect the worker result. Verify output
   existence, metadata, lineage, and deterministic checks where possible.
7. Patch the plan state to `completed`, `blocked`, `failed`, or `needs_review`.
   Persist the patch and attempt record before returning control.

Delegation should be a mechanism inside the runner, not the plan itself. A plan
declares the contract; the runner decides whether the current step warrants a
worker. This keeps small deterministic steps cheap and makes expensive work
explicitly budgeted.

The runner should require idempotency keys. For example, an attempt may write
`plan_id`, `step_id`, and `attempt_id` into its output metadata. On recovery it
can find an already-created valid output rather than produce duplicates.

## Proposed assessment skill

After each completed or failed attempt, an assessment skill should inspect only
the plan, attempt record, and referenced artefacts. It should return a structured
decision, not free-form encouragement:

```json
{
  "decision": "continue",
  "evidence": ["artifact:..."],
  "completed_steps": ["collect_sources"],
  "next_step": "synthesise_briefing",
  "risks": ["Source coverage is weak for competitor pricing."],
  "plan_patch": [],
  "human_decision_required": false
}
```

Permitted decisions are `continue`, `retry`, `replan`, `blocked`,
`needs_review`, and `complete`. The assessor may recommend a patch, but should
not broaden the objective, relax a success criterion, spend beyond a budget, or
publish an external result without defined authority.

Assessment needs both mechanical and judgement checks:

- Mechanical: required artefacts exist; metadata matches; lineage is complete;
  citations or record counts meet a contract; a calculated value can be
  independently recomputed.
- Judgement: evidence is sufficiently relevant; contradictions are represented;
  the output answers the stated objective rather than merely containing words
  associated with it.

Mechanical checks should dominate whenever possible. The recent data-pipeline
test showed why: a model can report success after a tool error or a weakened
metadata query. A durable runner must record and surface those errors.

## Where this model works

This is well suited to bounded, artefact-centred tasks:

- Research and briefing pipelines with explicit source registers and a final
  report.
- Market, product, company, or country analysis where intermediate data tables
  and assumptions must survive later review.
- Data cleaning, classification, extraction, and report generation where each
  transformation has a record-shaped input and output.
- Code or document workflows where a reviewer can validate tests, diffs, or
  rendered output before the next step.
- Work split among delegates when their remits, tools, and output targets can be
  stated narrowly.

It is especially valuable with lower-capability models: the plan and artefacts
reduce the need to retain a large implicit mental model in one context window.

## Where it fails or becomes expensive

- Vague objectives produce vague plans. A loop cannot manufacture a testable
  definition of "make this good".
- Metadata is only useful if values are disciplined. Inconsistent names,
  arbitrary tags, or missing `input_refs` turn search into guesswork.
- LLM judgement is not independent verification. Two model passes can repeat
  the same mistaken premise.
- External facts change. Time-sensitive sources need retrieval dates, expiry
  rules, and a decision on whether a plan should rerun.
- Delegates add latency, cost, and failure modes. They should not be used for a
  task one direct tool call can complete.
- Concurrent runners can duplicate work or overwrite state without leases and
  compare-and-swap plan revisions.
- A plan can become bureaucracy for a short task. Planning depth must be
  proportional to risk, duration, and the number of independent artefacts.
- A new chat only clears conversational context. It does not by itself establish
  correct durable state; the plan and artefact references must be sufficient.

## Suggested implementation sequence

1. Define and validate the plan JSON schema, including references, step states,
   acceptance evidence, and revision history.
2. Add KoreDoc helpers to create, find, and patch `objective_plan` artefacts by
   metadata.
3. Build a read-only plan inspector before adding execution. It should explain
   which step is eligible and why.
4. Implement a single-step runner with deterministic output and lineage checks.
5. Add delegated execution with attempt records, timeouts, and idempotency keys.
6. Add the assessor with a constrained decision vocabulary and human-review
   boundary.
7. Add end-to-end tests that create an artefact in one chat and consume it from
   a fresh chat or process. Test the saved files and tool logs, not only model
   final text.

The first successful version should be narrow: one plan, serial steps, explicit
human approval for re-planning and publication, and strong inspection tools.
Parallel scheduling, autonomous replanning, and broad background execution can
follow only after the artefact and validation contracts are dependable.
