# Design-KoreDeviceGateway

Requirements and early design statement for `KoreDeviceGateway`, a top-level suite service for exposing configured real-world devices to agents and UI users.

## Purpose

`KoreDeviceGateway` is the device-facing sibling of `KoreDataGateway`.

Its role is to provide one stable suite entry point for:

- current device values
- device status and health
- device event and log history
- safe callable operations against configured hardware
- REST ingestion endpoints for device-originated values and logs

The gateway allows an agent to treat a configured device as a chat-adjacent operational surface:

- inspect the current state
- ask for recent history
- perform bounded control operations
- receive structured success, failure, and safety feedback

This is not a generic hardware abstraction library. It is a suite integration service.

## Core Statement

`KoreDeviceGateway` does not own devices. It owns the interface between the Kore suite and devices.

Its job is to normalise diverse device integrations into one consistent agent and UI contract.

## Position In The Suite

```text
Agent / User
    |
    v
KoreDeviceGateway
    |
    +-- configured device adapters
    |      |
    |      +-- local drivers
    |      +-- HTTP APIs
    |      +-- vendor SDKs
    |      +-- command wrappers
    |
    +-- device state cache
    +-- device event/log store
    +-- control policy / safety checks
```

At suite level:

- `KoreDataGateway` handles knowledge and stored content.
- `KoreDeviceGateway` handles live or semi-live external equipment.

`KoreDeviceGateway` may itself act as a gateway over child device services.

Those child services may expose REST endpoints that accept incoming values and log entries for further processing.

## Scope

`KoreDeviceGateway` should handle:

- single current values
- structured device status snapshots
- time-series or append-only logs
- callable functions on devices
- polling and refresh of device state
- pushed ingestion of values and logs over REST
- bounded command execution with audit trail

It should not initially handle:

- hard real-time control loops
- low-latency industrial control guarantees
- autonomous unsafe actuation
- direct free-form shell access to hardware hosts

## Device Model

Every configured device should be represented as a named object with:

- identity
- type
- connection details
- status model
- value set
- callable operations
- safety policy

Minimum logical shape:

```text
Device
  id
  name
  type
  adapter
  status
  values[]
  operations[]
  logs[]
  last_refresh_at
  refresh_policy
```

## Push Ingest Model

`KoreDeviceGateway` is not only a polling surface.

It should also support devices or companion processes pushing data into the suite.

Authoritative rule:

Values and logs may enter the system either by pull or by push.

Push ingest should support at least:

- single value submission
- batch value submission
- log/event submission
- structured status snapshot submission

Typical sources:

- device-side webhook sender
- local collector process
- vendor integration bridge
- child device service translating an upstream protocol into Kore REST

This means `KoreDeviceGateway` and child services may expose REST endpoints such as:

- `POST /devices/{id}/values`
- `POST /devices/{id}/logs`
- `POST /devices/{id}/status`
- `POST /devices/{id}/events`

## Value Types

The gateway should support at least these value classes:

1. Scalar values
   Examples: temperature, voltage, mode, on/off, current job, signal strength

2. Structured status values
   Examples: printer state, robot pose summary, battery pack summary, alarm state set

3. Stream/log values
   Examples: event logs, telemetry samples, warnings, device console output

4. Derived values
   Examples: stale/not stale, healthy/degraded, last seen age, threshold breaches

Pushed values should carry metadata where available:

- source timestamp
- receive timestamp
- source identity
- unit
- quality/confidence
- sequence number if the source has one

## Operation Types

Operations should be first-class named actions, not arbitrary text commands.

Examples:

- `power_on`
- `power_off`
- `reset`
- `home`
- `pause`
- `resume`
- `start_capture`
- `stop_capture`
- `set_mode`

Each operation should declare:

- operation name
- human description
- argument schema
- whether it is read-only or mutating
- whether confirmation is required
- whether the operation is disabled by policy

## Safety Model

Safety is a core requirement.

The gateway must distinguish:

- read operations
- harmless state-changing operations
- risky state-changing operations

The system should support policy such as:

- read-only device
- allow-listed operations only
- confirmation-required operations
- operator-only operations
- rate-limited operations
- maintenance-window-only operations

Every mutating operation should be auditable.

Minimum audit fields:

- device
- operation
- arguments
- requester
- timestamp
- result
- error detail if failed

## Adapter Model

`KoreDeviceGateway` should be built around adapters.

Each adapter is responsible for turning a concrete device protocol into the common gateway contract.

Examples of adapter classes:

- HTTP/REST adapter
- local SDK adapter
- serial adapter
- Modbus adapter
- MQTT-backed adapter
- command-wrapper adapter

The adapter contract should expose something close to:

- `refresh_state(device_config) -> state`
- `read_logs(device_config, since, limit) -> logs`
- `invoke(device_config, operation, args) -> result`
- `health(device_config) -> health summary`
- `ingest_values(device_config, payload) -> ingest result`
- `ingest_logs(device_config, payload) -> ingest result`

This makes push handling a first-class part of the device contract, not an afterthought.

## Child Services

`KoreDeviceGateway` should be able to sit above child services in the same way `KoreDataGateway` sits above KoreData child services.

Possible child-service split:

- `KoreDeviceValues`
- `KoreDeviceLogs`
- `KoreDeviceControl`
- `KoreDeviceNumber`
- protocol-specific child services such as `KoreModbus`, `KoreMQTT`, or `KoreRestDevice`

The key rule is that child services must still converge on one gateway contract.

## KoreDeviceNumber

`KoreDeviceNumber` is a candidate child service for one specific class of device data:

- a single named numeric value observed over time

Examples:

- `SignalA`
- `TempB`
- `PressureLine3`
- `MotorCurrentLeft`

Authoritative statement:

`KoreDeviceNumber` is not just a storage service for numeric samples. It is an interpretation service for named numeric time-series.

### Role

For each configured numeric signal, `KoreDeviceNumber` should:

- accept timestamped numeric samples
- store the sample history
- learn or be configured with normal ranges
- detect abnormal ranges and abnormal transitions
- detect repeating cycle behaviour where present
- estimate where the current value sits within a cycle
- estimate likely near-future movement
- emit a notice when something is operationally notable

### Input Model

Each numeric stream should at minimum have:

- signal name
- source device
- numeric value
- source timestamp
- receive timestamp

Illustrative logical shape:

```text
NumericSignalSample
  device_id
  signal_name
  value
  source_time
  receive_time
  unit
  quality
```

### Core Responsibilities

`KoreDeviceNumber` should maintain, per named signal:

- sample history
- short-window trend
- long-window baseline
- expected normal range
- abnormal range or threshold conditions
- cycle model where one exists
- current phase estimate within the cycle
- short-horizon prediction
- current notice state

### Pattern Detection

The service should be able to reason about:

- stable ranges
- rising and falling trends
- spikes
- drops
- oscillation
- repeating cycles
- drift away from historical normal

Not every signal will have a cycle.

The design should allow signals to fall into two broad classes:

- bounded / threshold-driven signals
- cyclical / repeating-pattern signals

### Normal And Abnormal

Normal and abnormal should come from one or more of:

- fixed configured thresholds
- learned rolling baseline
- learned cycle envelope
- rate-of-change limits

Abnormality should not be limited to raw value.

It should also support:

- abnormal slope
- abnormal timing within a cycle
- abnormal duration above or below range
- missing expected oscillation or turnover

### Cycle Model

Where a signal exhibits a repeatable pattern, the service should attempt to answer:

- what cycle it appears to be following
- where in that cycle the present sample sits
- what values are likely to follow next

This is not a requirement for perfect forecasting.

It is a requirement for useful operational approximation.

Examples of useful outputs:

- `near cycle peak`
- `descending from recent high`
- `earlier than expected rise`
- `stuck below expected mid-cycle value`

### Notice Model

`KoreDeviceNumber` should produce a compact notice object when there is anything worth surfacing.

A notice should be able to describe:

- whether the signal is normal, warning, or abnormal
- what is notable
- where the signal appears to be within its pattern
- what is likely to happen next in the near future

Illustrative shape:

```text
NumericSignalNotice
  signal_name
  severity
  headline
  summary
  current_value
  expected_range
  cycle_position
  predicted_direction
  predicted_near_value
  generated_at
```

### REST Ingest Pattern

`KoreDeviceNumber` should expose REST endpoints that consume pushed numeric values.

Examples:

- `POST /numbers/{signal}/value`
- `POST /numbers/{signal}/batch`
- `GET /numbers/{signal}`
- `GET /numbers/{signal}/history`
- `GET /numbers/{signal}/notice`

The ingest path should be simple enough for low-complexity device-side senders.

### Agent Value

For an agent, the important outputs are not raw samples alone.

The service should help answer:

- is this number normal
- what changed
- does this look concerning
- where are we in the current pattern
- what is likely next

That is the reason for making `KoreDeviceNumber` its own service concept rather than leaving all numeric signals as unprocessed logs.

## Configuration Model

Devices should be declared in config before startup.

This follows the suite direction that mutable runtime topology should not be edited ad hoc in the UI.

However, ingest endpoints may be:

- fully preconfigured in static config
- partially generated on startup
- registered dynamically at runtime under a controlled adapter or child-service model

Minimum config concepts:

- device id
- display name
- adapter type
- connection block
- polling / refresh settings
- enabled operations
- safety policy

Illustrative shape:

```yaml
devices:
  printer_lab_1:
    name: Lab Printer 1
    adapter: http_rest
    connection:
      base_url: http://127.0.0.1:9721
      auth_token_env: PRINTER_LAB_1_TOKEN
    refresh:
      mode: poll
      interval_sec: 10
    policy:
      read_only: false
      confirm_operations:
        - reset
        - power_off
    operations:
      allow:
        - pause
        - resume
        - reset
```

## State Refresh

The gateway should support:

- on-demand refresh
- periodic polling
- adapter-pushed updates where available

Authoritative rule:

Current state should be cached, timestamped, and explicitly marked with freshness.

Agents and UI must be able to tell:

- when the device was last refreshed
- whether the value is current enough for operational use

For pushed data, the system should distinguish:

- source event time
- ingest receive time
- last successful poll time, if polling also exists

## API Shape

The external interface should be simple and explicit.

Early API families:

- `GET /devices`
- `GET /devices/{id}`
- `GET /devices/{id}/values`
- `GET /devices/{id}/logs`
- `POST /devices/{id}/refresh`
- `POST /devices/{id}/operations/{op}`
- `POST /devices/{id}/values`
- `POST /devices/{id}/logs`
- `POST /devices/{id}/status`

Agent-oriented responses should be structured and boring:

- no hidden device-specific surprises
- consistent success/error envelope
- operation results returned as typed data plus human summary

## Agent Tool Surface

`KoreDeviceGateway` should expose a small stable set of tools rather than one tool per vendor quirk.

Candidate tool surface:

- `list_devices`
- `get_device_status`
- `get_device_values`
- `get_device_logs`
- `refresh_device`
- `invoke_device_operation`

That gives one consistent tool contract while letting adapters vary underneath.

## UI Role

The UI should be comparable in spirit to `KoreDataGateway`, but operational rather than content-centric.

Minimum UI capabilities:

- list all configured devices
- show current health/status
- inspect values
- inspect recent logs/events
- invoke allowed operations
- show operation audit history

The UI should not invent alternate device semantics. It should reflect the same contract the agent sees.

## Persistence

The gateway should persist:

- device metadata cache
- recent state snapshots
- recent logs/events if useful
- operation audit history
- ingest audit and validation failures

It should not become the long-term system of record for vendor data unless explicitly designed for that.

Default expectation:

- short and medium-term operational history is stored locally
- very large raw telemetry remains the adapter or upstream system's responsibility unless promoted into a dedicated storage design later

Pushed ingestion should be validated before persistence:

- reject malformed payloads
- record validation failures
- preserve original event time where supplied
- avoid silent coercion of ambiguous values

## Relationship To Other Services

- `KoreAgent` uses `KoreDeviceGateway` as a tool and status/control surface.
- `KoreChat` may hold the conversational history around device interactions.
- `KoreComms` may later use device events as message sources.
- `KoreDataGateway` remains separate and should not absorb live device control concerns.

## Early Non-Goals

- general SCADA replacement
- arbitrary remote-code execution on device hosts
- automatic agent-issued unsafe actuation
- high-frequency telemetry warehousing
- vendor-specific UI duplication inside the gateway

## Recommended First Cut

Phase 1 should be deliberately small:

1. Define the config schema for devices.
2. Implement one adapter type, likely HTTP/REST.
3. Support:
   - list devices
   - get status
   - get values
   - push value ingest
   - push log ingest
   - invoke allow-listed operation
   - operation audit log
4. Build a simple UI list/detail page.
5. Integrate the tool surface into `KoreAgent`.

## Design Direction

The long-term success condition is consistency, not breadth.

If ten device types are added later, an agent should still see:

- the same device listing model
- the same status/value concepts
- the same operation invocation pattern
- the same audit and safety behavior

That consistency is the main reason for `KoreDeviceGateway` to exist as its own top-level service.
