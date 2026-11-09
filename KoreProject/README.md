# KoreProject

KoreProject is the suite's local ticket-tracking subsystem. It manages projects,
hierarchical tickets, boards, lifecycle lanes, ticket links, comments and history.

Start it through KoreStack, then open `http://127.0.0.1:29619/ui/tickets`.

## Workflow

1. Create or load a project.
2. Add tickets, optionally nesting a ticket under another ticket.
3. Use a board to place tickets in lifecycle lanes, or leave them in Backlog.
4. Link tickets with `depends_on` or `relates_to`; add comments and evidence
   references from a ticket's details panel.

`/ui`, `/ui/requirements`, and `/ui/design` redirect to `/ui/tickets`.
Requirements and design editing have been removed. Existing legacy definition
data is left untouched in storage and is no longer exposed through the UI or API.

## Data and API

`tickets.db` holds tickets, boards, lanes, links, comments and operational
activity. Ticket, board, lane, link and comment writes require the current
generation returned by `/api/tickets/generation`.

Available record kinds are `project`, `ticket`, `board`, and `lane`. The native
agent tools expose the same ticket-only model.
