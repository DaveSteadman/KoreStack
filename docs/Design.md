# KoreStack Design

## Purpose

This document describes how KoreStack is structured to meet the outcomes in
[Requirements.md](Requirements.md). It records the current architecture and the
target design constraints that guide changes across the suite.

## System context

KoreStack is a supervised group of local Python services. The `KoreStack` supervisor
starts services, resolves configuration, exposes the landing page, and monitors
service status. The suite is organised around independently deployable service
boundaries rather than importing one domain service into another as a library.

```text
                         KoreStack supervisor
                                 |
                  configuration, start, status, UI links
                                 |
  +---------------+-------------+--------------+--------------+
  |               |             |              |              |
KoreAgent      KoreChat      KoreData      KoreDocs/KoreCode  KoreComms
LLM/tools      conversations retrieval    user content        channels
  |
  +----- KoreAgentNetwork / KoreCron / KoreTest2 / KoreUnitTest
```

KoreLiveWeb is an optional egress boundary for web retrieval. It is deliberately
separate from local data services because it handles untrusted remote content.

## Ownership and boundaries

| Area | Owner | Responsibility | Must not own |
|---|---|---|---|
| Process lifecycle and service discovery | KoreStack | Resolve suite configuration, start, stop, probe, and present service state | Domain data or workflow decisions |
| Agent orchestration and model calls | KoreAgent | Sessions, tool selection, configured model use, work packets | Durable conversation ownership or domain-data implementation |
| Conversations | KoreChat | Durable threads, messages, and events | Model selection or external delivery |
| Data retrieval | KoreData | Gateway and specialist local data services | Agent orchestration |
| User content | KoreDocs and KoreCode | Documents, files, sheets, diagrams, and scoped code work | Cross-service scheduling or model ownership |
| External communication | KoreComms | Channel credentials, sending, receiving, and delivery state | General agent policy |
| External web evidence | KoreLiveWeb | Search, fetch, navigation, and provenance | Persistent local data ownership |
| Automation | KoreAgentNetwork and KoreCron | Persisted workflows and scheduled triggers | Bypassing central model, authority, or audit policy |
| Verification | KoreTest2 and KoreUnitTest | Live-interface and internal regression evidence | Production data ownership |

## Control plane, data plane, and execution plane

### Control plane

The supervisor resolves checked-in configuration, service enablement, ports, and the
data root. It polls service status and presents the operator with overall health.

Status must distinguish:

- **liveness**: the process can respond;
- **readiness**: its persistent state and dependencies are usable;
- **degraded**: a known optional or required dependency prevents part of its work; and
- **failed**: the service cannot perform its required role.

### Data plane

Persistent state is separated into:

- `<dataroot>/datauser/` for user-owned files and content; and
- `<dataroot>/datacontrol/` for service-owned databases, schedules, logs, caches,
  test state, and operational metadata.

Each service owns its schema, migrations, retention rules, and backup/restore
procedure. Services exchange data through documented HTTP, MCP, or native
skill-registration contracts; they do not depend on another service's private
database schema.

### Execution plane

KoreAgent runs the chat tool loop. KoreAgentNetwork runs composed workflow blocks and
KoreCron triggers scheduled work. KoreCode and Network Python blocks are trusted-local
execution capabilities. All execution paths must identify their initiating workflow
and apply resource limits, capability restrictions, and audit logging appropriate to
the operation.

## Local-first AI design

KoreAgent is the sole authority for the chat and System One model identities used by
the suite:

1. startup reads the configured model identities;
2. startup verifies that they are installed on the configured local Ollama host;
3. the models are preloaded and retained where resources permit;
4. the work-packet interface routes calls only to the configured resident chat or
   System One model; and
5. unconfigured per-request model overrides fail explicitly.

Other services submit prompts or typed decision questions to KoreAgent. They do not
call arbitrary models directly. Any future remote-model support must be an explicit
opt-in deployment mode with its own data-egress policy, status indicator, and tests.

## Trust and egress boundaries

The target default deployment is loopback-only and single-operator. The following
operations are privileged:

- code execution and debugging;
- state changes and destructive data operations;
- workflow execution and scheduled triggers;
- external communications;
- MCP tool invocation;
- web fetch, navigation, and search; and
- model and host configuration.

The target security design is:

1. authenticate and authorize privileged requests;
2. bind privileged services to loopback unless an explicit unsafe deployment mode is
   enabled;
3. use service-to-service credentials for internal calls;
4. protect browser writes against cross-site request forgery;
5. emit audit events for privileged and egress operations; and
6. enforce outbound URL policy before connection and after every redirect.

The outbound URL policy must resolve names and reject loopback, private, link-local,
multicast, and reserved addresses; set bounded redirect, response-size, and timeout
limits; and preserve source/provenance metadata for fetched content.

## Service contract design

Every service exposes:

- `/status` for structured liveness, readiness, dependency, and build/config identity;
- `/api/...` for versioned JSON APIs;
- `/ui` for browser interaction; and
- `/mcp` only when it intentionally exposes a tool surface.

Inter-service APIs require published schemas, stable error codes, pagination rules,
request limits, idempotency semantics, and timeouts. Contract changes must be
backward-compatible or versioned, with consumer tests added before release.

## Reliability and recovery

Services must fail explicitly rather than substitute empty or successful-looking
responses. The supervisor reports the dependency chain behind degraded states.

Persistent services define:

- schema migration and rollback ownership;
- backup and restoration procedure;
- retention and deletion behavior;
- locking/concurrency model;
- restart recovery behavior; and
- operator-facing repair steps.

## Delivery quality gates

The target delivery pipeline validates:

1. supported Python and operating-system versions;
2. unit tests and focused integration tests;
3. formatting, linting, and type checks;
4. API/contract compatibility tests;
5. dependency vulnerability and secret scanning; and
6. local-first and egress-boundary regression tests.

Dependencies must be reproducible from locked production and development inputs.

## Architecture Decision Records

Material choices must be recorded under `docs/adr/` before implementation. The initial
ADR set should cover:

- local-only versus remote deployment modes;
- service authentication and browser-write protection;
- model selection, residency, and context-budget policy;
- trust model and containment for code/network execution;
- outbound web and communications egress policy;
- persistent-store backup, restore, and migration ownership; and
- API versioning and compatibility policy.

## Current gaps and sequencing

The highest-priority design work is:

1. define the trusted-local security boundary and protect privileged endpoints;
2. add egress/SSRF controls to live web operations;
3. publish the service/API and data-lifecycle contracts;
4. make readiness and dependency health consistent across services; and
5. establish reproducible dependency and CI quality gates.

These are tracked as design obligations, not claims that the current implementation
already satisfies them.
