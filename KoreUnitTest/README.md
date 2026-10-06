# KoreUnitTest

KoreUnitTest is the unit-test subsystem. It runs the Python tests of the codebase once per build and records the results, following the same pattern as KoreTest2.

## Role in the suite

KoreTest2 checks the stack end to end through its interfaces; KoreUnitTest checks internal code directly. Both are keyed by `SUITE_VERSION`, so every new build is re-verified, and both are reached from the landing page like any other subsystem.

## Tests are user data

Each test is one Python file at `<dataroot>/datacontrol/koreunittest/tests/<Area>/test_*.py`. The file stem is the test ID. They live in datacontrol, not in the codebase, so they can be added or changed without touching the repository. Tests find the repository through the `KORESTACK_ROOT` environment variable.

## Architecture

| Element | Role |
|---|---|
| `service.py` | Discovers tests, schedules runs per build, stores results, serves the API |
| `run_file.py` | Runs one test file in its own subprocess: sets `KORESTACK_ROOT`, puts the repo root and the test directory on `sys.path`, loads the file by path |
| `results.sqlite3` | Build-keyed results under `datacontrol/koreunittest` |
| `runs/<build>/<id>.jsonl` | Per-test run logs |

Test classes (`unittest`) and plain `test_*` functions are both executed. A failing or erroring file never stops the others.

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/sessions` | Start a run; `{"rerun": true}` re-runs the current build |
| `GET /api/grid` | Tests against builds |
| `GET /api/runs/{id}` | Log of one test run |
| `/status`, `/ui` | Health and browser UI |

## Troubleshooting

| Problem | What to check |
|---|---|
| A test errors on import | It targets code that no longer exists; update or delete the file |
| Tests show pending | Start a run from the UI, or `POST /api/sessions` |
| Test cannot find repo code | Use `Path(os.environ["KORESTACK_ROOT"])` rather than relative paths |
