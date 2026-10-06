# KoreAgent

KoreAgent is the agent runtime of the suite. It runs the LLM tool-calling loop, owns skills and slash commands, serves the chat UI, and exposes the LLM and System One decision models to every other subsystem.

## Role in the suite

- **KoreChat** stores the durable threads; KoreAgent reasons and acts on them.
- **KoreCron** posts scheduled prompts to it; **KoreComms** messages reach it through KoreChat events.
- **KoreTest2** (prompt tests and the `judge` assert) and **KoreAgentNetwork** (LLM and Decision blocks) call `POST /api/work-packet`.
- It reaches **KoreData**, **KoreDocs**, **KoreCode**, and **KoreLiveWeb** as tools, over MCP and HTTP.
- KoreStack's Ollama State panel reads the model information it reports.

## Architecture

| Layer | Location (`app/`) | Responsibility |
|---|---|---|
| Input layer | `input_layer/` | HTTP routes: sessions, prompts, history, queue, logs, status, KoreChat proxy, slash commands |
| Orchestration | `agent/orchestration/` | Engine, sessions, context window, history, stop state |
| Tool runtime | `agent/tool_runtime/` | Tool loop, guards, recovery, formatting, tool selection and catalog |
| LLM clients | `llm_client*.py` | Ollama, OpenAI-compatible and LM Studio chat; `llm_client_system_one.py` for System One |
| Skills | `system_skills/`, `skill_manager.py` | Built-in skills (`skill.md` plus a Python module), service skill manifests, the skills catalog |
| Context | `context_compactor.py`, `context_manager.py` | Compaction for long conversations |
| Scheduler | `scheduler/` | Background and scheduled prompt execution |
| Datasets | `datasets_pkg/`, `working_data.py` | Record-shaped working sets for multi-step tasks |

## Two kinds of model

- **Chat model**: the normal tool-calling model.
- **System One model**: a decision model, not a chat model. It scores named, typed questions (`choice`, `noul`, `score`) over text, JSON, or images through Ollama's `/v1/systemone` endpoint, returning probabilities. The `SystemOne` skill makes it available to the chat model.

Switch models at runtime with slash commands such as `/systemone model <name>`. Defaults come from `config/koreagent_config.json`.

## Key API

| Endpoint | Purpose |
|---|---|
| `POST /api/sessions/{id}/prompt` | Submit a prompt to a session; stream results via `GET /api/runs/{run_id}/stream` |
| `GET /api/sessions/{id}/history` | Session history |
| `POST /api/work-packet` | One-shot LLM or System One decision call used by other subsystems |
| `GET /api/skills/catalog`, `POST /api/skills/invoke` | Skill discovery and invocation |
| `/api/skill-manager/...` | Register and manage skills and tools |
| `GET /api/status`, `/api/status/ollama`, `/api/version` | Health, Ollama and build information |
| `GET /api/logs...`, `GET /api/queue` | Run logs and the execution queue |
| `/api/kc/...` | Proxy to KoreChat conversations |

## Tool model

Local Python skills, system skills, and remote MCP tools share one internal contract. Results stay structured and keep provenance. The runtime enforces guardrails: it corrects or blocks invalid, inactive, or repeated tool calls, requires fetched evidence for web-grounded answers, and prevents false-success claims about actions never performed. Only a narrow selection of tools is exposed per turn.

## Configuration

- `config/koreagent_config.json`: LLM host, chat model, System One model, context window
- `config/korestack_config.json`: MCP connections and service URLs
- Local Ollama auto-start is off; set `KORE_OLLAMA_AUTOSTART=1` to let KoreAgent launch it

## Skill authoring

Add a folder under `app/system_skills/` with a `skill.md` and a Python module. The catalog is rebuilt when skill inputs change.

## Troubleshooting

| Problem | What to check |
|---|---|
| Model calls fail | The LLM host is reachable and the configured model exists |
| System One calls fail with 404 | The Ollama build lacks `/v1/systemone`, or the configured model is not a System One model |
| Tools do not appear | Skill catalog inputs and MCP connections |
| Scheduled prompts do not run | KoreCron is running and the schedule files exist in datacontrol |
| Session history looks inconsistent | KoreChat and KoreAgent share the same data root |
