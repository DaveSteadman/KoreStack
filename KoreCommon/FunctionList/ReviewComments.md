# KoreStack function-list review

## Naming standard

Use lowercase `snake_case` for functions.  Name actions with a verb
(`build_manifest`, `load_config`), predicates with `is_`/`has_`/`can_`, and
collection-returning functions with `list_` or `iter_`.  Prefix private module
helpers with one underscore.  Preserve framework-required names such as
`setUp`, `tearDown`, `do_GET`, and `do_POST`.

The current application functions already follow this standard.  The generator
helper that returned a list was renamed from `get_functions` to
`list_function_names`; its output now includes `async def` functions.

## Simple deletion review

No tracked source files were deleted in this pass.  The apparently standalone
files under `Data/datacontrol/` are documented command-line import/extraction
scripts, and the exact-match `__init__.py` files each define their own package
boundary.  Neither has sufficient evidence of redundancy for a safe deletion.

## Top 10 review comments

1. **P1 — Separate the long-running Reference importer from web-process
   lifetime.** The current dedicated worker protects API responsiveness, but a
   service restart still stops a multi-day job. Persist checkpoints and run it
   under a supervised worker process.

2. **P1 — Apply the independent SQLite-connection pattern to the remaining
   data services.** KoreFeed, KoreLibrary, and KoreRAG cache shared connections
   behind Python locks, which can serialize reads and writes despite WAL.

3. **P1 — Establish one import-job contract.** Feed ingestion, Kiwix imports,
   library imports, and RAG ingestors should expose the same durable job ID,
   progress, cancellation, retry, and error semantics.

4. **P2 — Consolidate the nearly identical LM Studio and Ollama graph
   extraction scripts.** Keep backend adapters small and share chunk fetching,
   response parsing, de-duplication, and graph submission.

5. **P2 — Consolidate common scaffolding in the manual graph-import scripts.**
   The company, military, munitions, and reference importers repeat root/config
   discovery, pagination, batching, and HTTP error policy.

6. **P2 — Retire path-injection compatibility gradually.** KoreData services
   still add `CommonCode` to `sys.path` and import generic module names such as
   `config` and `dbutil`; package-qualified imports would prevent collisions.

7. **P2 — Make function-list scope explicit.** It currently lists production,
   tests, and manually executed data-control scripts together. Add an optional
   production-only mode or separate sections so the index better supports code
   review.

8. **P2 — Enrich the function list with ownership context.** Nested functions,
   methods, and module-level functions currently share an unqualified name.
   Emit qualified names and line numbers to make the inventory actionable.

9. **P2 — Rationalise test discovery.** Tests are split among individual
   services, `KoreTest`, and direct scripts. Adopt one test command and register
   all focused suites there.

10. **P3 — Reduce service-local compatibility wrappers after callers migrate.**
    This review removed the redundant `CommonCode/compress.py` forwarding shim.
    Apply the same planned migration discipline to the remaining compatibility
    wrappers, retaining them only while legacy imports remain.
