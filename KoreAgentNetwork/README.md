# KoreAgentNetwork

KoreAgentNetwork is the visual automation layer. A network is a persisted JSON diagram of blocks joined by named connections; the output ports of one block become the input values of the next. Networks are how a user composes calls to the other subsystems without writing a service.

## Role in the suite

- Blocks reach any configured KoreStack service over HTTP (`api_get`, `api_post`).
- LLM and Decision blocks call KoreAgent's `POST /api/work-packet`, using the chat model and the System One decision model.
- Networks live in the user's data space, so they are portable and editable.

## Block types

| Block | What it does |
|---|---|
| Python | Runs user code with `inputs` and an `outputs` dictionary |
| LLM | Renders a prompt template from the inputs and returns the model response |
| Decision | Asks System One a yes/no question about the inputs; outputs the probability and pass or fail against a threshold |

Each block has a "Label & Type" header, input ports, and output ports. An output may feed several inputs, but an input accepts only one connection; selecting an existing route again removes it. A port set to `NoValue` stops downstream blocks from running.

## Python block helpers

The working directory of a Python block is the `datauser` root, so file operations act on user space.

| Helper | Use |
|---|---|
| `api_get(service, path)`, `api_post(service, path, body)` | Call a configured service |
| `llm(prompt, model="")`, `llm_result(...)`, `decide(question, state)`, `judge(...)` | Model calls |
| `read_text`, `write_text`, `append_text`, `read_json`, `write_json`, `exists`, `list_files`, `delete_file`, `open` | Files, confined to `datauser` |
| `NoValue`, `json`, `math`, `re` | Utilities |

```python
outputs['result'] = NoValue
if not exists('prompts.txt'):
    return
items = read_text('prompts.txt').splitlines()
i = read_json('prompts.idx.json', default=0)
outputs['result'] = items[i % len(items)]
write_json('prompts.idx.json', (i + 1) % len(items))
```

State kept in a file survives between runs, which is how a network steps through a list one item per run.

## Execution

Blocks run in dependency order. Each Python block runs in a fresh subprocess (60 second limit) and a network has a five-minute limit. This is bounded, trusted-local execution, not a security boundary.

## API

| Endpoint | Purpose |
|---|---|
| `GET/POST /api/networks` | List and create |
| `GET/PUT/DELETE /api/networks/{id}` | Read, save, delete |
| `POST /api/networks/{id}/run` | Run a whole network |
| `POST /api/run-node` | Run one block with given inputs |

## UI

Tabs per network, pan and zoom, a delete button, and resizable left and right panels with sliders. Per-block outputs, errors, and timings show after a run.

## Data

Networks are JSON files in `datauser/KoreNetworks/`. Block templates (the UI Templates tab: `GET/POST /api/templates`, `DELETE /api/templates/{id}`) are in `datauser/KoreNetworkTemplates/templates.json`, seeded with starter blocks on first use. `POST /api/networks/{id}/duplicate` copies a network as "Title (n)". The UI reopens the last viewed network and sizes the canvas to all nodes plus a 1000px margin.

## Troubleshooting

| Problem | What to check |
|---|---|
| Unknown service error | The name matches a service in `config/korestack_config.json` |
| LLM or Decision block fails | KoreAgent is running and its models are configured |
| File helper cannot find a file | Paths are relative to the `datauser` root |
| Downstream block did not run | An upstream port was `NoValue` |

## Run state

Block results and edited port values are saved per network to `datacontrol/koreagentnetwork/runstate/<id>.json` (`GET/PUT /api/networks/{id}/state`) after every block run, so they survive page navigation and restarts. Deleting a network deletes its state.

