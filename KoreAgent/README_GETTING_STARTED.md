# Getting Started with MiniAgentFramework

![MiniAgentFramework](progress/readme_header.png)

This guide covers everything needed to go from a blank machine to a working first run.

For usage reference (modes, slash commands, task management) see [README.md](README.md).
For module architecture and design notes see [README_DEVS.md](README_DEVS.md).

---

## Prerequisites

| Requirement | Minimum version | Notes |
|---|---|---|
| Python | 3.11 | 3.12+ recommended |
| Ollama | latest | [https://ollama.com](https://ollama.com) |
| Git | any | to clone the repo |

---

## 1. Install Python

Download and install Python 3.11 or later from [https://www.python.org/downloads/](https://www.python.org/downloads/).

On Windows, tick **"Add Python to PATH"** during installation.

Verify:
```powershell
python --version
```

---

## 2. Install Ollama

Download and install Ollama from [https://ollama.com](https://ollama.com).

After installation, verify Ollama is running:
```powershell
ollama list
```

If no models are shown yet, that is fine - see the next step.

---

## 3. Pull a model

MiniAgentFramework works best with a model in the 20B+ parameter range. A good default:

```powershell
ollama pull gemma3:27b
```

Smaller models (8B) run faster but are less reliable at multi-step tool use. If hardware is limited:
```powershell
ollama pull gemma3:12b
```

Check what is installed at any time:
```powershell
ollama list
```

---

## 4. Clone the repo

```powershell
git clone https://github.com/DaveSteadman/MiniAgentFramework.git
cd MiniAgentFramework
```

---

## 5. Create the virtual environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

The `(.venv)` prefix in your prompt confirms the environment is active.

---

## 6. Install Python dependencies

```powershell
pip install -r requirements.txt
```

---

## 7. Regenerate the skills catalog

The skills catalog (`code/KoreAgent/skills/skills_summary.md`) maps all available tools for the LLM. Build it once before first use:

```powershell
python .\code\KoreAgent\skills_catalog_builder.py
```

This step is also run automatically at startup whenever any `skill.md` file is newer than the catalog, so it only needs to be run manually after a fresh clone or after editing a `skill.md`.

---

## 8. First run

Start the Web UI / API server:
```powershell
python .\code\main.py
```

Then open:
```text
http://localhost:8000/
```

Example - target a different Ollama host:
```powershell
python .\code\main.py --llmhost http://MONTBLANC:11434
```

---

## Specifying a model

The `--model` flag accepts a short alias that is matched against installed model tags. For example if `gemma3:27b` is installed, `--model 27b` resolves to it:

```powershell
python .\code\main.py --model 27b
python .\code\main.py --model 12b --ctx 16384
```

List installed models directly via Ollama:
```powershell
ollama list
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ollama: command not found` | Ollama not in PATH | Reinstall Ollama and restart terminal |
| Model returns "I cannot" for date/web | Wrong or small model | Try a larger model with `--model 27b` |
| Skills catalog empty | Never built | Run `python .\code\KoreAgent\skills_catalog_builder.py` |
| LLM call times out | Model too large for VRAM | Use a smaller model or increase swap |
