# KoreStack (control plane)

KoreStack is the supervisor of the suite. It resolves shared configuration, launches every enabled service, probes their health, and serves the landing page that ties the suite together. The command line is documented in the [root README](../README.md).

## Role in the suite

Every other subsystem is a separate process with its own port. KoreStack is the only thing the operator starts: it builds the start plan from `config/korestack_config.json`, spawns the services, and keeps the shared URL map so the top bar of every UI can link to every other UI.

## Architecture

| Module | Responsibility |
|---|---|
| `main.py` | Argument parsing, service specs (name, command, port, health URL), start, status, dry-run |
| `dashboard.py` | Landing-page server: `/api/services`, suite URL map (`build_suite_urls`), Ollama endpoints |
| `ollama_control.py` | Talks to Ollama: version, loaded and configured models, System One model and stats |
| `endpoint_explorer.py` | Browser explorer of the HTTP endpoints exposed by the services |

UI assets are in `KoreUI/KoreStack/` and are served live, so there is no build step.

## Landing page

- **Paths**: suite root, data root, and the datacontrol and datauser folders in use
- **Ollama State**: Ollama version, the configured and loaded chat model, the System One model and its statistics
- **Services**: one card per service with health, port, and a link to its UI

## Health contract

A service is running when its `/status` returns success. Each service spec declares its own health URL, and the dashboard uses that URL rather than deriving one. A URL alias never replaces a real service entry in the suite URL map.

## Configuration

Ports, hosts, and enablement come from `config/korestack_config.json` (`services.<name>`), resolved through `KoreCommon/suite_paths.py`.

## Troubleshooting

| Problem | What to check |
|---|---|
| Startup exits before launching children | Run with `--dry-run` to inspect the plan |
| Health stays red | The service port is wrong or the child exited; check its log |
| Dashboard unreachable | `services.korestack.port` (29600) is busy |
| Ollama panel is empty | The Ollama host in `config/koreagent_config.json` is unreachable |
