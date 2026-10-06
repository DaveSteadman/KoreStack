# KoreUnitTest

Build-keyed unit-test service (port 29618), modelled on KoreTest2.

- One Python file per test: `KoreUnitTest/tests/<Area>/test_*.py`. The file stem is the test ID.
- Each file runs in its own subprocess (`python -m KoreUnitTest.run_file <relpath>`) from the repo root, because services each have their own `app` package.
- Supports unittest classes and module-level `test_*` functions.
- Results are stored per (test, build) in `Data/datacontrol/koreunittest/`; a run starts automatically ~15s after startup when the current build has pending tests.
- API: `POST /api/sessions` (`{"rerun": true}` to rerun), `GET /api/grid`, `GET /api/runs/{id}`, `GET /status`, UI at `/ui`.
