# KoreStack Next Iteration

> Status: the legacy planning and Workflow system was removed on 16 August
> 2026. The remaining sections describe the next implementation stages.

## Objective

Build a stronger agent framework: one that can take on broader prompts, use
tools reliably, retain useful work, and generate grounded answers and outputs
without accumulating task-specific prompt rules.

The framework should move reliability from model instructions into explicit
capability routing, durable artifacts, execution evidence, and validation.

## What We Learned

Recent SavedSearch-to-email runs exposed several framework weaknesses:

- A broad tool catalog was too expensive and distracting to expose in full.
- Individual tool activation caused recovery rounds because tools needed for a
  natural next step were not available together.
- Large tool results were truncated into the chat thread, leaving the model to
  infer what it had already reviewed.
- The model could retrieve and assess data, but rebuilding selected records in
  Python lost fields and caused a final failure.
- The current high-level Workflow and lightweight planning mechanisms added
  failure modes without providing dependable execution control.
- A textual model response was treated too readily as a successful deliverable.

The answer is not a larger global prompt, nor a growing list of special rules
for SavedSearches, news, emails, or any other individual use case.

## Capability Routing and Tool Arrangement

The active-tool concept remains correct. The selection unit changes from
individual functions to coherent, dynamically maintained tool groups.

### Tool hierarchy

```text
Always-on control
  └─ Capability router
       ├─ Data research
       ├─ Compose / transform
       ├─ Files / documents
       ├─ Communications / delivery
       ├─ Code / computation
       └─ Workflow management (future replacement, if needed)
```

The model normally sees a small control plane and the current active list. It
can list and activate a named group, or inspect the full catalogue and add an
individual tool when no group fits. It does not need to reason over the full
function catalogue on every prompt.

### Tool bundles

Tool groups are generated manually with `/tools groups reevaluate`. The command
builds the groups directly from the live local and MCP provider, skill, and
function namespaces: whole subsystems where they fit, and resource-level
subsystems where they do not. It validates complete coverage and the
sixteen-tool cap before saving to `datacontrol/koreagent/ToolSets.json`. The
file is used directly on later starts; no housekeeping prompt is required.

For example, a SavedSearch group may contain:

- Run or list SavedSearches.
- Inspect a collection.
- Read compact record batches.
- Dedupe records.
- Select records into a derived collection.
- Enrich selected records.

Each group has one to sixteen members. Group activation appends members to the
active FIFO list. Re-activating a tool moves it to the newest position, and an
overflow evicts the oldest tools. Individual activation remains the fallback.

### Capability metadata

The first implementation does not change individual tool execution or require
new contracts. The existing tool name, description, parameter metadata, and
MCP discovery information are enough for group generation. Richer metadata can
be introduced later only where it proves useful.

### Progressive disclosure

The catalogue presents named groups before individual functions, for example:

```text
Research a collection
  Run SavedSearch, review records, select/dedupe, fetch detail

Deliver content
  Bind destination, pause/resume, validate and publish a draft
```

The normal agent can choose a group. Only the `/tools groups` slash command can
create or alter groups. Dynamic server availability, approval states, and
additional provenance are explicitly deferred until this first version is in
use.

Do not collapse every family into one giant multiplexed tool with an
`operation` argument. That saves schema tokens but creates an error-prone
mini-language. Prefer a small number of well-shaped functions in each active
bundle.

## General Execution Model

Do not replace the old planner with a universal fixed state machine such as:

```text
collection → selection → draft → delivery
```

That is useful for content work but is the wrong shape for debugging, coding,
conversation, planning, or systems administration.

Instead, use a general execution controller that tracks execution facts:

```text
Intent
Artifacts available
Claims and evidence obtained
Pending decisions
Requested side effects
Acceptance conditions
```

The agent decides the work structure. The controller ensures that actions form
a coherent, observable, safe progression.

### Generic controller invariants

- Do not claim an action succeeded without supporting evidence.
- Do not invoke unavailable capabilities.
- Preserve artifacts rather than rebuilding or losing them in prose or code.
- Distinguish a draft from a delivered output.
- Require evidence appropriate to the requested acceptance conditions before
  claiming a structured task is complete.
- Record external side effects and their receipts.
- Report a blocked dependency rather than wandering through tools.

### What the controller must not require

- Not every task needs a dataset.
- Not every task needs planning.
- Not every task needs a selection phase.
- Not every task writes a document.
- Not every task delivers content.

Capability profiles such as research, code change, document editing, and
publication are optional starting points. The controller must allow the agent
to skip, branch, repeat, or stop steps when the task requires it.

## Artifact-Centred Work

The framework should treat useful intermediate work as durable typed artifacts,
not only as messages in a growing chat thread.

For data work, a common lifecycle is:

```text
collection → reviewed collection → selected collection
           → enriched collection → draft → validated deliverable → delivery
```

This is an example pattern, not a universal required flow.

The agent retains the semantic decisions humans need it to make:

- Relevance and quality assessment.
- Cross-record duplicate and overlap recognition.
- Selection and prioritisation.
- Writing, structure, voice, and explanation.

The framework handles retention, integrity, lineage, validation, and safe
delivery.

### Dataset downselection

`dataset_filter` is useful for isolated keep/drop decisions, but it is not an
editorial selection mechanism. Its per-record LLM calls cannot compare all
stories globally or choose the best fixed number.

Add deterministic collection operations such as:

- `dataset_select_indices(source, indices, save_as)`.
- `dataset_dedupe(source, by, save_as)`.
- `dataset_project(source, fields, excerpt_chars, save_as)`.

The primary agent should read concise batches covering every candidate, make
the semantic decision across the full set, then commit its selected record
indices into a derived dataset. It can then enrich only the retained records.

This prevents reconstruction in Python and preserves every source field needed
for final synthesis.

### Provenance and lineage

Artifacts should carry their inputs, source run, validation result, and status.
For example, a draft email should know:

- The selected records it was derived from.
- The run that created it.
- Its format and validation result.
- Whether it has been delivered.
- Its delivery receipt, if delivered.

The same principle applies to files, documents, graph changes, code changes,
and API submissions.

## Context Architecture

Context should be assembled, not merely accumulated.

The model should receive a focused execution context containing:

- The current user intent.
- Current task state.
- Relevant artifacts and concise manifests.
- Selected evidence.
- Recent decisions and unresolved errors.
- The active capability bundle.

Conversation history remains a source of possible context, not execution
memory.

Large results should expose compact review packets, such as record index,
title, source, date, URL, and bounded excerpt. The framework should record
coverage such as `24/24 candidates reviewed`; the model should not have to
infer this from several truncated tool transcripts.

Full text remains behind a durable artifact handle and is retrieved after
selection when necessary.

## Validation and Delivery

Text returned by a model is not automatically a successful deliverable.

Validation should be a first-class capability and record evidence for relevant
constraints:

- Requested count of items, sections, rows, or stories.
- Required format such as HTML, JSON, a file, or a KoreDocs document.
- Non-empty and non-truncated content.
- Source references and grounding.
- Existence of requested files or datasets.
- Confirmation receipts for external operations.

Some validation can be deterministic and some may use a separate evaluator.
The result must be recorded in task state, not injected as another vague
instruction to the same model.

Delivery must receive a validated, current-run deliverable artifact. It must
not mean “publish the previous chat response.” This prevents stale, empty, or
incidental messages from being sent.

## Separate Responsibilities

Do not require one free-running model loop to be planner, researcher, data
editor, tool operator, writer, and evaluator at the same time.

Even using one underlying model, use distinct responsibilities:

- Decide the next meaningful action.
- Execute the action and update artifacts.
- Write the final response from validated artifacts.
- Evaluate whether the output satisfies the request.

This is not necessarily a multi-agent design. It is separation of incompatible
responsibilities so one weak step cannot corrupt the entire run.

## Evaluation, Replay, and Observability

The test suite should measure framework behaviour as well as plausible final
text.

For each run, record:

- Selected capability bundle.
- State transitions.
- Tool calls, latency, retries, and errors.
- Artifact lineage.
- Validation outcomes.
- Final and delivery results.

Create replayable scenarios for representative work:

- SavedSearch-to-email.
- SavedSearch-to-SFTP file.
- Write a KoreDocs document from a dataset.
- Research a factual topic.
- Inspect and fix code.
- Handle empty results.
- Recover from a tool failure.
- Refuse publication without a valid current-run deliverable.

Track at least:

- Tool-recovery rounds per run.
- Repeated retrieval rate.
- Prompt-token spend per completed task.
- Percentage of runs reaching a validated artifact.
- Empty or incomplete final-response rate.
- Delivery attempts without a valid current-run deliverable.

## Suggested Delivery Order

```text
1. Complete removal of the failed planning layers. Done.
2. Introduce capability bundles and richer catalog metadata.
3. Introduce typed task state and artifact lineage.
4. Build the focused context assembler.
5. Add validation and delivery contracts.
6. Add trace replay and behavioural evaluation.
7. Add more advanced model-role separation where evaluation shows it helps.
```

The first five improvements strengthen the framework independently of model
choice. Better models can then improve judgement and writing instead of being
used to compensate for missing state, unreliable context, and ambiguous tool
behaviour.

## Non-Goals

- Do not reintroduce a large universal planning prompt.
- Do not encode an AI-news-specific workflow into the Agent.
- Do not expose every individual tool to every model call.
- Do not replace structured tool contracts with a giant multipurpose tool.
