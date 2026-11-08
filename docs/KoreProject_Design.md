# KoreProject Subsystem Design

Status: v0.1 implemented as a bounded first slice. The sections below remain the
target design; they are not a claim that every proposed capability is implemented.
See [KoreProject v0.1](../KoreProject/README.md) for the actual interface, storage,
operational safeguards and deferred capabilities.

v0.1 includes separate definition/work databases, linked requirements and design,
edit snapshots, hierarchical single-type tickets, configurable boards/lifecycle
lanes, comments, version conflicts, explicit ticket reset and native JSON skills.
Its WebUI uses KoreUI Common Elements from the outset. Formal baselines,
revision-pinned evidence, suspect-link propagation, traceability reports,
import/export and automated backup/recovery remain later work.

Related foundations: [Requirements](Requirements.md) and [Design](Design.md).

## 1. Purpose and scope

KoreProject provides a lightweight, local workspace for maintaining requirements,
design statements, and the work needed to deliver them. The WebUI combines linked
statement management with hierarchical tickets and lifecycle boards: a small,
integrated toolset managing the state and future workload to progress a project.

The data is both User and AI oriented, so an agent can measure system performance 
against design and requirements, as well as implement upcoming tickets. Poor 
performance or design weaknesses can be recorded in tickets fur future assessment.

The distinction between intent and implementation is fundamental:

- **Requirements statements** describe the problem, user value, and required outcomes.
- **Design statements** describe how those requirements will be satisfied.
- **Tickets** capture ideas, questions, defects, and implementation work using one
  ticket type. Their content, hierarchy, labels, and board placement organise the
  work; there are no separate epic, story, bug, or task types.
- **Links** connect these records so people and agents can understand why work exists,
  what it affects, and what remains unresolved.

An idea can be recorded immediately as a ticket without first creating requirements
or design statements.

## 2. Core concepts

### Requirement organisation and editing

The project workspace is a dashboard with requirement/design counts, section
counts, design coverage, ticket lifecycle counts and board counts. Cards open the
dedicated Requirements, Design, Tickets or Boards pages. Unavailable work storage
is reported explicitly rather than represented as empty work.

Requirements and Design have dedicated pages sharing the spreadsheet editor.
Each has an independent left-hand tree of named, nested sections; each section owns
a table. Sections have an immutable requirement/design kind, with matching parent
and member kinds enforced. These organisational sections are not statements and are
independent of semantic refinement links. Requirements without a section remain
visible in Unfiled; All requirements provides a combined view.
Delete section is permanent and recursive on both pages: it removes nested
sections, their statements, comments, history, cached creation responses and links
from both stores. Unrelated records and numbering counters remain. The UI confirms
the deletion and discards only affected drafts after success. Both stores must be
available; temporary deletion intent allows interrupted cleanup to resume while
project edits are blocked. No archived copy or undo state is retained.

The main pane is a spreadsheet-style editor, one requirement per row. The stable
unique ID is first, title second, requirement text third and design links fourth.
These are the only four columns; requirement-to-requirement links remain stored
but are not displayed in this table. The ID cell
contains only the requirement number: no actions, version or status text. Owner,
state, section and archive controls are absent. Section membership comes from the
selected table rather than an editable requirement field. Requirement
references use the project prefix and a per-project integer starting at 1, without
padding: `GRID-REQ-1`. Internal UUIDs remain the relationship identity. A single
Save button above the table saves new and edited rows across sections; new rows
receive their references on saving. The compact design-links column shows only
design references (`GRID-DES-12324`) and small minus/plus controls for removing/adding
links. Each reference and its remove button stay on one line, leaving the remaining
width for requirement text. Both the title and requirement controls autosize to the
full row height, driven by text or links. Descriptions, relationship text and notes
are not shown in these cells. Link changes persist independently of text drafts.
Save operations retain revisions and
reject stale versions. Sections and membership are durable project definitions,
not disposable ticket data.

The Design page mirrors this layout with Design text and Requirement links.
Tickets and Boards have separate pages. The full-width board page has draggable
ticket cards and lane drop targets; a Move action provides a keyboard/touch
alternative. Ticket and board/lane details open in shared-styled dialogs rather
than shrinking the board. A card's Comment action opens its discussion form.
Moves preserve version/generation checks and explicit parent-close confirmation.
Data settings remain a separate page.

### Projects

A project groups statements, tickets, boards, and their history. A project may
describe KoreStack itself, one subsystem, or another user-managed body of work.

Each project has a stable identifier, short display key, name, description, and
archive state. Display references such as `KSTACK-REQ-12`, `KSTACK-DES-8`, and
`KSTACK-TKT-42` remain stable when record titles change. Ticket references use an
independent per-project integer sequence starting at 1, without leading zeros.
The UI shows these references, not internal UUIDs, including parent selections,
links and activity. UUIDs remain the identity used by API calls and relationships.

### Requirements and design statements

Statements are individually addressable records, not just paragraphs inside a
document. A shared statement structure has a kind of `requirement` or `design`.

Common fields:

- stable ID and display reference;
- project, kind, title, and Markdown statement body;
- owner, labels, and display order;
- lifecycle state and revision number;
- creator, creation time, last editor, and last update time.

Rationale and acceptance criteria are not separate statement fields. Older
revision snapshots retain these fields for historical reference.

Requirements state a need of system performance. Design statements can describe
a component, interface, data model, algorithm, or implementation decision, with links
to the requirements they address.

A "Commentary" note is an additional item that we can create and link to any item for 
additional explanation. When an incident happens, extra rational may exist behind a piece 
of design or requirement, and this exists to record that for future readers.

Proposed statement lifecycle: **Draft -> In review -> Approved -> Retired**.
Approval applies to a particular revision. Editing an approved statement creates a
new draft revision without silently replacing the approved revision.

Initial navigation is an ordered, filterable statement table. Section headings and
document-style views can be added without making document position the identity of
a statement.

### Links and traceability

Links are explicit, typed relationships between stable record IDs:

| Relationship | Source | Target | Meaning |
|---|---|---|---|
| Satisfies | Design statement | Requirement | The design intends to satisfy the requirement |
| Refines | Statement | Statement of the same kind | Adds detail to a broader statement |
| Addresses | Ticket | Requirement or design statement | Work contributes to or changes this statement |
| Depends on | Ticket | Ticket | Work relies on another ticket |
| Relates to | Statement or ticket | Statement or ticket | Useful context without implying satisfaction or dependency |

Ticket parentage is a separate structural relationship, not another spelling of
dependency. A parent ticket groups work; it does not automatically block its children.

Links have their own identity, optional explanatory note, creator, and timestamp.
Both incoming and outgoing links are visible. Duplicate links, self-links, and cycles
in refinement or dependency relationships are rejected.

The first release keeps links within a project. Cross-project traceability is a
possible extension rather than a prerequisite for useful local work.

**A link is not evidence that a requirement has been met.** Likewise, closing a
ticket does not approve a statement or establish compliance.

## 3. Hierarchical tickets: one type

A ticket has:

- stable ID and display reference;
- project, title, Markdown description, and optional acceptance notes;
- optional parent ticket and ordered children;
- owner, priority, labels, and creation/update metadata;
- optional board/lane placement;
- comments, revision history, and links to statements and other tickets;
- references to supporting documents, code changes, and test evidence.

There is no ticket-type field. A large ticket and its smallest child use the same
schema and editor. An idea is simply a ticket whose description captures the idea.

Hierarchy rules:

1. A ticket has at most one parent, in the same project.
2. Self-parenting and cycles are rejected.
3. Reparenting preserves ticket identity, discussion, links, and board placement.
4. Parent and child tickets can progress independently.
5. Child completion counts are informative; they do not automatically close parents.
6. Completing a parent with unfinished children requires an explicit confirmation.
7. Archiving a parent must not silently archive or hide its active children.

The UI provides a collapsible ticket tree and a flat searchable list. Tickets can
exist without a board, providing an unassigned backlog for quick idea capture.

## 4. Boards and lifecycle swimlanes

A board belongs to a project and contains an ordered set of named lifecycle
swimlanes. In this subsystem, "swimlane" means a lifecycle bucket, not an additional
assignee grouping layered over a separate status system.

Default swimlanes:

**Backlog -> Planned -> In Progress -> In Review -> Done**

Backlog is the shared unassigned ticket queue, not a stored lane belonging to a
particular board. Planned replaces Inbox; the former Ready lane is removed and
its tickets move to Planned when migrating the original default lane layout.

Users can rename, reorder, and add lanes, including Deferred or Cancelled. Each lane
has a category of `open`, `active`, or `closed` for progress summaries; a closed lane
also records whether its outcome means completed or cancelled.

Proposed initial presentation: horizontal swimlanes with ticket cards within each
lane. Display orientation is a UI choice; board and lifecycle semantics do not depend
on it.

### Placement and movement

- Each ticket has at most one owning board placement and one lane on that board.
- The lane is the ticket's lifecycle location; there is no independently editable
  status field that can contradict it.
- Tickets outside a board are explicitly unassigned, not implicitly completed.
- Cards have a saved order within a lane.
- Moving a card changes its lane and records an activity event.
- Moving between boards requires an explicit destination lane.
- A ticket's parent does not force its children onto the same board or lane.
- Additional filtered views may show a ticket without creating another lifecycle.

Board and lane operations must preserve references. Non-empty lanes cannot be deleted
without moving their tickets. Archiving a board must offer reassignment or show clearly
that its tickets remain on an archived board; it must not silently close them.

Drag-and-drop is a convenience, not the only control. A keyboard-accessible move action
offers the same operation. Failed saves restore the prior card placement and show an
error rather than presenting an unsaved move as successful.

## 5. WebUI

KoreProject **must use KoreUI and its shared Common Elements from the first UI
implementation**. Follow the [KoreUI conventions](../KoreUI/README.md) and reuse
[UIElements](../KoreUI/UIElements/README.md), rather than introducing a separate
visual theme or component library. The lightweight requirements/wiki/ticket toolset
describes functionality, not permission to adopt Atlassian styling.

This is a design constraint, not an optional later cleanup:

- Reuse the shared shell, navigation, typography, icons, colours, spacing tokens,
  panels, tables, buttons, form controls, dialogs, and feedback patterns wherever
  corresponding Common Elements exist.
- Reference shared assets directly; do not copy them into a subsystem-local theme
  or override them to create a different visual identity.
- Keep subsystem templates and browser assets under `KoreUI/KoreProject/`.
- Limit subsystem-specific CSS to genuinely new project layouts, such as ticket
  hierarchies and board swimlanes. Compose these from shared elements and tokens
  instead of restyling standard controls or hard-coding substitute theme values.
- Check the existing Common Elements before adding a component. If a missing
  component is reusable across services, extend UIElements rather than creating
  a competing local implementation.
- Document any necessary deviation and its reason before implementation.

| View | Main capabilities |
|---|---|
| Project overview | Project description, boards, active work, and traceability gaps |
| Statements | Requirement/design filters, ordered tables, revision and approval state |
| Statement detail | Statement editor, history, incoming/outgoing links |
| Tickets | Hierarchical tree, flat list, quick capture, search and filters |
| Ticket detail | Description, parent/children, comments, placement, links, evidence references |
| Board | Lifecycle swimlanes, ordered cards, movement and board configuration |
| Traceability | Requirement-to-design-to-ticket matrix with missing-link indicators |

Record detail pages have stable URLs for sharing and agent citations. A side panel
may provide quick editing without losing board or list context.

Editors clearly indicate unsaved changes, save failures, and revision conflicts.
Markdown is safely rendered; imported or agent-authored text must not execute browser
scripts. Changes to lifecycle placement must not alter unrelated statement approvals.

## 6. Persistence and service boundary

Proposed implementation:

- a Python HTTP service named `koreproject`, supervised by KoreStack;
- SQLite persistence under `<dataroot>/datacontrol/koreproject/`;
- service code under `KoreProject/`;
- templates and browser assets under `KoreUI/KoreProject/`;
- suite configuration for enablement and port selection; no port assigned by this draft.

### Two databases with different lifecycles

Project definitions and ticket work **must be stored in separate SQLite databases**.
Requirements and design are the enduring definition of a project; tickets are a
transitory, continually accumulating record of work against that definition.

"Fixed" means the project definition is independent of ticket churn, not that an
approved requirement or design can never be deliberately revised.

| Database | Contents | Lifecycle |
|---|---|---|
| `projects.db` | Project identities, requirements, designs, statement revisions, statement-to-statement links, and their supporting records | Durable; retained and backed up as the authoritative project definition |
| `tickets.db` | Tickets, hierarchy, boards, lanes, placements, ticket links, discussion, work references, and ticket activity | Operational; independently archivable, replaceable, and explicitly disposable |

These are subsystem-level stores containing project-scoped records. A requirement
or design statement must never exist only inside the disposable ticket database.

Conceptual table ownership:

| Store | Tables | Responsibility |
|---|---|---|
| Project | `projects` | Stable project identity and metadata |
| Project | `statements`, `statement_revisions` | Requirement/design content, revisions, and approval history |
| Project | `statement_links` | Design-to-requirement links and statement refinement/relatedness |
| Project | `statement_comments`, `statement_references`, `project_activity` | Durable discussion, supporting references, and definition change history |
| Ticket | `tickets` | Project reference, content, parentage, priority, owner, and version |
| Ticket | `boards`, `lanes`, `ticket_placements` | Lifecycle organisation and card order |
| Ticket | `ticket_links`, `ticket_statement_links` | Dependencies, related tickets, and references to durable statements |
| Ticket | `ticket_comments`, `ticket_references`, `ticket_activity` | Operational discussion, evidence references, and work history |

### Cross-database references

Tickets reference stable project and statement IDs from the project database.
Ticket-to-statement links live in the ticket database; a statement's incoming ticket
links are queried from that store rather than copied into its durable definition.
Deleting ticket history therefore leaves no ticket foreign keys to repair in the
project database.

SQLite foreign keys enforce relationships within each database. The service validates
cross-database references, project membership, relationship kinds, and cycles. There
is no cross-database foreign-key or atomic-commit guarantee. Definition edits and
ticket edits are separate operations; each commits its own activity event in the
same database transaction.

Projects and statements support archival for recoverable removal. The project
picker also provides an explicitly confirmed **Delete** operation that permanently
removes the selected project and its statements, tickets, boards, links and history
from both stores, without affecting other projects. This is separate from resetting
the disposable ticket database. Durable deletion intent allows interrupted
cross-store deletion to resume; projects pending deletion reject further edits.
When older
ticket backups reference missing projects or statement revisions, the UI explicitly
reports unavailable references; it never silently remaps them by name.

Ticket numbers must not be reused after a reset; their counters remain in the
operational database. Ticket-store generation identity guards writes and retry
keys, while URLs and relationships retain immutable UUIDs. Restoring a ticket
backup must preserve its counters; a fresh replacement database starts new sequences.

### Disposal, retention, and recovery

- Resetting ticket data must leave project identities, statements, revisions, and
  statement-to-statement links untouched.
- A ticket-store reset discards tickets, boards, lane configuration, comments,
  ticket links, and operational history. The UI lists this scope and requires explicit
  confirmation; the operator may export or back up the ticket store first.
- Disposal is an intentional operator action, not an automatic retention policy.
  Routine ticket operations still use archival where appropriate.
- The project-definition UI remains usable without the ticket database. Unavailable
  or corrupt ticket storage is reported explicitly, not mistaken for an empty backlog.
- An intentionally reset ticket store can be initialised independently, without
  rebuilding or importing the project definitions.
- Ticket evidence is disposable unless explicitly promoted to a durable statement
  reference or review record. Approved conclusions must not rely solely on ticket
  comments that can later be discarded.

Writes include an expected revision/version. Conflicting edits return an explicit
conflict response, allowing a person or agent to refresh instead of overwriting newer
work. Create operations support idempotency keys so retries do not duplicate tickets.

Use independent schema migrations, bounded SQLite lock waits, and short transactions
for each database. Backups must use consistent SQLite snapshots rather than copying
only live database files while WAL writes are outstanding. A coordinated backup of
both stores pauses mutations while both snapshots are taken; independently restored
stores require reference validation. Ticket reset must first quiesce ticket writes
and close its database handles, not delete files underneath active requests.

## 7. API and agent integration

The UI and agent use the same service operations and validation rules. Proposed API
families cover projects, statements/revisions, tickets, boards/lanes, links, comments,
references, and activity. Lists support pagination and bounded filters.

KoreProject exposes agent operations through the suite's native JSON skill-registration
and invocation mechanism, not a new MCP integration path. Useful operations include:

- search and retrieve statements, tickets, and their linked context;
- capture an idea or create a child ticket;
- propose a statement revision and link it to relevant work;
- update ticket content or board placement with an expected version;
- add investigation notes and evidence references;
- report unlinked requirements, unresolved dependencies, or stale review links.

KoreProject does not own model inference or run arbitrary code. Model-assisted work
uses KoreAgent; existing test services own test execution.

Agent-originated changes record the initiating run/session and actor. Initial agent
permissions allow drafts and ordinary ticket work, but not approval of requirements
or designs. Work with implementation evidence can be submitted to Review without the
agent silently declaring its own work accepted.

Evidence references should identify the tested code revision and applicable statement
revision. A new approved statement revision flags affected design/work links for
review; prior test results are not silently treated as evidence for the changed intent.

## 8. Relationship to Markdown documentation

Existing requirements and design files remain authoritative until explicitly adopted
into KoreProject. Initial import is a reviewed operation that preserves provenance;
the subsystem must not automatically split prose into approved requirements.

After adoption, an approved statement set can be exported as readable Markdown,
including stable references and revision information. Exports are identified as
generated views, not a second independently editable source of truth.

## 9. Initial implementation sequence

1. Separate project-definition and ticket stores with tested linking, revision, and
   independent-reset invariants.
2. WebUI for statement editing, ticket capture, hierarchy, and linked detail views.
3. Boards, lifecycle swimlanes, ordering, movement, and conflict handling.
4. Native agent operations and attributable activity history.
5. Traceability views, evidence references, and reviewed Markdown import/export.

Use KoreStack's own requirements and improvement backlog as the first working example.
Do not require every existing subsystem to migrate its documentation before the first
project can be useful.

## 10. Acceptance scenarios for the first release

- Verify that every page uses the shared KoreUI shell and Common Elements, including
  shared spacing and theme tokens. Board and ticket layouts must not introduce a
  separate stylesheet theme or duplicate existing standard components.
- Capture an unassigned idea ticket without creating a statement or choosing a type.
- Create parent and child tickets; reject a hierarchy cycle without partial writes.
- Link a design statement to a requirement and tickets to both; inspect links in either
  direction.
- Move tickets between lanes and reload the page to confirm persisted placement/order.
- Move a ticket across boards without duplicating it or leaving contradictory status.
- Reject stale edits from another browser or agent without losing either user's work.
- Retain the approved statement revision while a proposed revision is under review.
- Show missing traceability separately from failed or absent verification evidence.
- Record an agent's investigation and evidence without granting it statement approval.
- Restore coordinated database backups with record identities, hierarchy, board
  placement, and history intact.
- Reset the ticket database and verify that project definitions, statement links,
  and approved revisions are unchanged and usable.
- Create new tickets after reset without reusing old ticket identities; explicitly
  report missing references when restoring an incompatible older ticket backup.

## 11. Deferred scope

The initial subsystem does not need multiple ticket types, sprint planning, time
tracking, billing, resource allocation, external issue-tracker synchronisation,
arbitrary workflow scripting, or a full diagram editor.

Cross-project relationships, advanced permissions, formal baselines, and richer
verification records can follow demonstrated need. The first release should deliver
the complete statement-to-design-to-ticket workflow before adding planning machinery.
