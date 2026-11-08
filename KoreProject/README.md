# KoreProject v0.1

KoreProject is the suite's local requirements, design and work-tracking subsystem.
Start it through KoreStack, then select **Project** in the shared top bar or open
`http://127.0.0.1:29619/ui`. Configure its host, port and enablement under
`services.koreproject` in [suite configuration](../config/korestack_config.json).
See the [root command-line documentation](../README.md#command-line).

## First workflow

1. Choose **New** to create a project with a permanent short key, title and description.
   To open an existing project, select it in the dropdown and choose **Load**.
   Selection alone does not switch the loaded workspace. Load is disabled for the
   currently loaded project or an empty selection; Delete requires a selection
   and available ticket storage. All three actions are disabled while a request
   is in progress. Edit project and Refresh are in the workspace toolbar.
2. The workspace is a dashboard. Open **Requirements** or **Design** from its cards
   or the app bar. Organise named sections in each page's
   left tree and edit requirements in the selected section's spreadsheet-style
   table, or design statements in their separate tree and tables. Link design
   **satisfies** requirement.
3. Open **Tickets** to capture work or ideas. A ticket has one optional parent, with no
   separate epic/story/bug types. Children progress independently.
4. Create a board. Its swimlanes are Backlog, Planned, In Progress, In Review and Done.
   Backlog is the shared unassigned ticket queue; the other four lanes belong to the board.
   Edit/add lanes, their lifecycle categories, and numeric ordering.
5. In a ticket's details, choose **Board / lifecycle lane** to move it. An empty
   placement is the unassigned backlog. Numeric order puts lower values first;
   creation time and ID break ties. Movement works with keyboard or mouse; v0.1
   does not require drag-and-drop.
6. Link tickets **addresses** statements or **depends on** other tickets. Add
   comments and evidence references. Inspect snapshots in activity history.

The UI uses [KoreUI Common Elements](../KoreUI/UIElements/README.md) for its shell,
navigation, panels, controls, typography, spacing and theme. Local styling is
limited to the project workspace, ticket indentation and content layout.

## Project dashboard and work pages

`/ui?project=<id>` is a dashboard, not a statement editor. It shows:

- Requirements: total records, active sections, records linked to satisfying
  designs, and records without design coverage.
- Design: total records, active sections, designs linked to requirements, and
  designs without requirement links.
- Tickets and boards: total tickets, backlog, open/in-progress/done/cancelled
  counts, active boards and archived tickets. Lifecycle counts exclude archived
  tickets. Unavailable lane references are reported separately.

Coverage counts distinct endpoints of `satisfies` links, not relationship rows.
Each card has an Open action; the work card opens Tickets or Boards directly.
Unavailable ticket storage is explicitly reported, never shown as zero work.
Project New/Load/Delete, Edit project and Refresh remain available.

`/ui/tickets?project=<id>` provides the hierarchical ticket list.
`/ui/boards?project=<id>&board=<id>` is a dedicated full-width board page, without
the project-management fields or a side editor. Ticket and board/lane editing
opens in a KoreUI-styled dialog, leaving the board's width unchanged.
Drag active tickets between active lanes or back to the unassigned backlog;
the Move action also supports keyboard and touch users. Moves retain optimistic
version checks and require explicit confirmation before closing a parent with
unfinished children. Failed moves leave cards in their original lanes and display
the error. Each card's Comment action opens the ticket's comment form; comments
and history remain available in the ticket dialog.
Legacy ticket URLs with `view=boards` redirect to the board page.
`/ui/settings` retains the explicit ticket-store reset.
All pages share project navigation and preserve the selected project in links.
The browser remembers the last loaded project across visits to any KoreProject
page, including entry from KoreStack. Changing a dropdown alone does not change
the remembered project; use Load. An explicit `project` URL parameter takes
precedence (an empty value clears the selection). Missing projects or projects
pending deletion clear the remembered selection, page context and stale links.
Archived projects remain available read-only. A failed project-list request does
not erase the remembered project. This preference is local to the browser and
KoreProject origin; blocked browser storage is reported in the browser console.

## Requirements page

`/ui/requirements?project=<id>` is the dedicated requirement viewing/editing page.
The tree contains named, nested sections, not requirement-to-requirement parentage.
Each section owns its own table; selecting a parent does not implicitly include
its children's requirements. **All requirements** is a combined view and
**Unfiled** holds requirements without a section, including pre-existing records.

- **New** creates a section beneath the selected section (or at top level).
  **Edit** renames, reorders or reparents it. Cycles and cross-project parents
  are rejected. **Delete** permanently removes the selected section,
  all nested sections and their statements, including archived records, comments,
  history, links and cached creation responses. A confirmation warns that affected
  unsaved drafts are also discarded; unrelated drafts and records are retained.
  There is no archive or undo. Numbering counters are retained, so IDs are not reused.
  Deletion requires both stores to be available to remove ticket links. Interrupted
  deletion blocks project edits and resumes on retry or service restart; only
  temporary deletion intent is retained, not an archived copy.
- The table has exactly four columns: unique ID, title, requirement text and
  design links. The first cell contains only the requirement
  number (blank until a new row is saved), with no buttons, version or status text.
  Owner, state, section and archived are not editable fields on this page.
  New requirements belong to the selected table automatically; editing existing
  text preserves membership and other stored metadata. Legacy archive flags do
  not hide requirements in this editor. Rationale and acceptance criteria are no
  longer statement fields.
- The narrow fourth column shows only linked design references, a small minus
  button beside each entry, and a plus button to choose an additional design.
  There are no titles, relationship descriptions, notes or empty-state text.
  Plus creates an incoming design-satisfaction link; minus confirms removal.
  Link changes persist immediately without discarding unsaved requirement text.
  New requirements must be saved before linking designs. The requirement-text
  column expands into the remaining width. Each design reference and its minus
  button stay on one line; the column fits their content without wrapping IDs.
  Title and requirement text both use multiline controls that automatically grow
  to the same row height, including when links or wrapped text make a row taller.
  Linked IDs navigate to the design editor. Requirement-to-requirement links
  remain stored but are not displayed or searched in this table.
  Relationship management remains available through the API/native skills and
  design-side links.
- The single **Save** button above the table stores all drafts, stopping on an
  error (rows already saved remain saved). New requirements are drafts until
  Save assigns their IDs. Refresh asks before discarding unsaved rows or edits.
  Drafts survive switching sections. Conflicts preserve edits rather than
  overwriting another editor; use Refresh to explicitly discard and reload.
  Editing approved content still returns it to draft.
- Comments and revision history remain stored and accessible through the API;
  there are no per-row Details controls in the spreadsheet.
- The section tree supports arrow-key navigation; Tab traverses table controls.
  Wide tables scroll horizontally, with a fixed ID column and sticky headers.

Sections, membership, requirement links and revisions live in `projects.db`, so
they remain available without ticket storage and survive a ticket-store reset.
Section membership is independent of semantic refinement/satisfaction links.

## Design page

`/ui/design?project=<id>` uses the same spreadsheet implementation as Requirements,
with its own nested section tree. Its columns are **Unique ID**, **Title**,
**Design**, and **Requirement links**. The controls retain the single top Save,
equal-height autosizing text fields, and compact single-line reference links.
Plus links a requirement satisfied by the design; minus confirms removal.

Each section has an immutable `kind` (`requirement` by default, or `design`).
Parent/child sections and member statements must have matching kinds. Existing
requirement sections remain requirement-only, and existing designs start Unfiled.
Both trees, their memberships and history survive ticket resets. Old
`/ui?project=<id>&statement=<id>` links navigate to the correct dedicated editor.

## Independent data lifecycles

Both databases live in `<datacontrol>/koreproject/`:

| Database | Contents |
|---|---|
| `projects.db` | Projects, nested requirement sections, requirements/design, immutable edit snapshots, statement links and comments |
| `tickets.db` | Tickets, hierarchy, boards, lanes, work links, comments, operational activity and retry keys |

Definition changes and operational changes are separate transactions. There are no
foreign keys from project definitions into disposable work. Within-store structural
foreign keys are enabled; the service validates link endpoints and cross-store
project membership. IDs use UUIDs, with stable project-prefixed display references.
Project keys and statement kinds cannot change after creation.
Requirement, design and ticket references use independent per-project sequences,
starting at 1 without leading zeros: `GRID-REQ-1`, `GRID-DES-1`, `GRID-TKT-1`.
Ticket labels use these numbers (for example `GRID-TKT-101`), never UUIDs.
Activity displays resolve internal identities to readable references; original
snapshots remain unchanged in storage.
Numbers are allocated transactionally and are not reused after archival, retries,
restarts or ticket resets. Links and URLs continue using internal UUIDs.

**Delete** beside the project dropdown permanently removes the **selected** project
(which may differ from the loaded workspace). It requires typing `DELETE <KEY>` and
removes that project's statements/revisions, tickets, boards/lanes, comments, links,
activity and retry records from both databases. It is not archival and cannot be
undone; other projects are preserved. Archival remains available in record editing.
Deletion requires the current project version and readable stores. It is not
exposed as a native agent skill.

Because SQLite WAL does not provide an atomic transaction spanning both databases,
confirmed deletion first records durable intent, then removes operational data and
definitions. An interrupted deletion is marked pending and blocks further edits to
that project; retry Delete or restart the service to complete it after restoring
storage access. The definition schema upgrades automatically to version 7; the
ticket schema upgrades to version 3. Existing legacy statements receive numbered
display references in creation-time order (UUID breaks ties). Version 5 converts
legacy design references to `KEY-DES-N` while preserving already-numbered
requirements and their counters. Their UUIDs, links and
remaining content are retained. Rationale and acceptance criteria are removed
from current statement records, editors and API fields; historical revision
snapshots retain the original fields and references. Changed records receive a
new version and a migration history entry; cached creation responses are updated.
Version 6 assigns existing sections the requirement kind and adds independent
design sections, preserving references and memberships. Ticket schema version 2
numbers existing tickets in creation-time order per project (UUID breaks ties),
preserving their identities, hierarchy, lanes, links, comments and historical
snapshots. It increments record versions and updates cached creation responses.
Ticket schema version 3 updates boards with the original default lane set:
Inbox becomes Planned, Ready tickets move to Planned and Ready is removed,
In progress becomes In Progress, and Review becomes In Review. Ticket identities,
comments and history are retained; removed-lane discussion/history and creation
retries resolve to Planned. Other custom lanes and custom lane layouts are retained.
Reload open editors after upgrading.

**Data settings > Reset all ticket data** requires typing `RESET ALL TICKETS`.
This removes work for **all projects**, including boards/lanes, comments and work
links. It preserves project definitions, statement history and statement links.
Reset is transactional and serializes with mutations; it does not delete database
files beneath requests. A fresh generation prevents delayed old requests or retry
keys from recreating work. Ticket counters remain in the operational database
across resets, so ticket numbers are not recycled. Preserve that database and its
counters when restoring work; replacing it with a fresh database starts new sequences.

If ticket storage is missing or corrupt, it is reported as unavailable, not an
empty backlog. Definition screens remain usable and health returns HTTP 503.
The v0.1 reset screen operates on a readable ticket database. Recovery of a missing
or corrupt database is an explicit offline operation:

1. Stop KoreProject through the suite before touching its data.
2. Back up or quarantine the exact `tickets.db`, `tickets.db-wal` and
   `tickets.db-shm` files, if present. Never remove `projects.db`.
3. Restore a consistent ticket backup, or initialize a fresh ticket store with
   `ProjectStore(data_directory)._initialize_tickets()` from a repository Python
   environment after quarantining those ticket files.
4. Restart through KoreStack. Old ticket links may be unavailable; they are not
   reassigned by title.

For backup, stop KoreProject and copy both databases, including any associated WAL
files. Do not copy only a live SQLite main file. Built-in coordinated backup/export
and recovery tooling are deferred.

Run **one service process / one worker per data directory**. The service owns a
mutation lock for cross-store validation and reset; multiple independent writers
against the same directory are not supported. SQLite uses WAL and bounded waits.

## Concurrency and review

- Updates require `expected_version`; stale edits return HTTP 409, retaining the
  browser's unsaved form for manual reconciliation.
- Create operations accept `request_id`. Retrying the same payload returns the
  same record; reusing the key with other content returns 409.
- Every ticket/board/lane write and ticket-related link/comment requires the
  current ticket-store `generation`, obtained from reads or the generation route.
- Links reject wrong-kind and cross-project endpoints. Parentage, dependencies
  and refinement reject cycles. A parent needs explicit confirmation before
  moving into a terminal lane with unfinished direct children.
- Statements have draft, in_review, approved and retired states. Editing approved
  content creates a draft; previous snapshots remain durable. Native agent tools
  cannot approve statements. This is lightweight local review, not a formal
  approval/identity system or a separate published revision pointer.
- Board lane is the ticket's canonical lifecycle; there is no competing status.
  Occupied lanes cannot be archived or recategorised until active tickets move.

## HTTP and native agent interface

Interactive API documentation: `/docs`. Shared discovery: `/__endpoint_manifest`.

| Route | Purpose |
|---|---|
| `GET /status` | Health and ticket generation |
| `GET /api/records/{kind}` | Lists with `project_id`, title/body `q`, `offset`, `limit` (maximum 500) |
| `GET /api/records/{kind}/{id}` | Record, links, comments and latest 100 activity snapshots |
| `GET /api/statement-links` | Paginated durable requirement/design links for `project_id` |
| `POST /api/records/{kind}` | Create with `data`, `project_id`, optional `request_id`, ticket generation |
| `PUT /api/records/{kind}/{id}` | Partial data update with expected version |
| `POST /api/records/{kind}/{id}/comments` | Add a comment |
| `POST /api/links` | Add a validated relationship |
| `DELETE /api/links/{store_kind}/{id}` | Remove a relationship; preserve its activity |
| `GET /api/tickets/generation` | Read current operational-store generation |
| `POST /api/tickets/reset` | Explicit operator-only reset surface |
| `DELETE /api/projects/{id}` | Permanently delete one project with `expected_version` and `confirmation: "DELETE <KEY>"` |

`kind` is project, section, statement, ticket, board or lane. Sections have
`parent_id` and `position`; requirements have nullable `section_id`. Relationship `store_kind` is
statement for statement-to-statement links, otherwise ticket.

[skill_registration.json](skill_registration.json) registers `project_read`,
`project_save`, `project_link` and `project_comment` using the existing native JSON
skill-registration/invocation mechanism. They use the same validated storage
operations as the UI. No MCP server or model inference is introduced.
The shared registration worker refreshes registration while the service runs.

v0.1 records `operator` versus `agent` actors. It does not authenticate individual
users, authorize roles, or attach agent run/session identities. Keep the default
loopback bind; cross-origin browser writes are rejected, but that is not a
replacement for authentication. Reset is omitted from native agent tools, not
protected against arbitrary trusted local HTTP callers.

## v0.1 boundaries

Included: projects, linked requirement/design records, retained edit snapshots,
single-type hierarchical tickets, configurable boards and horizontal lifecycle
lanes, search, archive controls, comments, activity, conflict detection and isolated
ticket reset. Browser lists fetch API pages and filter locally; very large
workspaces will need incremental/virtualized rendering.

Deferred: formal baselines/sign-off, revision-pinned evidence and suspect-link
propagation, traceability reports, run-level agent attribution, bulk operations,
external tracker synchronization, sprint planning, Markdown import/export,
automated backup/recovery, and browsing history beyond the latest 100 snapshots.
All older snapshots are still retained in the database. Existing Markdown
requirements/design remain authoritative until explicitly adopted.

## Validation

From the repository root, using the suite environment:

```powershell
$env:KORESTACK_ROOT = (Get-Location).Path
& .\.venv\Scripts\python.exe -m unittest Data.datacontrol.koreunittest.tests.KoreProject.test_project -v
```

Tests use isolated temporary databases, not live operational data.
