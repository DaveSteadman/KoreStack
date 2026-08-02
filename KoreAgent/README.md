# KoreAgent

KoreAgent is the local agent runtime for the suite. It owns prompt orchestration, tool calling, slash commands, task scheduling, and the browser interface used to interact with the model.

## Why it exists

KoreStack needs an agent that can reason over local tools and local data without depending on a hosted orchestration layer. KoreAgent provides that runtime while keeping durable conversation state and other subsystem concerns outside the core orchestration loop.

## What it does

- Runs the tool-calling orchestration pipeline
- Integrates with configured LLM backends
- Loads built-in skills, external skills, and MCP-exposed tool surfaces
- Provides slash commands, scratchpad workflows, and delegated subruns
- Hosts the browser UI and scheduler for interactive and background work

## How to run it

Normally you start KoreAgent through the suite root:

```powershell
python .\main.py
```

To run KoreAgent on its own:

```powershell
python .\KoreAgent\main.py
```

## Install and configuration

- Install shared dependencies from the repo root with `pip install -r requirements.txt`
- Review `config/koreagent_config.json` before first run and make sure the configured host and model are valid
- Review `config/korestack_config.json` for MCP connections and related service URLs
- If you use Ollama or another local model host, make sure it is reachable before starting the agent
- Local Ollama auto-start is off by default; set `KORE_OLLAMA_AUTOSTART=1` only if you want KoreAgent to launch a local Ollama process itself

### Agent-specific first run

For a local Ollama-backed setup:

```powershell
ollama list
ollama pull gemma3:27b
python .\KoreAgent\app\skills_catalog_builder.py
python .\KoreAgent\main.py
```

Notes:

- Smaller models can work, but multi-step tool use is less reliable
- The skills catalog is also rebuilt automatically when `skill.md` inputs change, but a manual rebuild is a good first-run check
- The active host and default model should match `config/koreagent_config.json`

## Tool model

KoreAgent treats tools as one internal contract even when they come from different places.

- Local Python tools, built-in system skills, and remote MCP tools should look the same to orchestration
- MCP is the preferred remote-service transport, but it is not the internal source of truth
- Tool results should stay structured as long as possible and preserve provenance
- Read-only and mutating tools should remain clearly distinguishable in code and logs

## Guardrails and runtime control

The current runtime direction relies on host-side guardrails as well as prompt policy.

- web-grounded answers should not stop at search snippets when fetched evidence is required
- invalid, inactive, or repeated tool calls should be corrected or blocked by the runtime
- plan phases can restrict which tools are legal at a given stage
- the host should prevent false-success answers that claim writes or external actions never performed

## Execution direction

The current design direction is toward a tighter execution loop:

- explicit task planning before broader tool use
- bounded inspection, action, and validation phases
- more durable work-item state for substantial tasks
- better host-side repair of common tool-call mistakes
- cleaner separation between bounded agent execution and future long-running research management

## Developer orientation

The subsystem is best understood in four layers:

- orchestration and tool runtime
- API and browser input layer
- session-state integrations through KoreChat
- skill and MCP tool exposure

The durable design goals from the deleted planning notes are now:

- keep one internal tool model regardless of local or MCP origin
- keep selected or active tool exposure narrower than the full attached tool universe
- support durable record-shaped working sets for multi-step filtering and reporting tasks
- move larger decomposition and long-horizon research control out of the casual chat loop

## New user notes

- Use this README as the subsystem entry point
- Use the code and tests directly for implementation detail; this README is now the primary written entry point

## Troubleshooting

| Problem | What to check |
|---|---|
| Agent UI starts but model calls fail | Confirm the configured LLM host is reachable and the model name resolves |
| Tools do not appear | Check the skill catalog inputs and the configured MCP connections |
| The tool list looks stale after editing skills | Run `python .\KoreAgent\app\skills_catalog_builder.py` and restart the service |
| Scheduled tasks do not run | Verify the schedule files and the configured `datacontrol` path |
| Small models behave erratically on tool tasks | Use a stronger installed model and verify the resolved model name |
| Session history looks inconsistent | Confirm KoreChat and any shared storage paths point at the same suite data root |

## Skill authoring

New skills should follow the existing `skill.md` plus Python-module pattern already used under `app/skills/` and `app/system_skills/`.