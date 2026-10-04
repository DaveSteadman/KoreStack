# KoreAgentNetwork

KoreAgentNetwork is the visual processing layer for KoreStack. A network is a persisted JSON diagram of Python nodes and named connections: output ports from one node become input values to the next node.

## What it does

- Draw and connect processing blocks in the browser.
- Keep network definitions in `datauser/KoreNetworks/`, so they are user-owned and portable.
- Execute nodes in dependency order and expose per-node outputs, failures, and timings.
- Give embedded Python explicit access to configured local services through `api_get(service, path)` and `api_post(service, path, body)`.

## Node contract

Each block receives an `inputs` dictionary and must set its declared values in an `outputs` dictionary.

```python
status = api_get("koreagent", "/status")
outputs["model"] = status["runtime"]["model"]
```

The runner also makes `json`, `math`, and `re` available. It permits only configured KoreStack service names; arbitrary URLs and filesystem helpers are not supplied. Each node runs in a new isolated Python subprocess with a 60-second limit, and a network has a five-minute overall limit. This is a bounded, trusted-local execution feature—not a security boundary for malicious Python.

## Run

Start through KoreStack or directly:

```powershell
python .\KoreAgentNetwork\main.py
```

Open `http://127.0.0.1:29617/ui` with the checked-in configuration.
