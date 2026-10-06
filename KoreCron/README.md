# KoreCron

KoreCron is the scheduler of the suite. It sends prompts to KoreAgent at scheduled times and triggers KoreTest2 runs, so recurring work and per-build testing happen without an operator.

## Role in the suite

- **Cron prompts**: named prompts with a schedule. When due, KoreCron opens a fresh KoreChat conversation, hands the prompt to KoreAgent, waits for the reply, and records the outcome.
- **Test runs**: schedule definitions that `POST` to KoreTest2 `/api/sessions`.
- It only calls other services; it holds no model or test logic itself.

## Architecture

| Element | Role |
|---|---|
| Definition store | `cronprompts.json` in `<dataroot>/datacontrol/korecron` |
| Scheduler | Computes next run times, persists `scheduler_state.json`, and appends `scheduler_run_history.json` |
| Dispatcher | Resolves service URLs from the suite config and makes the HTTP calls |
| Timeline | Next and past runs for the UI |

## API

| Endpoint | Purpose |
|---|---|
| `GET/POST /api/cronprompts` | List and create prompt schedules |
| `PUT/DELETE /api/cronprompts/{name}` | Update and delete |
| `POST /api/cronprompts/{name}/clone`, `/run`, `/agent-resume` | Clone, run now, resume an agent run |
| `GET/POST /api/test-runs`, `DELETE /api/test-runs/{id}` | Scheduled test runs, such as the KoreTest2 full run |
| `GET /api/timeline` | Schedule timeline |
| `/status`, `/ui` | Health and browser UI |

## Troubleshooting

| Problem | What to check |
|---|---|
| A schedule never fires | The entry is enabled and its schedule text parses; see the timeline |
| Prompt runs but no reply | KoreAgent and KoreChat are both healthy |
| Test run does nothing | KoreTest2 is running; its session may find nothing new to run |
