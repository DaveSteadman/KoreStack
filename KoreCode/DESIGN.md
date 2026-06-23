# KoreCode Design Document

> Status: Draft
> Date: 2026-05-01

---

## 1. Vision

KoreCode is a local-first code editor for the KoreStack workspace.

It is intentionally narrow in scope:

- edit Python and text files inside KoreStack
- provide a familiar code-editor workflow with file tree, tabs, and syntax highlighting
- expose code-aware LLM assistance for analysis, autocomplete, and targeted review
- avoid general IDE scope such as Git tooling, plugin ecosystems, language marketplaces, debuggers, and multi-language project management

The baseline product is a polished built-in Notepad for KoreStack code. The differentiator is the code-analysis workflow layered on top of that editor.

---

## 2. Product Boundaries

KoreCode is not trying to replace VS Code as a general development environment.

Phase 1 excludes:

- Git integration
- extension or plugin installation
- non-Python language tooling beyond plain-text editing
- project generators
- terminal orchestration beyond minimal optional command launchers
- build-system management
- LLM-assisted editing until the base editor is stable

Supported core file types for the first pass:

- `.py`
- `.md`
- `.json`
- `.txt`
- `.html`
- `.css`
- `.js`
- `.csv` treated as plain text
- additional safe text files rendered as plain text when practical

---

## 3. User Experience

KoreCode should feel like a lightweight single-user desktop editor embedded in the Kore suite.

### 3.1 Shell

KoreCode should adopt the shared Kore suite shell from UIElements:

- suite top bar
- application bar beneath it
- KoreCode-specific accent color defined in shared theme code
- shared panel and workspace tokens from UIElements

### 3.2 Main Layout

The default layout is a three-part workspace:

- left: file explorer rooted at the KoreStack project folder
- center: tabbed editor surface for open files
- optional bottom or right panel later for LLM output, diagnostics, or action history

Phase 1 requires only the left explorer and central editor.

### 3.3 Editor Behaviors

Phase 1 editor behaviors:

- open files from the explorer into tabs
- switch between multiple open tabs
- detect unsaved changes
- save active file
- syntax highlight supported file types
- basic text selection support
- line numbers
- current-line highlight
- find-in-file

The Find control should toggle a dedicated find panel on and off within the active editor tab. When open, that panel sits above the editable code area and shifts the editor content downward rather than floating inside the scroll region.

Phase 1 prioritizes reliability, low dependency count, and fully open-source components over feature breadth.

---

## 4. File System Scope

The file explorer is wired to the KoreStack root.

Initial root target:

`C:\Util\GithubRepos\KoreStack`

Phase 1 scope is a single root only: the active KoreStack workspace.

The initial implementation should avoid multi-root behavior, arbitrary folder selection, and adjacent-repo management.

---

## 5. LLM-Assisted Features

The main value of KoreCode is not generic editing. It is code-aware assistance grounded in the local project.

However, implementation should be staged. The first delivery target is a robust local editor. LLM features follow only after the editor basics are working reliably.

### 5.1 Grand Context

KoreCode should maintain a mechanical summary of the codebase without requiring an LLM call.

This summary should extract items such as:

- file paths
- module names
- class names
- function names
- signatures
- docstrings where present
- possibly import relationships

This summary becomes structured context that can be attached to later prompts.

Likely outputs:

- a JSON index for fast lookup
- optional markdown summaries for human inspection

### 5.2 Autocomplete

Given the active file, cursor position, and nearby code, KoreCode should request suggested next lines.

The feature should:

- use current file context first
- attach relevant project summary entries when useful
- return a short continuation, not a full rewrite
- let the user accept, reject, or partially insert the suggestion

Initial trigger mode should be manual only.

### 5.3 Bug Fix / Improvement Review

Given a selected block, active function, or visible editor range, KoreCode should request a review for:

- likely bugs
- edge cases
- readability issues
- structural improvements
- Python-specific correctness concerns

This should produce commentary and optionally proposed replacement code.

Initial presentation target is inline annotations in the editor rather than a side panel or modal.

### 5.4 AI Action Types

Each action has a fixed shape: a **target** (what the model should look at), a **context** (what supporting material is attached), and an **output contract** (what the model is expected to return).

#### 5.4.1 Continue

The model extends the code from the cursor or end of a selected region.

- **Target:** cursor position or selected trailing block.
- **Context:** current file up to the cursor; optionally signatures from the wider file.
- **Output:** plain text continuation to be inserted after the cursor. No replacement; no explanation required.
- **UI:** ghosted inline preview; Tab to accept, Escape to reject. Chat panel not involved.

---

#### 5.4.2 Replace

The model rewrites a selected range according to an instruction.

- **Target:** selected lines `[file, from, to, content]`.
- **Context:** opt-in chips — whole file, signatures in scope, imports, a referenced second file.
- **Output:** structured edit block:
  ```json
  {
    "explanation": "...",
    "edits": [{ "file": "...", "from": 120, "to": 150, "replacement": "..." }]
  }
  ```
- **UI:** instruction typed in the chat composer; response renders as diff block with **Apply** and **Dismiss** buttons. Apply writes the replacement into the editor, marks the tab dirty, and enters the edit into undo history.

---

#### 5.4.3 Bug Hunt

The model reviews a selected range or whole function for defects, not rewriting unless asked.

- **Target:** selected region, or auto-expanded to the nearest enclosing function/class.
- **Context:** always includes the whole function; optionally the whole file.
- **Output:** commentary list — each item has a line reference, severity label (`bug` / `edge-case` / `style`), and a short explanation. Optionally a proposed replacement for each item.
- **UI:** commentary items rendered inline in the chat thread. Line references are clickable — click jumps the editor to that line. Each item with a proposed replacement has its own **Apply** button.

---

#### 5.4.4 Explain

The model explains what a selected region does in plain language.

- **Target:** selected lines.
- **Context:** surrounding function, optionally whole file.
- **Output:** prose explanation only. No edits proposed.
- **UI:** prose rendered in the chat thread. No Apply button.

---

#### 5.4.5 Architecture Conversation

A free-form multi-turn conversation about design, structure, or approach. No file is required to be active.

- **Target:** none, or a whole file attached voluntarily.
- **Context:** user-attached files or the grand context index; no auto-injection.
- **Output:** prose only.
- **UI:** standard chat thread (current mode). No diff blocks. The session persists for the lifetime of the page, not per-file.

---

#### 5.4.6 Naming and Summarising

The model proposes names, docstrings, or a module-level summary.

- **Target:** selected function, class, or whole file.
- **Context:** signatures from the enclosing scope.
- **Output:** plain text replacement for the name, docstring block, or file header. Structured as a single-edit block.
- **UI:** diff block in the chat thread with Apply.

---

#### Common UI Rules Across All Action Types

- If there is a live selection when the user opens the chat composer, the selection is automatically attached as the primary target and displayed as a dismissible chip.
- The user can manually pin additional context (whole file, a second file, signatures) using toggle chips below the input.
- Actions that return edits always render a diff block before the Apply button is shown. The user must see the proposed change before they can apply it.
- Apply is always undoable via the editor's normal undo stack.
- Multiple edits in one response are applied together as a single undo unit.

---

## 6. Technical Direction

A likely architecture is:

- Python backend for file IO, project indexing, and LLM request orchestration
- lightweight browser UI consistent with the other Kore apps
- shared shell and visual tokens from UIElements
- editor component chosen for local syntax highlighting and text operations

The current preference is to minimize code dependencies, favor reliable mature components, and stay fully open source.

The editor engine for KoreCode will be CodeMirror 6.

Rationale:

1. CodeMirror 6 keeps the dependency footprint smaller than Monaco.
2. It remains fully open source and well suited to a focused in-browser editor.
3. It provides the core capabilities needed for Phase 1 without pulling KoreCode toward full IDE scope.

Custom editing primitives are still out of scope because they would trade reliability for unnecessary implementation effort.

---

## 7. Data and Prompt Inputs

KoreCode will need to persist or derive:

- open tabs state
- recent files
- cursor and scroll position per file
- unsaved dirty state
- project summary index
- prompt history or action history
- model settings and prompt templates

Initial action history should be logged to a file under `datacontrol/korecore/` as requested, though the final folder name should be confirmed if `korecore` was meant to be `korecode`.

Prompt requests may combine:

- selected text
- active file path
- active file content
- cursor position
- surrounding lines
- project summary entries
- optional user instruction

AI-generated edits are expected to support direct in-place file updates once LLM features are enabled.

---

## 8. Phased Delivery

### Phase 1: Usable Editor

- create KoreCode app shell
- file explorer rooted at KoreStack
- open/save files
- tabs for open files
- syntax highlighting
- basic find-in-file
- no LLM dependency in the first usable milestone

Phase 1 find behavior uses a toggleable per-tab find panel embedded in the editor pane rather than an in-scroll overlay.

### Phase 2: Project Context

- mechanical project summarizer
- searchable function/class index
- attach summary context to actions

### Phase 3: LLM Actions

- autocomplete
- selection review
- bug-fix suggestions
- explain / rewrite flows

### Phase 4: Refinement

- better tree operations
- action history
- richer diff/accept UI
- support for adjacent approved Python repos

---

## 9. Resolved Decisions And Remaining Questions

Resolved decisions:

1. KoreCode should be a browser app aligned with the rest of the Kore suite.
2. Phase 1 root scope is only the current KoreStack workspace root.
3. Phase 1 first-class file support includes Python, markdown, JSON, TXT, and HTML/CSS/JS, with CSV treated as plain text.
4. The editor engine will be CodeMirror 6, chosen for minimal dependencies, reliability, and open-source licensing.
5. Autocomplete should be manual only.
6. Review and bug-fix results should appear as inline annotations.
7. Project summarization should run on demand.
8. AI-generated edits may write directly in place once AI features are enabled.
9. Action history should be written to a log file under `datacontrol/korecore/`, pending naming confirmation.
10. The first implementation should deliver a stable editor before any LLM integration is added.

Remaining questions:

These need answering before implementation details are locked.

1. Should the history/log path really be `datacontrol/korecore/`, or should it be renamed to `datacontrol/korecode/`?
2. Once AI features start, where should the mechanical project summary live: JSON files, SQLite, or transient regenerated output?
3. When AI features arrive, should they use existing Kore LLM plumbing directly or sit behind an abstract provider interface first?
4. What exact CodeMirror feature set should Phase 1 include beyond syntax highlighting, such as search, bracket matching, or fold gutters?
5. Do you want any file-tree write operations in Phase 1, or should the tree be open-only at first?

---

## 10. Initial Recommendation
KoreCode has improved materially, but it is still closer to “a good local code editor with AI plumbing” than “a fully capable local AI Python IDE.” The biggest remaining gaps are not cosmetic. They are execution ownership, language intelligence, environment control, and multi-file reliability.

My new top 10, ordered by positive impact:

Move the full agent loop into Python
The browser still decides too much. JS should submit intent, selection, cursor, active file, and user actions. Python should own prompt assembly, tool rounds, retries, proposal creation, validation, apply, and recovery. This is the single biggest step toward a real AI IDE.

Add a real Python language-service layer
KoreCode currently has custom AST helpers, but it does not yet have IDE-grade navigation or intelligence. The next leap is go to definition, find references, rename symbol, hover, diagnostics, import resolution, and workspace symbol search. Without this, it will not feel comparable to VSCode.

Replace ad hoc file/symbol helpers with a persistent workspace graph
KoreCodeWorkspace.md is useful, but not enough. Build a proper SQLite-backed index of files, symbols, imports, references, classes, functions, and docstrings with incremental refresh. That becomes the backbone for both AI context and normal IDE navigation.

Make edit proposals a first-class reviewed workflow everywhere
You now have the start of this. Finish it. Every AI write should become:
proposal -> preview diff -> validate -> apply -> reload -> attach to run
No bypasses. Retire the remaining direct client-side apply path once the proposal path is complete.

Add an integrated local execution environment
A coding IDE needs to run code. KoreCode needs interpreter selection, venv awareness, pytest execution, targeted script run, lint/format commands, captured stdout/stderr, and run history. Today it can edit code, but it cannot yet close the loop like an IDE.

Build a proper diagnostics pipeline
Syntax errors, import failures, test failures, lint findings, stale hash conflicts, proposal validation failures, and agent tool failures should all surface in one consistent diagnostics model. Right now errors are fragmented across alerts, tags, and run JSON.

Create a run inspector UI
You now have backend runs, tool calls, and proposals, but they are not yet a first-class operator surface. KoreCode needs a visible run timeline with statuses, prompt snapshots, tool requests, tool results, proposal IDs, apply results, and retry/resume controls. This is critical for trust and debuggability.

Unify chat/editor interactions around file-aware actions
The current chat panel is useful, but still relatively generic. To feel like an IDE, the main actions should be explicit and file-native:
continue, explain, bughunt, replace selection, fix failing test, rename symbol, generate tests, implement TODO
The user should not need to rely on free-form prompting for common coding actions.

Add background filesystem sync and conflict handling
KoreCode currently behaves like it is the only editor. A serious IDE must handle external file changes, branch switches, generated files, test artifacts, and index invalidation. It needs file watching, dirty-state reconciliation, and explicit conflict UI.

Retire stale architecture and duplicate paths
   KoreCode still carries signs of multiple generations:
older design docs that describe obsolete chat/runtime behavior
custom AST endpoints mixed with newer run/proposal APIs
client assumptions that still leak backend concerns
legacy direct edit logic that should eventually disappear
   This cleanup is lower impact than the items above, but it matters because duplication will slow every later feature.

The most important shift in thinking is this: stop treating KoreCode as “editor plus chat.” Treat it as a Python-first local orchestration system with an editor front end. VSCode-level capability comes from the backend owning language knowledge, environment control, execution, diagnostics, and safe edit application.


