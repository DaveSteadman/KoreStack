# DESIGN_TOOLS

This document defines how tools should be arranged across KoreStack.

It is a requirements statement for the target design, and a guide for future changes.
It intentionally prefers simple authoritative statements over implementation detail.


## Purpose

Tools are how KoreAgent reaches capabilities outside the core model:

- local Python capabilities
- remote service capabilities
- data access capabilities
- file and workspace capabilities
- orchestration helpers

The design goal is:

1. one clear internal model of a tool
2. one clear model-facing interface
3. one clear service-facing interface
4. minimal translation
5. no ambiguity about ownership, transport, or responsibility


## Block Diagram

```text
                +---------------------------+
                |       KoreAgent LLM       |
                |   model-facing adapter    |
                +------------+--------------+
                             |
                             v
                    +------------------+
                    |  Internal Tool   |
                    |     Contract     |
                    | Spec / Call /    |
                    | Result / Errors  |
                    +----+--------+----+
                         |        |
              +----------+        +-----------+
              |                               |
              v                               v
     +-------------------+           +-------------------+
     |  Local Tool Host  |           | Remote Tool Host  |
     |  Python functions |           |  MCP services     |
     +---------+---------+           +---------+---------+
               |                               |
               v                               v
     +-------------------+           +-------------------+
     | local code, files,|           | suite services,   |
     | system actions    |           | data, docs, graph |
     +-------------------+           +-------------------+
```


## Core Requirement

The system shall have a single internal tool contract.

All tools, regardless of origin, shall be represented internally using the same concepts:

- `ToolSpec`
- `ToolCall`
- `ToolResult`
- `ToolError`

No model API schema and no transport protocol shall be treated as the internal source of truth.


## Authoritative Statements

### 1. Internal contract first

The internal tool contract is the authoritative definition of a tool inside KoreAgent.

It shall define:

- stable tool name
- human description
- input schema
- output shape
- error shape
- source type
- capability tags
- side-effect classification

Source types shall be explicit:

- `local`
- `remote_mcp`
- `builtin`


### 2. MCP is the remote service standard

MCP shall be the preferred standard for tool-capable remote microservices.

This means:

- if a service exposes tools to KoreAgent across process or network boundaries, it should expose them through MCP
- MCP is the authoritative remote tool transport
- ad hoc bespoke per-service tool RPCs should be avoided

MCP is a service boundary standard, not the internal runtime model.


### 3. Model schema is an adapter, not the design centre

The tool schema passed to the LLM is an adapter for the current model API.

It shall not be the authoritative system representation.

This means:

- OpenAI-style function schema is a rendering target
- future model providers may require different renderings
- the internal contract must remain stable if the model API changes


### 4. Local tools and remote tools must look identical to orchestration

The orchestration layer shall not have separate reasoning paths for local and remote tools.

It may dispatch them differently, but it must see one uniform internal contract.

Allowed distinction:

- execution adapter

Disallowed distinction:

- duplicated planning logic
- duplicated validation logic
- duplicated result interpretation logic


### 5. Tool discovery must be code-backed

Tool definitions must come from executable truth, not primarily from prose.

Preferred order of authority:

1. code or runtime introspection
2. explicit typed declarations
3. documentation

Documentation is guidance for humans and models.
It is not the primary source of tool schema.


### 6. Result structure must be preserved as long as possible

Tool results shall remain structured until the last possible moment.

The system should not eagerly flatten results into plain strings if typed structure is available.

The last-mile rendering into prompt text should happen only when needed for model consumption.

This preserves:

- provenance
- pagination
- typed fields
- source metadata
- error classification


### 7. Provenance is mandatory

Every tool result shall preserve provenance.

At minimum:

- tool name
- source service or module
- timestamp or execution context
- whether the result is complete, partial, cached, or failed


### 8. Side effects must be explicit

Every tool shall declare whether it is:

- read-only
- write-capable
- externally mutating

Write-capable tools shall be easy to identify in both code and logs.


### 9. Errors must be structured

Errors shall not be represented only as free text.

The internal contract shall distinguish:

- validation error
- transport error
- execution error
- timeout
- unavailable service
- permission or policy block

Human-readable text may be attached, but must not be the only signal.


### 10. Naming must be stable

Tool names are an API surface.

They shall:

- be globally unique in the agent runtime
- remain stable once published
- avoid transport-specific prefixes unless they convey real domain meaning

Service ownership belongs in metadata, not in unstable naming hacks.


## Required Arrangement

### Layer 1: Tool definition

Every tool must first exist as an internal `ToolSpec`.

`ToolSpec` should include:

- `name`
- `description`
- `input_schema`
- `source_type`
- `source_id`
- `mutability`
- `tags`


### Layer 2: Tool adapters

Adapters are responsible only for translation.

Required adapters:

- local Python adapter
- MCP adapter
- model tool-schema adapter

Adapters should not own planning rules.


### Layer 3: Tool execution

Execution takes a `ToolCall` and returns a `ToolResult`.

Execution concerns:

- argument validation
- dispatch
- timeout
- retries where appropriate
- error wrapping
- provenance


### Layer 4: Tool rendering

Rendering is separate from execution.

Rendering concerns:

- prompt formatting
- truncation
- scratchpad parking
- UI display
- logs

The same result may be rendered differently for:

- the LLM
- the UI
- logs
- persistence


## What Must Be Avoided

The system should avoid:

- deriving tool schema mainly from markdown prose
- converting rich remote results into strings too early
- using the model API schema as the internal tool model
- mixing planning logic with transport logic
- creating one-off service-specific tool gateways when MCP is suitable
- silently losing provenance or error type during conversion


## Preferred Future State

### Local tools

Local tools should be declared in code with explicit schema and metadata.

Documentation may still exist, but code should be authoritative.


### Remote tools

Remote tools should be exposed through MCP by default.

Their MCP schemas should be imported into the internal tool contract with minimal loss.


### Agent runtime

KoreAgent should operate over a single tool registry built from:

- local code-backed tools
- imported MCP tools
- built-in orchestration tools

That registry should then be rendered into the current model-facing schema.


## Compliance Tests

A good implementation should satisfy these checks:

1. Can the same orchestration code plan over local and remote tools without knowing which is which?
2. Can a tool be rendered to the model schema without losing its source metadata?
3. Can a tool result remain structured until prompt-render time?
4. Can the system swap model providers without redesigning tool internals?
5. Can a remote service expose tools without bespoke agent-specific glue beyond the MCP adapter?

If the answer to any of these is no, the design is drifting.


## Migration Direction

When changing the current implementation, prefer this order:

1. define an explicit internal `ToolSpec` / `ToolCall` / `ToolResult`
2. adapt local skill catalog output into that form
3. adapt MCP discovery output into that form
4. make orchestration consume only that form
5. move prompt rendering to a dedicated final adapter
6. reduce markdown-derived tool schema where code-backed schema is available


## Final Rule

Inside KoreAgent:

- MCP is the standard remote tool transport
- the internal tool contract is the system standard
- the model tool schema is only a presentation adapter

That separation should be preserved in all future work.
