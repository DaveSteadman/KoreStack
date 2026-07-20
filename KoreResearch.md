# KoreResearch
## Problem
`KoreAgent` is good at interactive execution.
It is not a good home for research that is:
- long-running
- stateful
- evidence-heavy
- multi-stage
- resumable
- inspectable

The failure mode is predictable.
One chat session gets asked to:
- plan the work
- do the work
- remember the work
- branch the work
- verify the work
- resume the work later
- publish the work

That is too much responsibility for one interactive prompt/tool loop.

## What KoreResearch Solves
`KoreResearch` exists to solve one problem:

- how to run research that is bigger than one agent conversation without losing structure, evidence, progress, or control

It should make long-horizon work:
- bounded
- resumable
- inspectable
- evidence-backed
- publishable

## Core Position
`KoreResearch` should not be another ordinary skill inside `KoreAgent`.
It should be a separate subsystem that manages research runs as first-class objects.

`KoreAgent` remains the execution engine.
`KoreResearch` becomes the run manager.

## What It Is Not
`KoreResearch` is not:
- a single huge prompt
- a hidden background thread in `KoreAgent`
- a leaf tool
- an unbounded delegate loop
- a reimplementation of ordinary search

## Why The Split Matters
The split keeps responsibilities clean.

`KoreAgent` should focus on:
- one bounded task
- one bounded train of thought
- one prompt/tool execution slice

`KoreResearch` should focus on:
- what to do next
- what evidence is required
- whether the result was good enough
- whether to continue, branch, retry, wait, or stop

If those responsibilities stay mixed, the system drifts toward long, fragile, low-visibility sessions.

## Clean Ownership
### `KoreAgent`
Owns:
- interactive session runtime
- prompt construction for a single slice
- tool calling
- immediate execution behavior
- bounded outputs

### `KoreResearch`
Owns:
- run lifecycle
- decomposition
- branch selection
- scheduling
- checkpoints
- progress visibility
- result evaluation
- artifact publication

### Shared orchestration
Owns:
- plan state
- guardrails
- retry rules
- budget rules
- stop / pause / blocked rules
- evidence precedence rules

The rule is simple:
- separate runtime responsibility
- do not duplicate reasoning policy

## Relationship To KoreAgent
`KoreResearch` should submit bounded work into dedicated `KoreAgent` sessions over time.

That means:
- `KoreResearch` decides the next bounded task
- `KoreAgent` executes it
- `KoreResearch` records the outcome and decides what happens next

This is the whole point of the subsystem.

## Delegate Relocation
When a real `KoreResearch` subsystem exists, the current durable `delegate` skill should move into it.

Why:
- durable delegation is a planning decision
- decomposition is a planning decision
- branch management is a planning decision
- result integration is a planning decision

If `delegate` stays as the main mechanism inside `KoreAgent`, the working agent has to:
- decide whether to split the task
- invent the subtasks
- launch children
- track children
- merge the results

That weakens focus.
Moving durable delegation into `KoreResearch` lets `KoreAgent` stay focused on a single bounded train of thought.

`KoreAgent` may still keep a lightweight inline delegate for narrow context-isolation cases.
But long-horizon branching belongs in `KoreResearch`.

## Execution Model
`KoreResearch` should run in bounded slices, not one huge loop.

Each slice should:
1. read persisted run state
2. choose the next step
3. create an execution brief
4. submit that brief to the run's `KoreAgent` session
5. wait for the bounded result
6. validate the result
7. update run state
8. schedule the next slice or stop

This gives:
- pause / resume
- retry with backoff
- checkpointing
- crash recovery
- time and token budgeting

## Execution Brief
The handoff to `KoreAgent` should be explicit.

Suggested brief fields:
- `step_id`
- `objective`
- `context`
- `constraints`
- `allowed_sources`
- `required_evidence`
- `expected_output`
- `completion_test`
- `failure_test`

The brief exists to keep the split clean:
- `KoreResearch` defines the task and criteria
- `KoreAgent` handles execution

## Research Map
`KoreResearch` should keep a research map.
It should not just keep a chat transcript.

The first version can be simple:
- a run
- a branch tree
- a task queue
- records produced by tasks
- links to sources and artifacts

The map is for:
- decomposition
- navigation
- resumability
- gap tracking
- targeted retries

## Memory Map vs RAG
This memory map is not the same thing as a RAG database.

RAG is mainly about:
- storing chunks
- retrieving chunks

`KoreResearch` needs more:
- run state
- branch state
- task state
- output state
- evidence state

So:
- source documents may be chunked for retrieval
- chunks may support research records
- but the core object in `KoreResearch` should not be "chunk"

The memory map is an operational structure, not just a retrieval structure.

## Avoid Overfitting The Schema
The system should not assume all research looks like:
- `Entity`
- `Claim`
- `Evidence`

Those shapes are useful for some tasks, but not all tasks.

Examples that may need different shapes:
- literature review
- market scan
- troubleshooting investigation
- design-option comparison
- corpus enrichment

So the stable schema should stay small.
The research content should stay flexible.

## Stable Envelope, Flexible Records
Keep the operational envelope strict.
Keep the research payload flexible.

Useful stable fields:
- `run_id`
- `node_id`
- `task_id`
- `parent_id`
- `kind`
- `status`
- `objective`
- `refs`
- `payload`
- `created_at`
- `updated_at`

The actual research record should usually live in JSON payloads.
That avoids overfitting `KoreResearch` to one ontology too early.

## Record Model
A good default model is:
- `Run`
- `Node`
- `Task`
- `Source`
- `Artifact`
- `Record`

Where `Record` is the flexible knowledge-bearing object.

That allows one run to use:
- catalogue-style factual records
- comparison records
- note records
- synthesis records

without forcing every run into one global table design.

## Child Result Contract
Sub-conversations should return structured contributions, not loose prose.

The exact schema can vary by run, but every result should at least say:
- what task it answered
- what it produced
- what sources it used
- what remains unresolved
- whether it passed its completion test

A child saying "I found interesting sources" should not count as completion.

## Session Ownership
Each research run should own its own dedicated `KoreAgent` session.

This avoids:
- collisions with foreground chat
- context pollution
- mixed logs
- race conditions over prompt history

The user may inspect the session.
But while active, the run owns it.

## Observability
Long-running work without visibility is not trustworthy.

Each run should expose:
- current status
- current phase
- current step
- current execution brief summary
- retries and failures
- outputs and artifacts
- last progress time
- owning session id

Every run should have its own page.

## Run States
Useful states:
- `queued`
- `planning`
- `researching`
- `waiting`
- `blocked`
- `paused`
- `complete`
- `failed`
- `cancelled`

## Guardrails
`KoreResearch` needs hard boundaries.

At minimum:
- max cycles per run
- max retries per step
- token and wall-clock budgets
- evidence-before-finalization checks
- duplicate-work suppression
- clean `blocked` and `waiting` states

## Integrations
`KoreResearch` should integrate with:
- `KoreAgent` for execution
- `KoreLiveWeb` for search/fetch visibility
- `KoreData` for retrieval and persistence
- `KoreDocs` for published outputs

## First Vertical Slice
The first version should stay narrow.

Build:
1. one research run type
2. one persisted run model
3. one branch tree
4. one execution-brief format
5. one bridge to dedicated `KoreAgent` sessions
6. one run page
7. one report output path

Do not start with:
- arbitrary recursive branching
- a full knowledge graph
- broad permanent agent-role systems
- a second copy of `KoreAgent` planning logic

## Example Task Shape
A good early use case is a corpus-style task:

- build a list of hundreds of radar types
- gather structured information for each
- track missing fields
- revisit weak records
- publish the dataset and evidence pack

This is exactly the sort of work that is awkward in one chat and natural in a persisted research run.

## Summary
`KoreResearch` exists to solve long-running research that does not fit cleanly inside one interactive agent conversation.

It should:
- manage runs
- decompose work
- relocate durable delegation upward
- keep state and evidence durable
- keep `KoreAgent` focused on bounded execution
- make the work resumable, inspectable, and publishable
