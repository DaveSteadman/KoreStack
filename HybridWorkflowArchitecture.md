# Hybrid Prompt and Python Workflows

## Purpose

KoreStack needs a reliable way to combine deterministic application logic with language-model work.

The current approach sends procedural instructions to an LLM and relies on it to choose and call the correct tools. This is acceptable for exploratory work, but it is not acceptable for scheduled jobs where the same input must produce the same sequence of operations every time. The Daily AI News job demonstrates the problem: sometimes it produces the report, while other runs produce a short discussion or clarification response.

The target is a workflow that can interleave:

- deterministic Python actions;
- ordinary prompts sent to the LLM;
- validation and control-flow checks;
- calls to other named workflows;
- explicit publication or delivery actions.

The LLM remains useful as a text and reasoning engine. It is not responsible for following the procedural parts of a job.

## Target workflow

A workflow is a named function made from ordered steps. A step is either an action or a prompt.

An illustrative Daily AI News workflow is:

```text
function daily_ai_news:
    python workspace.clear()
    python comms.bind(to_list="AI-News", subject="AI News Daily", start_paused=True)
    python koredata.saved_search("AINews").save_as("ai_news").require_nonempty()

    prompt:
        Using dataset "ai_news", produce the five most significant stories.
        Write a complete HTML email. Do not ask questions or describe the task.

    python response.require_html(min_length=1000)
    python comms.publish_previous()
```

This is the conceptual syntax, not necessarily the final storage format.

The important execution contract is:

1. Python steps execute exactly as written.
2. Prompt steps are the only steps sent to the LLM.
3. Every step must complete successfully before the next step starts.
4. A failed validation stops the workflow.
5. A stopped workflow does not publish the previous response.
6. Every step records its inputs, result, duration and error state.

## Workflow functions

Workflows should be reusable and callable by name from KoreCron, slash commands, other workflows and eventually the UI.

```text
/workflow run daily_ai_news
```

A workflow may call another workflow:

```text
function daily_ai_news:
    call prepare_ai_news_dataset
    prompt: Produce the HTML report using dataset "ai_news".
    call publish_validated_html
```

This allows repeated sequences to be factored without forcing every individual operation into a large universal framework.

## Python actions

The first experimental version can represent actions as single-line Python-style function calls:

```text
python workspace.clear()
python koredata.saved_search("AINews", dataset="ai_news", require_nonempty=True)
python response.require_html(min_length=1000)
python comms.publish_previous()
```

These lines should not be passed to `eval` and should not provide unrestricted Python execution. They should be parsed as a deliberately small action-call grammar:

```text
namespace.function(positional_arguments, keyword_arguments)
```

Only registered namespaces and functions are callable. Arguments are restricted to JSON-compatible literal values. Attribute traversal, imports, assignments, loops, comprehensions and arbitrary expressions are rejected.

This produces the convenience of single-line Python calls while keeping execution understandable and testable.

For complex logic, the action should call a normal source-controlled Python function:

```python
@workflow_action("koredata.saved_search")
def run_saved_search(context, name: str, dataset: str, require_nonempty: bool = False):
    ...
```

The workflow definition stays concise; the implementation remains ordinary Python with tests and explicit dependencies.

## Relationship to skills and MCP

During the experiment, actions may call existing skill implementations where that is the shortest route to a working vertical slice. This is transitional, not the desired final dependency structure.

The eventual architecture should give each capability one underlying Python operation or service API:

```text
                         -> Agent tool adapter
core Python operation ---|
                         -> Workflow action
```

For example, SavedSearch execution should have one implementation used by both the Agent and deterministic workflows:

```python
koredata.run_saved_search(name="AINews")
```

An Agent-facing tool may expose that operation to an LLM. A workflow action may invoke it directly. Neither implementation should contain a second copy of SavedSearch behaviour.

Because KoreStack owns a fixed, custom-written integration set, MCP does not need to remain the internal application boundary. It can be retained temporarily while capabilities are moved behind bespoke Python interfaces, then removed once no runtime path depends on it.

## Workflow context

Every workflow run needs a small execution context. This should carry identity and state, not grow into a second version of every KoreStack API.

Initial context fields are likely to be:

- workflow run ID;
- KoreChat conversation ID;
- current step number;
- named datasets created during the run;
- previous prompt response;
- structured step results;
- cancellation and failure state.

Actions receive this context automatically:

```python
def publish_previous(context):
    response = context.previous_response
    ...
```

The context should not expose raw database connections. KoreChat, KoreData and KoreComms interactions should go through their owned Python interfaces or service APIs.

## Prompt steps

A prompt step receives an explicit view of the workflow state. It should not depend on the LLM remembering what an earlier procedural prompt was intended to do.

For example, the executor should construct the report turn with:

- the exact prompt text;
- the explicitly named `ai_news` dataset;
- any declared previous response;
- a clear output contract.

The prompt definition may declare its inputs:

```text
prompt report_html:
    datasets: [ai_news]
    output: html
    min_length: 1000
    text: |
        Select the five most significant stories and produce the final email.
```

Output declarations are enforced by Python after generation. They are not merely additional instructions to the model.

## Failure behaviour

Failure must be explicit and terminal unless a workflow deliberately declares a retry policy.

Examples:

- Unknown action: stop before execution.
- Invalid arguments: stop before execution.
- SavedSearch not found: stop.
- Empty required dataset: stop.
- LLM timeout: stop or apply the declared retry policy.
- Response is not HTML: stop; do not publish.
- Publication fails: mark the workflow failed with the generated response retained.

The workflow run should expose the failed step and error in KoreCron rather than recording only that the job was started.

Retries should repeat a specific step, not restart or improvise the whole conversation. Procedural steps should not be retried by asking the LLM what to do next.

## Daily AI News target

The final Daily AI News execution should be:

```text
1. workspace.clear()
2. comms.bind(...)
3. koredata.saved_search("AINews") -> dataset "ai_news"
4. require dataset "ai_news" to be non-empty
5. send the report prompt with "ai_news" explicitly attached
6. require a substantive HTML response
7. comms.publish_previous()
```

Only step 5 is probabilistic. Steps 1-4 and 6-7 are deterministic.

The job must never publish a clarification question, tool-selection discussion or short failure explanation as the Daily AI News email.

## Experimental first pass

The first pass should prove the execution model without reorganising all KoreStack integrations.

Minimum implementation:

1. Add a workflow registry and loader.
2. Add a restricted action-call parser.
3. Add `/workflow run <name>`.
4. Execute prompt steps through the existing KoreChat/Agent route.
5. Execute action steps through registered Python functions.
6. Tag workflow errors so KoreCron stops immediately.
7. Implement only the actions required by Daily AI News.
8. Add a run log showing every step and its result.

The first action set can be limited to:

```text
workspace.clear
koredata.saved_search
dataset.require_nonempty
response.require_html
comms.bind
comms.publish_previous
```

Existing cron prompt sequences remain supported during the experiment.

## Later reorganisation

If the experiment is successful:

1. Extract shared domain operations from MCP tool implementations.
2. Make Agent tools thin adapters over those operations.
3. Move other scheduled procedural jobs to workflows.
4. Add typed workflow editing and validation to KoreCron UI.
5. Add versioning, dry-run support and reusable workflow composition.
6. Remove MCP runtime components when no required integration depends on them.

The rewrite decision should follow a working vertical slice. The experiment should establish whether named hybrid workflows are understandable, observable and reliable before the rest of KoreStack is reorganised around them.

## Open decisions

- Whether workflow definitions live in Python, JSON, YAML or a small text format.
- Whether single-line actions are authored directly or selected through a structured UI.
- Whether prompt inputs contain full dataset records or a bounded dataset reference resolved by the Agent.
- What retry policies are permitted for LLM prompt steps.
- Whether workflow functions may branch, or remain strictly linear initially.
- How workflow definitions are versioned when scheduled jobs refer to them.
- Whether the experimental branch retains MCP adapters or removes them within the first vertical slice.

The recommended initial answers are: use a simple declarative file format, linear execution, registered actions only, no arbitrary Python, explicit datasets, no automatic retries, and coexistence with the current runtime until Daily AI News has demonstrated consistent operation.
