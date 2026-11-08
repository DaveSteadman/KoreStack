# KoreStack Requirements

## Purpose

KoreStack is a local-first AI workspace. It lets a trusted operator run AI-assisted
workflows across conversations, documents, code, data, automation, communications,
and tests as one coherent system.

This document states the outcomes the system must provide. It deliberately does not
prescribe implementation mechanisms; those belong in [Design.md](Design.md).

## Product principles

1. **User value first.** Each subsystem must contribute a clear capability to the
   user's workflow rather than exist as an isolated technical component.
2. **Local-first by default.** User data, model inference, state, and execution stay
   on the operator's machine unless the operator explicitly enables an external
   integration for a specific purpose.
3. **Explicit authority.** Actions that execute code, change state, access external
   systems, or transmit data must be attributable, constrained, and observable.
4. **Reliable composition.** A subsystem failure must be detectable and must not
   silently turn into a false-success result in another subsystem.
5. **Recoverable ownership.** Operators must be able to understand, back up, restore,
   and remove the data and configuration they own.

## User and product outcomes

### R1: Integrated local workspace

The system shall provide one supervised workspace for conversations, agent workflows,
data, documents, code, communications, networks, schedules, and tests. The landing
page shall identify each service and whether it is ready for its intended work.

### R2: Local model operation

The system shall use the configured local Ollama chat and System One models for
agent-driven inference. Model selection shall be centrally configured, visible to the
operator, and testable. A request from a subsystem shall not silently select an
unconfigured model.

External model endpoints, if supported in the future, shall require an explicit
operator configuration and shall be visibly distinguished from local model operation.

### R3: Data residency and egress control

The system shall classify data handled by each subsystem and document where it is
stored, retained, transmitted, and deleted. External egress, including web retrieval,
communications, MCP integrations, and remote model calls, shall be explicit,
operator-controlled, and attributable to the initiating workflow.

The system shall offer a useful offline/degraded mode: local content remains available
when optional external integrations are unreachable, and unavailable capabilities
report a clear reason.

### R4: Safe trusted-local execution

The system shall treat code execution, workflow networks, external communications,
and state-changing APIs as privileged capabilities. It shall document the trusted-user
threat model and prevent accidental exposure of these capabilities beyond the local
operator boundary by default.

### R5: Stable service behavior

Every service shall publish a compatible health/readiness signal and a documented API
contract. Callers shall receive structured errors that distinguish invalid input,
unavailable dependencies, authorization failures, conflicts, and internal errors.

Mutating operations shall have documented idempotency, timeout, retry, and failure
semantics. APIs used between services shall have stable schemas and compatibility
rules.

### R6: Durable, inspectable user state

User-owned content and service-owned operational state shall be separated. The system
shall document backup, restore, retention, migration, and deletion behavior for both.
Destructive actions shall be explicit and auditable.

### R7: Operable and observable services

The supervisor and service UIs shall surface liveness, readiness, dependency state,
configuration identity, and actionable failure information. Logs and audit events
shall allow an operator to determine which workflow, user action, model, tool, or
integration caused a material operation.

### R8: Maintainable delivery

The repository shall have a supported runtime matrix, reproducible dependency
installation, and automated checks for tests, formatting/linting, typing, dependency
vulnerabilities, and secrets. Changes to public contracts, data formats, and trust
boundaries shall include focused regression coverage.

## Acceptance criteria

The following criteria turn these requirements into reviewable project outcomes:

| Requirement | Evidence of satisfaction |
|---|---|
| R1 | Service inventory, ownership, and readiness states are available from the supervisor. |
| R2 | A test proves all agent and work-packet inference uses configured local models; an attempted unconfigured override fails explicitly. |
| R3 | An egress inventory identifies every external destination and its data classes; outbound operations have documented approval and logging behavior. |
| R4 | Default network exposure, authentication, and execution authority are documented and regression-tested. |
| R5 | Each inter-service API has a versioned schema and documented errors, retry, and idempotency behavior. |
| R6 | Backup, restore, retention, and deletion procedures are documented and exercised for persistent stores. |
| R7 | Service status distinguishes liveness from readiness and identifies failed dependencies without exposing sensitive data. |
| R8 | CI validates the supported matrix and blocks regressions in quality, dependency, and secret checks. |

## Scope and non-goals

KoreStack is designed for a trusted local operator. It is not, without a separately
approved deployment design, a multi-tenant internet service or a substitute for a
network perimeter. Adding remote access, shared tenancy, or third-party inference
changes the trust boundary and requires an Architecture Decision Record and updates to
this document.

## Requirement governance

Changes to these requirements require:

1. a user or operational outcome that motivates the change;
2. an update to [Design.md](Design.md) when implementation architecture changes; and
3. acceptance tests or observable evidence for the affected requirement.
