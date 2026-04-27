# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Discovers all skill.md definition files and builds a consolidated JSON catalog for orchestration.
#
# Scans the skills directory recursively for skill.md files, summarises each one into a structured
# JSON record (skill name, module path, functions, inputs, outputs), then writes the full catalog
# as a machine-readable JSON file for runtime use. A companion skills_summary.md file is produced
# for human inspection only. The orchestration layer uses the JSON catalog to build JSON Schema
# tool definitions sent to the model via /v1/chat/completions.
#
# Supports two summarisation modes:
#   - LLM-assisted: sends the skill.md text to an Ollama model and parses the JSON response.
#   - Local (--no-llm): deterministic regex/text extraction, used as a fallback or for CI.
#
# Usage:
#   python skills_catalog_builder.py
#   python skills_catalog_builder.py --no-llm
#   python skills_catalog_builder.py --skills-root /path/to/skills --output-json /path/to/output.json
#   python skills_catalog_builder.py --output-summary /path/to/output.md
#
# Related modules:
#   - llm_client.py    -- used for optional LLM-assisted summarisation
#   - orchestration.py    -- calls build_tool_definitions at runtime; consumes the catalog
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

from KoreAgent.llm_client import call_ollama
from KoreAgent.llm_client import ensure_ollama_running
from KoreAgent.utils.workspace_utils import get_workspace_root
from KoreAgent.utils.workspace_utils import normalize_module_path


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
SKILLS_SCHEMA_VERSION        = "1.0"
DEFAULT_SKILLS_ROOT          = Path(__file__).resolve().parent / "skills"
DEFAULT_SYSTEM_SKILLS_ROOT   = Path(__file__).resolve().parent / "system_skills"
DEFAULT_OUTPUT_FILE          = DEFAULT_SKILLS_ROOT / "skills_catalog.json"
DEFAULT_SUMMARY_FILE         = DEFAULT_SKILLS_ROOT / "skills_summary.md"
DEFAULT_SUMMARY_MODEL = "gpt-oss:20b"
_LOADED_PAYLOAD_CACHE: dict[tuple[str, float, int], dict] = {}
_TOOL_DEFS_CACHE: dict[str, list[dict]] = {}


def _workspace_abspath(module_path: str) -> Path:
    workspace_root = get_workspace_root()
    candidate = str(module_path).strip()
    if not candidate.endswith(".py"):
        candidate = f"{candidate}.py"
    return (workspace_root / candidate).resolve()


def _load_module_from_path(module_path: str):
    absolute_module_path = _workspace_abspath(module_path)
    if not absolute_module_path.exists():
        return None

    # Use the same canonical name as skill_executor so both loaders share the same module
    # object and module-level state (e.g. sandbox flags, caches) rather than getting separate
    # instances from two independent exec_module calls.
    dynamic_module_name = f"skill_module_{absolute_module_path.stem}_{abs(hash(str(absolute_module_path)))}"

    if dynamic_module_name in sys.modules:
        return sys.modules[dynamic_module_name]

    spec = importlib.util.spec_from_file_location(dynamic_module_name, absolute_module_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[dynamic_module_name] = module
    spec.loader.exec_module(module)
    return module



def _existing_callable_signatures(functions: list[str], module_path: str) -> list[str]:
    if not module_path:
        return functions

    module = _load_module_from_path(module_path)
    if module is None:
        return functions

    filtered: list[str] = []
    for function_sig in functions:
        func_name = str(function_sig).split("(", 1)[0].strip()
        if func_name and hasattr(module, func_name):
            filtered.append(function_sig)
    return filtered


# ====================================================================================================
# MARK: CLI
# ====================================================================================================
def parse_catalog_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build JSON summary catalog for all skills.")
    parser.add_argument("--skills-root", default=str(DEFAULT_SKILLS_ROOT), help="Root folder containing skills.")
    parser.add_argument("--output-json", default=str(DEFAULT_OUTPUT_FILE), help="Output JSON catalog file.")
    parser.add_argument("--output-summary", default=str(DEFAULT_SUMMARY_FILE), help="Output markdown summary file.")
    parser.add_argument("--model", default=DEFAULT_SUMMARY_MODEL, help="Ollama model used for LLM summarization.")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM calls and use local deterministic extraction only.",
    )
    parser.add_argument(
        "--num-ctx",
        type=int,
        default=131072,
        help="Context window for summary LLM calls (ignored with --no-llm).",
    )
    return parser.parse_args()


# ----------------------------------------------------------------------------------------------------
def find_skill_files(skills_root: Path) -> list[Path]:
    found = list(skills_root.rglob("skill.md"))
    if DEFAULT_SYSTEM_SKILLS_ROOT.exists() and DEFAULT_SYSTEM_SKILLS_ROOT.resolve() != skills_root.resolve():
        found.extend(DEFAULT_SYSTEM_SKILLS_ROOT.rglob("skill.md"))
    return sorted(found)


# ----------------------------------------------------------------------------------------------------
def summarize_with_llm(skill_md_path: Path, model_name: str, num_ctx: int) -> dict | None:
    skill_text = skill_md_path.read_text(encoding="utf-8")

    prompt = (
        "You are summarizing a software skill definition file.\n"
        "Return ONLY a single JSON object with this schema and no extra keys:\n"
        "{\n"
        '  "skill_name": "string",\n'
        '  "relative_path": "string",\n'
        '  "purpose": "string",\n'
        '  "module": "string",\n'
        '  "functions": ["string"],\n'
        '  "inputs": ["string"],\n'
        '  "outputs": ["string"]\n'
        "}\n"
        f"relative_path must be exactly: {skill_md_path.as_posix()}\n"
        "If any section is missing, infer conservatively from the file text.\n"
        "Skill file text:\n"
        f"{skill_text}"
    )

    llm_response = call_ollama(model_name=model_name, prompt=prompt, num_ctx=num_ctx)
    try:
        return json.loads(extract_first_json_object(llm_response))
    except (RuntimeError, json.JSONDecodeError):
        return None


# ----------------------------------------------------------------------------------------------------
def _parse_param_descriptions(skill_text: str) -> dict[str, dict[str, str]]:
    # Returns {func_name: {param_name: description_string}} parsed from the ## Parameters section.
    # Each ### heading identifies a function; bullet lines beneath it map param names to descriptions.
    result: dict[str, dict[str, str]] = {}
    params_match = re.search(r"##\s+Parameters\s*\n(.*?)(?=\n##\s|\Z)", skill_text, re.DOTALL | re.IGNORECASE)
    if not params_match:
        return result
    section = params_match.group(1)
    blocks  = re.split(r"\n(?=###\s)", "\n" + section)
    for block in blocks:
        block = block.strip()
        if not block.startswith("###"):
            continue
        first_nl  = block.find("\n")
        heading   = block[:first_nl].strip() if first_nl > 0 else block
        body      = block[first_nl:] if first_nl > 0 else ""
        func_match = re.match(r"###\s+`?([A-Za-z_][A-Za-z0-9_]*)\s*[\(`]", heading)
        if not func_match:
            continue
        func_name  = func_match.group(1)
        param_dict: dict[str, str] = {}
        for line in body.splitlines():
            pm = re.match(r"\s*-\s+`([A-Za-z_][A-Za-z0-9_]*)`\s*(?:\*[^*]*\*\s*)?-\s+(.*)", line)
            if pm:
                param_dict[pm.group(1)] = pm.group(2).strip()
        if param_dict:
            result[func_name] = param_dict
    return result


# ----------------------------------------------------------------------------------------------------
def _parse_triggers(skill_text: str) -> list[str]:
    # Extract trigger phrases from the ## Triggers section body.
    # When a bullet contains backtick-quoted items (possibly comma-separated), each quoted
    # phrase is extracted individually. Plain-text bullets are taken as-is after stripping
    # the leading "- " marker. Either way, the "Invoke this skill when..." descriptor line
    # is excluded since it is a header, not a trigger phrase.
    triggers: list[str] = []
    block_match = re.search(r"##\s+Triggers\s*\n(.*?)(?=\n##\s|\Z)", skill_text, re.DOTALL | re.IGNORECASE)
    if not block_match:
        return triggers
    for line in block_match.group(1).splitlines():
        bullet_match = re.match(r"\s*-\s+(.*)", line)
        if not bullet_match:
            continue
        content = bullet_match.group(1).strip()
        if content.lower().startswith("invoke this skill"):
            continue
        backtick_phrases = re.findall(r"`([^`]+)`", content)
        if backtick_phrases:
            triggers.extend(backtick_phrases)
        elif content:
            triggers.append(content)
    return triggers


def _section_body(skill_text: str, heading: str) -> str:
    match = re.search(rf"##\s+{re.escape(heading)}\s*\n(.*?)(?=\n##\s|\Z)", skill_text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_function_signatures(skill_text: str, module: str) -> list[str]:
    interface_body = _section_body(skill_text, "Interface")
    if not interface_body:
        interface_body = skill_text
    candidates = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*\([^`]*\))`", interface_body)
    filtered: list[str] = []
    for sig in candidates:
        parsed = _parse_tool_signature(sig)
        if parsed is None:
            continue
        name, params = parsed
        if sig.endswith("(...)"):
            continue
        if params:
            filtered.append(sig)
        else:
            filtered.append(f"{name}()")
    deduped = list(dict.fromkeys(filtered))
    return _existing_callable_signatures(deduped, module)


# ----------------------------------------------------------------------------------------------------
def summarize_locally(skill_md_path: Path) -> dict:
    # utf-8-sig strips the BOM if present, otherwise behaves like utf-8.
    skill_text = skill_md_path.read_text(encoding="utf-8-sig")
    lines      = [line.strip() for line in skill_text.splitlines() if line.strip()]

    # Use the first Markdown heading as the skill title, falling back to the parent directory name.
    title = next((line.lstrip("# ").strip() for line in lines if line.startswith("#")), skill_md_path.parent.name)
    purpose = _section_body(skill_text, "Purpose") or _section_body(skill_text, "Overview")

    # Extract the module path. Handles two formats:
    #   - Bullet:  "- Module: `path`"
    #   - Heading: "## Module\n`path`"
    module = ""
    module_match = re.search(r"-\s*Module:\s*`([^`]+)`", skill_text)
    if module_match:
        module = module_match.group(1)
    else:
        heading_match = re.search(r"##\s+Module\s*\n+`([^`]+)`", skill_text)
        if heading_match:
            module = heading_match.group(1)

    # Extract trigger keyword from '## Trigger keyword: X' heading if present.
    trigger_keyword = ""
    trigger_match = re.search(r"##\s+Trigger keyword:\s*(.+)", skill_text, re.IGNORECASE)
    if trigger_match:
        trigger_keyword = trigger_match.group(1).strip()

    functions = _extract_function_signatures(skill_text, module)

    # Extract bullet-point items from the Input and Output sections.
    input_section = re.findall(r"## Input(.*?)(##|$)", skill_text, re.DOTALL | re.IGNORECASE)
    output_section = re.findall(r"## Output(.*?)(##|$)", skill_text, re.DOTALL | re.IGNORECASE)

    input_lines = []
    if input_section:
        input_lines = [line.strip(" -") for line in input_section[0][0].splitlines() if line.strip().startswith("-")]

    output_lines = []
    if output_section:
        output_lines = [line.strip(" -") for line in output_section[0][0].splitlines() if line.strip().startswith("-")]

    return {
        "skill_name":        title,
        "relative_path":     skill_md_path.as_posix(),
        "purpose":           purpose,
        "module":            module,
        "trigger_keyword":   trigger_keyword,
        "triggers":          _parse_triggers(skill_text),
        "functions":         functions,
        "inputs":            input_lines,
        "outputs":           output_lines,
        "param_descriptions": _parse_param_descriptions(skill_text),
    }


# ----------------------------------------------------------------------------------------------------
def summarize_skill(skill_md_path: Path, use_llm: bool, model_name: str, num_ctx: int) -> dict:
    summary: dict | None = None
    if use_llm:
        try:
            llm_summary = summarize_with_llm(skill_md_path=skill_md_path, model_name=model_name, num_ctx=num_ctx)
            if isinstance(llm_summary, dict):
                summary = llm_summary
        except Exception as exc:
            print(f"[catalog] LLM summarise failed for {skill_md_path.name}: {exc}")

    if summary is None:
        summary = summarize_locally(skill_md_path=skill_md_path)
    elif "param_descriptions" not in summary:
        # LLM path does not produce param_descriptions or triggers - overlay from local parse.
        skill_text = skill_md_path.read_text(encoding="utf-8-sig")
        summary["param_descriptions"] = _parse_param_descriptions(skill_text)
        if "triggers" not in summary:
            summary["triggers"] = _parse_triggers(skill_text)

    return summary


# ----------------------------------------------------------------------------------------------------
def to_workspace_relative_path(path: Path) -> str:
    workspace_root = Path(__file__).resolve().parent.parent
    try:
        return path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


# ----------------------------------------------------------------------------------------------------
def normalize_summary(summary: dict, skill_md_path: Path) -> dict:
    normalized = dict(summary)
    normalized["relative_path"] = to_workspace_relative_path(skill_md_path)

    normalized.setdefault("trigger_keyword", "")
    normalized.setdefault("triggers", [])
    normalized.setdefault("param_descriptions", {})
    for field_name in ["functions", "inputs", "outputs"]:
        field_value = normalized.get(field_name, [])
        if isinstance(field_value, list):
            normalized[field_name] = list(dict.fromkeys(str(item).strip() for item in field_value if str(item).strip()))
        elif isinstance(field_value, str) and field_value.strip():
            normalized[field_name] = [field_value.strip()]
        else:
            normalized[field_name] = []

    return normalized


def build_skills_payload(
    skills_root: Path,
    use_llm: bool = False,
    model_name: str = "",
    num_ctx: int = 0,
) -> dict:
    skill_files = find_skill_files(skills_root)
    summaries = [
        normalize_summary(
            summarize_skill(skill_file, use_llm=use_llm, model_name=model_name, num_ctx=num_ctx),
            skill_file,
        )
        for skill_file in skill_files
    ]
    return {
        "schema_version": SKILLS_SCHEMA_VERSION,
        "skills_root": to_workspace_relative_path(skills_root),
        "skills": summaries,
    }


# ====================================================================================================
# MARK: CATALOG LOADING
# ====================================================================================================
def extract_first_json_object(text: str) -> str:
    """Extract the first complete JSON object from *text*, raising RuntimeError if none found."""
    start_index = text.find("{")
    if start_index < 0:
        raise RuntimeError("No JSON object found in provided text")

    depth     = 0
    in_string = False
    escaped   = False

    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start_index : index + 1]

    raise RuntimeError("Failed to parse a complete JSON object from text")


# ----------------------------------------------------------------------------------------------------
def _rebuild_skills_catalog_if_stale(catalog_path: Path) -> None:
    """Rebuild the runtime JSON catalog when any skill.md is newer than the catalog."""
    skills_root = catalog_path.parent
    if not catalog_path.exists():
        needs_rebuild = True
    else:
        summary_mtime = catalog_path.stat().st_mtime
        skill_files   = list(skills_root.rglob("skill.md"))
        if DEFAULT_SYSTEM_SKILLS_ROOT.exists():
            skill_files.extend(DEFAULT_SYSTEM_SKILLS_ROOT.rglob("skill.md"))

        # Detect a count mismatch first - catches deleted skill folders where no
        # remaining file has a newer mtime than the catalog.
        needs_rebuild = False
        try:
            existing = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
            if len(existing.get("skills", [])) != len(skill_files):
                needs_rebuild = True
        except Exception:
            needs_rebuild = True

        if not needs_rebuild:
            for sf in skill_files:
                if sf.stat().st_mtime > summary_mtime:
                    needs_rebuild = True
                    break
                try:
                    skill_text = sf.read_text(encoding="utf-8-sig")
                except Exception:
                    continue
                module_match = re.search(r"-\s*Module:\s*`([^`]+)`", skill_text)
                if not module_match:
                    module_match = re.search(r"##\s+Module\s*\n+`([^`]+)`", skill_text)
                if not module_match:
                    continue
                try:
                    module_path = _workspace_abspath(module_match.group(1))
                except Exception:
                    continue
                if module_path.exists() and module_path.stat().st_mtime > summary_mtime:
                    needs_rebuild = True
                    break

    if not needs_rebuild:
        return

    payload = build_skills_payload(skills_root, use_llm=False, model_name="", num_ctx=0)
    write_skills_catalog(payload, catalog_path)


# ----------------------------------------------------------------------------------------------------
def load_skills_payload(catalog_path: Path) -> dict:
    """Load the skills catalog payload from a JSON catalog path or legacy summary path."""
    catalog_path = Path(catalog_path)
    if catalog_path.is_dir():
        catalog_path = catalog_path / DEFAULT_OUTPUT_FILE.name
    if catalog_path.suffix.lower() == ".md":
        raw_text = catalog_path.read_text(encoding="utf-8")
        json_segment = extract_first_json_object(raw_text)
        return json.loads(json_segment)

    _rebuild_skills_catalog_if_stale(catalog_path)
    stat = catalog_path.stat()
    cache_key = (str(catalog_path.resolve()), stat.st_mtime, stat.st_size)
    cached = _LOADED_PAYLOAD_CACHE.get(cache_key)
    if cached is not None:
        return cached
    payload = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
    _LOADED_PAYLOAD_CACHE.clear()
    _LOADED_PAYLOAD_CACHE[cache_key] = payload
    return payload


# ====================================================================================================
# MARK: TOOL DEFINITIONS
# ====================================================================================================
# _CLEAN_SIG_RE: rejects signatures containing <placeholders> or \n-style escape sequences.
# Quoted string defaults (date="") are allowed; see the positional-string guard in the parser.
_CLEAN_SIG_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\(([^<>\\]*)\)$')
_PARAM_RE     = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*([A-Za-z_][A-Za-z0-9_\[\]| ]*))?\s*(?:=\s*\S+)?')


def _python_type_to_json_schema(ptype: str) -> dict:
    """Return a JSON Schema fragment for a Python type annotation string."""
    ptype_stripped = ptype.strip()
    list_match = re.match(r'^list\[([^\]]+)\]$', ptype_stripped, re.IGNORECASE)
    if list_match:
        return {"type": "array", "items": _python_type_to_json_schema(list_match.group(1))}
    ptype_lower = ptype_stripped.lower()
    if ptype_lower in ("bool", "boolean"):
        return {"type": "boolean"}
    if ptype_lower in ("int",):
        return {"type": "integer"}
    if ptype_lower in ("float", "number"):
        return {"type": "number"}
    return {"type": "string"}


def _parse_tool_signature(sig: str) -> tuple[str, list[dict]] | None:
    """Parse 'func_name(p1: type1, p2: type2)' into (name, params_list).

    Returns None when the signature looks like an example call (contains <>, backslashes,
    or has a quoted string as the first positional argument).
    """
    sig = sig.strip()
    # Reject example calls whose first argument is a string literal: func("...", ...)
    if re.match(r'^[A-Za-z_][A-Za-z0-9_]*\s*\(\s*["\']', sig):
        return None
    m = _CLEAN_SIG_RE.match(sig)
    if not m:
        return None
    func_name  = m.group(1)
    params_str = m.group(2).strip()

    params: list[dict] = []
    if params_str:
        for part in params_str.split(","):
            part = part.strip()
            if not part:
                continue
            pm = _PARAM_RE.match(part)
            if not pm:
                continue
            pname       = pm.group(1).strip()
            ptype       = (pm.group(2) or "str").strip()
            has_default = "=" in part
            params.append({"name": pname, "type": ptype, "required": not has_default})

        # If params_str was non-empty but nothing parsed, the signature is a
        # placeholder like func(...) - treat it as an example and reject it so
        # a properly-typed overload later in the list can win the seen_names slot.
        if not params:
            return None

    return func_name, params


def build_tool_definitions(skills_payload: dict) -> list[dict]:
    """Convert the skills catalog payload into OpenAI-format JSON Schema tool definitions.

    Each clean function signature (e.g. 'get_datetime_data()' or 'set_task_prompt(name: str, ...)')
    becomes one tool entry. Example-call entries in the functions list are silently skipped.
    Compatible with Ollama /v1/chat/completions, LM Studio, and OpenAI.
    """
    payload_key = json.dumps(skills_payload, sort_keys=True, ensure_ascii=False)
    cached = _TOOL_DEFS_CACHE.get(payload_key)
    if cached is not None:
        return cached

    tools:      list[dict] = []
    seen_names: set[str]   = set()

    for skill in skills_payload.get("skills", []):
        purpose         = skill.get("purpose", "")
        trigger_kw      = skill.get("trigger_keyword", "").strip()
        description     = f"Triggered by keyword '{trigger_kw}'. {purpose}" if trigger_kw else purpose

        skill_module = None
        skill_module_path = str(skill.get("module", "")).strip()
        if skill_module_path:
            skill_module = _load_module_from_path(skill_module_path)

        skill_param_descriptions = skill.get("param_descriptions", {})

        for func_sig in skill.get("functions", []):
            parsed = _parse_tool_signature(func_sig)
            if parsed is None:
                continue
            func_name, params = parsed
            if func_name in seen_names:
                continue
            seen_names.add(func_name)

            # Prefer a per-function docstring over the skill-level description.
            func_description = description
            if skill_module:
                func_obj = getattr(skill_module, func_name, None)
                if func_obj and getattr(func_obj, "__doc__", None):
                    first_para = func_obj.__doc__.strip().split("\n\n")[0]
                    func_description = " ".join(first_para.split())

            properties: dict = {}
            required:   list = []
            func_param_descs = skill_param_descriptions.get(func_name, {})
            for p in params:
                param_name = p["name"]
                raw_desc   = func_param_descs.get(param_name, "").strip()
                param_desc = raw_desc if raw_desc else f"Parameter '{param_name}'."
                properties[param_name] = {
                    **_python_type_to_json_schema(p["type"]),
                    "description": param_desc,
                }
                if p["required"]:
                    required.append(param_name)

            tool_func: dict = {
                "name":        func_name,
                "description": func_description,
                "parameters": {
                    "type":       "object",
                    "properties": properties,
                },
            }
            if required:
                tool_func["parameters"]["required"] = required

            tools.append({"type": "function", "function": tool_func})

    _TOOL_DEFS_CACHE.clear()
    _TOOL_DEFS_CACHE[payload_key] = tools
    return tools


# ----------------------------------------------------------------------------------------------------
def render_summary_document(payload: dict, output_path: Path) -> str:
    return "\n".join(
        [
            "# Skills Summary",
            "",
            "Single JSON payload for orchestration planning.",
            "",
            json.dumps(payload, indent=2),
            "",
        ]
    )


def write_skills_catalog(payload: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_skills_summary(payload: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_summary_document(payload, output_path), encoding="utf-8")


# ----------------------------------------------------------------------------------------------------
def main() -> None:
    args        = parse_catalog_args()
    skills_root = Path(args.skills_root).resolve()
    output_json_path = Path(args.output_json).resolve()
    output_summary_path = Path(args.output_summary).resolve()

    skill_files = find_skill_files(skills_root=skills_root)
    if not skill_files:
        raise RuntimeError(f"No skill.md files found under {skills_root}")

    if not args.no_llm:
        ensure_ollama_running()

    payload = build_skills_payload(
        skills_root=skills_root,
        use_llm=not args.no_llm,
        model_name=args.model,
        num_ctx=args.num_ctx,
    )
    write_skills_catalog(payload, output_json_path)
    write_skills_summary(payload, output_summary_path)

    print(f"Wrote {to_workspace_relative_path(output_json_path)} with {len(payload['skills'])} skill summaries.")
    print(f"Wrote {to_workspace_relative_path(output_summary_path)} for human inspection.")


# ----------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    main()
