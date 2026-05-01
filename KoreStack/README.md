# KoreStack

KoreStack is the coordinating service for the Kore system.

It is responsible for:

- starting and stopping the runnable Kore services
- presenting the landing page when the system starts
- surfacing service health, IPs, ports, and key runtime metrics
- exposing the shared data-folder layout defined in top-level config
- acting as the first resolver of suite-level config for cross-service startup

Typical entrypoints:

```powershell
python .\main.py
python .\KoreStack\main.py
```

## Startup

Start the full suite from the workspace root:

```powershell
python .\main.py
```

Start only selected services behind KoreStack:

```powershell
python .\main.py --services conversation,docs
python .\main.py --services docs,code
```

Start services without opening the landing page HTTP server:

```powershell
python .\main.py --services agent --no-dashboard
```

Show the resolved startup plan without launching anything:

```powershell
python .\main.py --dry-run
```

Probe the current status view without starting services:

```powershell
python .\main.py status
```

## Shutdown

If KoreStack is running in the foreground terminal, stop the landing page and all child
services with `Ctrl+C` in that same terminal.

If the landing page is running, you can also stop individual services from the service
cards using the `Stop` action. KoreStack will keep running until you stop the main
process.

The intended operator flow is:

- start the suite from the workspace root with `python .\main.py`
- manage individual services from the KoreStack landing page when needed
- stop the full suite with `Ctrl+C` in the terminal that launched KoreStack
