# KoreStack

KoreStack is the suite control plane. It resolves shared configuration, launches enabled services, exposes the landing page, and reports service health and routing information.

## Why it exists

Without KoreStack, each service would need to be started and diagnosed separately. KoreStack provides the operator entry point for the full suite and keeps service coordination in one place.

## What it does

- Starts and stops runnable Kore services
- Shows the landing page and service status dashboard
- Resolves suite-level paths, ports, and URLs before child services launch
- Supports partial-start and dry-run workflows for debugging

## How to run it

From the repo root:

```powershell
python .\main.py
```

Or directly:

```powershell
python .\KoreStack\main.py
```

Useful variants:

```powershell
python .\main.py --dry-run
python .\main.py status
python .\main.py --services koreagent,korechat,koredocs
python .\main.py --services korecode --no-dashboard
```

## Install and configuration

- Install shared dependencies from the repo root with `pip install -r requirements.txt`
- KoreStack reads the suite config from `config/korestack_config.json`
- The suite root, data root, and derived service locations resolve through `KoreCommon/suite_paths.py`

## Troubleshooting

| Problem | What to check |
|---|---|
| Startup exits before launching children | Run `python .\main.py --dry-run` to inspect the resolved plan |
| One service blocks the suite | Start a narrower set with `--services ...` and isolate the failing subsystem |
| Health probes stay red | Confirm the target service port is correct and the child process actually stayed alive |
| Dashboard is not reachable | Check `services.korestack.port` and whether `--no-dashboard` was used |

## Related docs

- Root overview: [../README.md](../README.md)
