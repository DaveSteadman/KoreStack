# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# KoreDeviceDriver skill for KoreAgent.
#
# Exposes first-class tools for listing, creating, editing, generating, and running
# KoreDeviceDriver entries through the KoreDeviceDriver HTTP service.
#
# Typical planner usage:
#   1. device_driver_generate_from_prompt(...)  -- draft a driver snippet from a natural-language prompt
#   2. device_driver_get(...)                   -- inspect the saved entry
#   3. device_driver_run(...)                   -- execute the current driver code and inspect the result
#
# Related modules:
#   - KoreDevice/KoreDeviceDriver/app/server.py -- backing HTTP API
#   - KoreDevice/KoreDeviceGateway/app/server.py -- UI routes that mirror the same operations
#   - skills_catalog_builder.py -- reads skill.md to build the runtime tool catalog
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import ast
import json
import textwrap
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_code_dir = str(Path(__file__).resolve().parents[3])
if _code_dir not in sys.path:
    sys.path.insert(0, _code_dir)

from llm_client import call_llm_chat        as _call_llm_chat
from llm_client import get_active_model     as _get_active_model
from llm_client import get_active_num_ctx   as _get_active_num_ctx
from utils.workspace_utils import get_suite_defaults_file


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_DEFAULT_HOST = "127.0.0.1"
_TIMEOUT_SEC  = 20
_MAX_RETRIES  = 3


# ====================================================================================================
# MARK: HELPERS
# ====================================================================================================
def _read_suite_config() -> dict:
    cfg_path = get_suite_defaults_file()
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _device_driver_base_url() -> str:
    raw      = _read_suite_config()
    network  = raw.get("network")  if isinstance(raw.get("network"),  dict) else {}
    services = raw.get("services") if isinstance(raw.get("services"), dict) else {}
    host     = str(network.get("host") or _DEFAULT_HOST).strip() or _DEFAULT_HOST
    port_cfg = services.get("koredevicedriver") if isinstance(services.get("koredevicedriver"), dict) else {}
    port     = port_cfg.get("port")
    if port is None:
        raise RuntimeError("Missing services.koredevicedriver.port in config/korestack_config.json")
    return f"http://{host}:{port}"


def _request_json(*, method: str, path: str, payload: dict | None = None) -> dict | list | str:
    base_url = _device_driver_base_url().rstrip("/")
    url      = f"{base_url}{path}"
    headers  = {"Accept": "application/json"}
    data     = None

    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(url=url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SEC) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"KoreDeviceDriver HTTP {exc.code}: {detail or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"KoreDeviceDriver unreachable at {base_url}: {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"KoreDeviceDriver request failed: {exc}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body


def _normalize_optional_text(value: str) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _normalize_optional_int(value: int) -> int | None:
    return int(value) if int(value) > 0 else None


def _build_driver_payload(
    *,
    display_name:      str            = "",
    vendor:            str            = "",
    protocol:          str            = "",
    transport_address: str            = "",
    poll_interval_sec: int            = 0,
    enabled:           bool           = False,
    description:       str            = "",
    python_snippet:    str            = "",
) -> dict:
    payload = {
        "display_name":      _normalize_optional_text(display_name),
        "vendor":            _normalize_optional_text(vendor),
        "protocol":          _normalize_optional_text(protocol),
        "transport_address": _normalize_optional_text(transport_address),
        "poll_interval_sec": _normalize_optional_int(poll_interval_sec),
        "enabled":           bool(enabled),
        "description":       _normalize_optional_text(description),
        "python_snippet":    _normalize_optional_text(python_snippet),
    }
    return payload


def _extract_code_block(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def _validate_python_snippet(python_snippet: str) -> str:
    snippet = _extract_code_block(python_snippet).rstrip() + "\n"
    try:
        tree = ast.parse(snippet, filename="driver_snippet.py")
    except SyntaxError as exc:
        raise RuntimeError(f"Python snippet syntax error at line {exc.lineno}: {exc.msg}") from exc

    has_read_driver = any(
        isinstance(node, ast.FunctionDef) and node.name == "read_driver"
        for node in tree.body
    )
    if not has_read_driver:
        raise RuntimeError("Python snippet must define read_driver(context)")
    return snippet


def _generate_driver_code(name: str, prompt: str) -> str:
    return _generate_driver_code_with_feedback(name=name, prompt=prompt)


def _generate_driver_code_with_feedback(
    name:           str,
    prompt:         str,
    previous_code:  str = "",
    run_result:     dict | None = None,
    attempt_number: int  = 1,
) -> str:
    model = _get_active_model()
    if not model:
        raise RuntimeError("No active model is configured for driver generation")

    num_ctx  = _get_active_num_ctx()
    feedback = ""
    if previous_code.strip():
        feedback = (
            "\nPrevious saved code failed when executed through the real KoreDeviceDriver service.\n"
            f"Attempt: {attempt_number}\n"
            "You must repair the code, not explain it.\n\n"
            "Previous code:\n"
            f"{previous_code.rstrip()}\n\n"
            "Observed run result:\n"
            f"{json.dumps(run_result or {}, indent=2, ensure_ascii=False)}\n"
        )
    messages = [
        {
            "role": "system",
            "content": (
                "Write Python code for a KoreDeviceDriver entry.\n"
                "Return only Python code, with no markdown fences or explanation.\n"
                "The code must define exactly one function named read_driver(context).\n"
                "The function should inspect context['driver'] for metadata and return a JSON-serializable dict.\n"
                "Use only Python standard library modules unless the task explicitly requires otherwise.\n"
                "Every module or symbol used by the snippet must be imported explicitly inside the snippet.\n"
                "Do not assume names like shutil, os, json, pathlib, subprocess, or datetime are already available.\n"
                "If you use shutil.disk_usage, include 'import shutil'.\n"
            ),
        },
        {
            "role": "user",
            "content": (
                f"Driver name: {name}\n"
                f"Task: {prompt}\n"
                f"{feedback}\n"
                "The code is only acceptable if the saved driver entry can be executed successfully through the service.\n"
                "Return code that handles errors cleanly and returns a meaningful JSON-serializable dict.\n\n"
                "Write the driver snippet now."
            ),
        },
    ]
    result  = _call_llm_chat(model_name=model, messages=messages, tools=None, num_ctx=num_ctx)
    snippet = _extract_code_block(result.response or "")
    if not snippet.strip():
        raise RuntimeError("Model returned an empty driver snippet")
    return _validate_python_snippet(snippet)


def _run_result_is_success(run_result: dict) -> bool:
    if not isinstance(run_result, dict):
        return False
    if not bool(run_result.get("ok")):
        return False

    result = run_result.get("result")
    if isinstance(result, dict):
        status_value = str(result.get("status") or "").strip().lower()
        if status_value in {"failed", "error", "exception"}:
            return False
        if result.get("error") not in (None, ""):
            return False
    return True


def _save_driver_entry(
    *,
    name:              str,
    display_name:      str,
    vendor:            str,
    protocol:          str,
    transport_address: str,
    poll_interval_sec: int,
    enabled:           bool,
    description:       str,
    python_snippet:    str,
) -> dict:
    try:
        return device_driver_create(
            name              = name,
            display_name      = display_name,
            vendor            = vendor,
            protocol          = protocol,
            transport_address = transport_address,
            poll_interval_sec = poll_interval_sec,
            enabled           = enabled,
            description       = description,
            python_snippet    = python_snippet,
        )
    except RuntimeError:
        return device_driver_update(
            name              = name,
            display_name      = display_name,
            vendor            = vendor,
            protocol          = protocol,
            transport_address = transport_address,
            poll_interval_sec = poll_interval_sec,
            enabled           = enabled,
            description       = description,
            python_snippet    = python_snippet,
        )


def _generate_saved_driver_until_valid(
    *,
    name:              str,
    prompt:            str,
    display_name:      str,
    vendor:            str,
    protocol:          str,
    transport_address: str,
    poll_interval_sec: int,
    enabled:           bool,
    description:       str,
    max_attempts:      int,
) -> dict:
    attempts: list[dict] = []
    snippet              = ""

    for attempt_number in range(1, max(1, max_attempts) + 1):
        snippet = _generate_driver_code_with_feedback(
            name           = name,
            prompt         = prompt,
            previous_code  = snippet,
            run_result     = attempts[-1]["run_result"] if attempts else None,
            attempt_number = attempt_number,
        )
        saved_entry = _save_driver_entry(
            name              = name,
            display_name      = display_name,
            vendor            = vendor,
            protocol          = protocol,
            transport_address = transport_address,
            poll_interval_sec = poll_interval_sec,
            enabled           = enabled,
            description       = description,
            python_snippet    = snippet,
        )
        run_result = device_driver_run(name=name)
        attempt    = {
            "attempt":        attempt_number,
            "generated_code": snippet,
            "saved_entry":    saved_entry,
            "run_result":     run_result,
            "success":        _run_result_is_success(run_result),
        }
        attempts.append(attempt)
        if attempt["success"]:
            return {
                "name":           name,
                "generated_code": snippet,
                "saved":          True,
                "saved_entry":    saved_entry,
                "run_result":     run_result,
                "validated":      True,
                "attempt_count":  attempt_number,
                "attempts":       attempts,
            }

    failure = attempts[-1]["run_result"] if attempts else {"ok": False, "error": "No attempts executed"}
    raise RuntimeError(
        "Generated driver code did not validate after "
        f"{max(1, max_attempts)} attempts.\n"
        f"Last run result:\n{textwrap.indent(json.dumps(failure, indent=2, ensure_ascii=False), '  ')}"
    )


# ====================================================================================================
# MARK: PUBLIC SKILL API
# ====================================================================================================
def device_driver_list() -> list[dict]:
    """Return every KoreDeviceDriver entry."""
    result = _request_json(method="GET", path="/api/drivers")
    return result if isinstance(result, list) else [result]


def device_driver_get(name: str) -> dict:
    """Return one KoreDeviceDriver entry by name."""
    if not str(name or "").strip():
        raise RuntimeError("Driver name is required")
    result = _request_json(method="GET", path=f"/api/drivers/{urllib.parse.quote(name.strip(), safe='')}")
    return result if isinstance(result, dict) else {"result": result}


def device_driver_create(
    name:              str,
    display_name:      str  = "",
    vendor:            str  = "",
    protocol:          str  = "",
    transport_address: str  = "",
    poll_interval_sec: int  = 0,
    enabled:           bool = False,
    description:       str  = "",
    python_snippet:    str  = "",
) -> dict:
    """Create a new KoreDeviceDriver entry."""
    clean_name = str(name or "").strip()
    if not clean_name:
        raise RuntimeError("Driver name is required")

    payload         = _build_driver_payload(
        display_name      = display_name,
        vendor            = vendor,
        protocol          = protocol,
        transport_address = transport_address,
        poll_interval_sec = poll_interval_sec,
        enabled           = enabled,
        description       = description,
        python_snippet    = _validate_python_snippet(python_snippet) if python_snippet.strip() else "",
    )
    payload["name"]  = clean_name
    result           = _request_json(method="POST", path="/api/drivers", payload=payload)
    return result if isinstance(result, dict) else {"result": result}


def device_driver_update(
    name:              str,
    display_name:      str  = "",
    vendor:            str  = "",
    protocol:          str  = "",
    transport_address: str  = "",
    poll_interval_sec: int  = 0,
    enabled:           bool = False,
    description:       str  = "",
    python_snippet:    str  = "",
) -> dict:
    """Update an existing KoreDeviceDriver entry."""
    clean_name = str(name or "").strip()
    if not clean_name:
        raise RuntimeError("Driver name is required")

    payload = _build_driver_payload(
        display_name      = display_name,
        vendor            = vendor,
        protocol          = protocol,
        transport_address = transport_address,
        poll_interval_sec = poll_interval_sec,
        enabled           = enabled,
        description       = description,
        python_snippet    = _validate_python_snippet(python_snippet) if python_snippet.strip() else "",
    )
    result  = _request_json(
        method  = "PUT",
        path    = f"/api/drivers/{urllib.parse.quote(clean_name, safe='')}",
        payload = payload,
    )
    return result if isinstance(result, dict) else {"result": result}


def device_driver_set_code(name: str, python_snippet: str) -> dict:
    """Replace only the Python snippet for an existing driver entry."""
    existing = device_driver_get(name)
    return device_driver_update(
        name              = existing.get("name", name),
        display_name      = str(existing.get("display_name")      or ""),
        vendor            = str(existing.get("vendor")            or ""),
        protocol          = str(existing.get("protocol")          or ""),
        transport_address = str(existing.get("transport_address") or ""),
        poll_interval_sec = int(existing.get("poll_interval_sec") or 0),
        enabled           = bool(existing.get("enabled")),
        description       = str(existing.get("description")       or ""),
        python_snippet    = python_snippet,
    )


def device_driver_run(
    name:              str,
    display_name:      str  = "",
    vendor:            str  = "",
    protocol:          str  = "",
    transport_address: str  = "",
    poll_interval_sec: int  = 0,
    enabled:           bool = False,
    description:       str  = "",
    python_snippet:    str  = "",
) -> dict:
    """Run a KoreDeviceDriver entry using either the saved or supplied code."""
    clean_name = str(name or "").strip()
    if not clean_name:
        raise RuntimeError("Driver name is required")

    payload = _build_driver_payload(
        display_name      = display_name,
        vendor            = vendor,
        protocol          = protocol,
        transport_address = transport_address,
        poll_interval_sec = poll_interval_sec,
        enabled           = enabled,
        description       = description,
        python_snippet    = _validate_python_snippet(python_snippet) if python_snippet.strip() else "",
    )
    result = _request_json(
        method  = "POST",
        path    = f"/api/drivers/{urllib.parse.quote(clean_name, safe='')}/run",
        payload = payload,
    )
    return result if isinstance(result, dict) else {"result": result}


def device_driver_generate_from_prompt(
    name:              str,
    prompt:            str,
    display_name:      str  = "",
    vendor:            str  = "",
    protocol:          str  = "",
    transport_address: str  = "",
    poll_interval_sec: int  = 0,
    enabled:           bool = False,
    description:       str  = "",
    save:              bool = True,
    run_after_generate: bool = False,
    max_attempts:      int  = _MAX_RETRIES,
) -> dict:
    """Generate a driver snippet and validate the saved entry on the real service path."""
    clean_name   = str(name or "").strip()
    clean_prompt = str(prompt or "").strip()
    if not clean_name:
        raise RuntimeError("Driver name is required")
    if not clean_prompt:
        raise RuntimeError("Generation prompt is required")

    if save:
        return _generate_saved_driver_until_valid(
            name              = clean_name,
            prompt            = clean_prompt,
            display_name      = display_name,
            vendor            = vendor,
            protocol          = protocol,
            transport_address = transport_address,
            poll_interval_sec = poll_interval_sec,
            enabled           = enabled,
            description       = description or clean_prompt,
            max_attempts      = max_attempts,
        )

    snippet = _generate_driver_code(clean_name, clean_prompt)
    result  = {
        "name":           clean_name,
        "generated_code": snippet,
        "saved":          False,
        "run_result":     None,
        "validated":      False,
        "attempt_count":  1,
    }

    if run_after_generate:
        result["run_result"] = device_driver_run(
            name              = clean_name,
            display_name      = display_name,
            vendor            = vendor,
            protocol          = protocol,
            transport_address = transport_address,
            poll_interval_sec = poll_interval_sec,
            enabled           = enabled,
            description       = description or clean_prompt,
            python_snippet    = snippet,
        )

    return result
