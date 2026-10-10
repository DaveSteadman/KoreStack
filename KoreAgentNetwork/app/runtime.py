from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from typing import Any

from KoreCommon.datauser_fs import get_datauser_root

import threading
import uuid

from .config import NODE_TIMEOUT_SECONDS, RUN_TIMEOUT_SECONDS, service_config, suite_services


_RUNNER = r'''
import ast
import json
import math
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

payload = json.loads(sys.stdin.read())
services = payload["services"]
payload_info = {"trigger": payload.get("trigger")}

def _url(service, path):
    if service not in services:
        raise ValueError("Unknown KoreStack service: " + str(service))
    suffix = str(path or "")
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return services[service].rstrip("/") + suffix

def _call(request, timeout):
    try:
        with urllib.request.urlopen(request, timeout=min(max(float(timeout), 1), 180)) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError("HTTP " + str(exc.code) + " from " + request.full_url + ": " + detail) from None

def api_get(service, path, timeout=20):
    return _call(urllib.request.Request(_url(service, path), method="GET"), timeout)

def api_post(service, path, body=None, timeout=20):
    encoded = json.dumps(body if body is not None else {}).encode("utf-8")
    request = urllib.request.Request(_url(service, path), data=encoded, method="POST", headers={"Content-Type": "application/json"})
    return _call(request, timeout)

def llm_result(prompt, model="", timeout=60):
    packet = {"route": "llm", "prompt": str(prompt)}
    if str(model).strip():
        packet["model"] = str(model).strip()
    return api_post("koreagent", "/api/work-packet", {"json_text": json.dumps(packet)}, timeout=min(max(float(timeout), 1), 180))

def llm(prompt, model="", timeout=60):
    return llm_result(prompt, model=model, timeout=timeout)["response"]

def decide(question, state=None, model="", timeout=60):
    payload = {"route": "system_one", "state": inputs if state is None else state, "questions": {"verdict": {"type": "noul", "instructions": str(question)}}}
    if str(model).strip():
        payload["model"] = str(model).strip()
    result = api_post("koreagent", "/api/work-packet", {"json_text": json.dumps(payload)}, timeout=min(max(float(timeout), 1), 180))
    answers = json.loads(result["response"])
    return float(answers["verdict"]["noul"])

judge = decide

_skip_auto = {"all": False, "names": []}

def disable_auto_trigger(name=None):
    if name is None:
        _skip_auto["all"] = True
    else:
        _skip_auto["names"].append(str(name))

def trigger(name, payload=None):
    info = payload_info.get("trigger")
    if not info:
        raise RuntimeError("trigger() is only available when the block runs inside a saved network")
    body = json.dumps({"token": info["token"], "name": str(name), "payload": payload if payload is not None else {}}).encode("utf-8")
    request = urllib.request.Request(info["url"], data=body, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=None) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError("trigger " + str(name) + " failed: " + exc.read().decode("utf-8", "replace")[:500]) from None

import pathlib

ROOT = pathlib.Path(payload["root"]).resolve()

def _path(name):
    target = (ROOT / str(name)).resolve()
    if target != ROOT and ROOT not in target.parents:
        raise ValueError("Path escapes the datauser folder: " + str(name))
    return target

def safe_open(name, mode="r", encoding="utf-8", **kwargs):
    target = _path(name)
    if any(flag in mode for flag in "wax+"):
        target.parent.mkdir(parents=True, exist_ok=True)
    if "b" in mode:
        encoding = None
    return open(target, mode, encoding=encoding, **kwargs)

def read_text(name):
    return _path(name).read_text(encoding="utf-8-sig")

def write_text(name, text):
    target = _path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(text), encoding="utf-8")

def append_text(name, text):
    with safe_open(name, "a") as handle:
        handle.write(str(text))

def read_json(name, default=None):
    target = _path(name)
    if not target.exists() and default is not None:
        return default
    return json.loads(target.read_text(encoding="utf-8-sig"))

def write_json(name, value):
    write_text(name, json.dumps(value, indent=2, ensure_ascii=False))

def exists(name):
    return _path(name).exists()

def list_files(pattern="*", folder="."):
    base = _path(folder)
    return sorted(str(item.relative_to(ROOT)).replace("\\", "/") for item in base.glob(pattern) if item.is_file())

def delete_file(name):
    target = _path(name)
    if target.is_file():
        target.unlink()
        return True
    return False
ALLOWED_IMPORTS = {
    "datetime", "time", "calendar", "zoneinfo", "collections", "itertools", "functools", "operator",
    "statistics", "string", "textwrap", "html", "urllib.parse", "base64", "hashlib", "random", "decimal",
    "fractions", "csv", "uuid", "difflib", "copy", "heapq", "bisect", "enum", "dataclasses", "typing",
    "json", "math", "re", "unicodedata", "secrets", "numbers",
}
def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level != 0 or (name not in ALLOWED_IMPORTS and name.split(".")[0] not in ALLOWED_IMPORTS - {"urllib.parse"}):
        raise ImportError(f"import of '{name}' is not allowed; allowed modules: " + ", ".join(sorted(ALLOWED_IMPORTS)))
    return __import__(name, globals, locals, fromlist, level)
safe_builtins = {
    "__import__": safe_import,
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict, "enumerate": enumerate,
    "Exception": Exception, "TypeError": TypeError, "ValueError": ValueError,
    "filter": filter, "float": float, "int": int, "isinstance": isinstance, "len": len,
    "list": list, "map": map, "max": max, "min": min, "next": next, "iter": iter, "reversed": reversed,
    "range": range, "round": round, "repr": repr, "KeyError": KeyError, "IndexError": IndexError,
    "RuntimeError": RuntimeError, "StopIteration": StopIteration,
    "set": set, "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "zip": zip,
}
namespace = {
    "__builtins__": safe_builtins,
    "api_get": api_get,
    "open": safe_open, "read_text": read_text, "write_text": write_text, "append_text": append_text,
    "read_json": read_json, "write_json": write_json, "exists": exists, "list_files": list_files, "delete_file": delete_file,
    "api_post": api_post,
    "trigger": trigger,
    "disable_auto_trigger": disable_auto_trigger,
    "decide": decide,
    "inputs": payload["inputs"],
    "judge": judge,
    "llm": llm,
    "llm_result": llm_result,
    "outputs": {},
    "NoValue": None,
    "json": json,
    "math": math,
    "re": re,
}
try:
    tree = ast.parse(payload["code"], "<KoreAgentNetwork node>")
    wrapper = ast.parse("def __node__():\n    pass")
    wrapper.body[0].body = tree.body or [ast.Pass()]
    ast.fix_missing_locations(wrapper)
    exec(compile(wrapper, "<KoreAgentNetwork node>", "exec"), namespace, namespace)
    namespace["__node__"]()
    outputs = namespace["outputs"]
    if not isinstance(outputs, dict):
        raise TypeError("outputs must remain a dictionary")
    print(json.dumps({"ok": True, "outputs": outputs, "skip_triggers": True if _skip_auto["all"] else _skip_auto["names"]}, default=str))
except Exception as exc:
    print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
'''

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_-]+)\}")


def _execution_order(network: dict) -> list[str]:
    node_ids = [node["id"] for node in network["nodes"]]
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, int] = {node_id: 0 for node_id in node_ids}
    links = [(edge["source_node"], edge["target_node"]) for edge in network["edges"]]
    for source, target in links:
        outgoing[source].append(target)
        incoming[target] += 1
    rank = {node["id"]: (node["order"] if node.get("order") is not None else float("inf"), index) for index, node in enumerate(network["nodes"])}
    ready = [node_id for node_id in node_ids if incoming[node_id] == 0]
    ordered: list[str] = []
    while ready:
        ready.sort(key=rank.__getitem__)
        node_id = ready.pop(0)
        ordered.append(node_id)
        for target in outgoing[node_id]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
    if len(ordered) != len(node_ids):
        raise ValueError("Network contains a cycle; execution requires a directed acyclic graph")
    return ordered


def _node_inputs(node: dict, incoming: dict[str, list[dict]], outputs: dict[str, dict]) -> dict:
    values = {port["name"]: port.get("default") for port in node["inputs"]}
    for edge in incoming[node["id"]]:
        if edge["source_node"] in outputs:
            values[edge["target_port"]] = outputs[edge["source_node"]].get(edge["source_port"])
    return values


def _child_environment() -> dict[str, str]:
    retained = ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "HOME", "USERPROFILE", "TMP", "TEMP")
    environment = {key: os.environ[key] for key in retained if os.environ.get(key)}
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


def _render_template(template: str, values: dict[str, Any]) -> str:
    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise ValueError(f"Missing input value for {{{name}}}")
        value = values[name]
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)
    return _PLACEHOLDER_RE.sub(_replace, str(template or ""))


def _service_url(service: str, path: str) -> str:
    services = suite_services()
    if service not in services:
        raise ValueError(f"Unknown KoreStack service: {service}")
    suffix = str(path or "")
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return services[service].rstrip("/") + suffix


def _post_service_json(service: str, path: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    encoded = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        _service_url(service, path),
        data=encoded,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=max(1, min(float(timeout), 120))) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"{service} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{service} is unavailable: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{service} returned an invalid response")
    return payload


def _call_llm(prompt: str, model: str, timeout: float) -> dict[str, Any]:
    packet: dict[str, Any] = {"route": "llm", "prompt": prompt}
    if model:
        packet["model"] = model
    return _post_service_json("koreagent", "/api/work-packet", {"json_text": json.dumps(packet)}, timeout)


def _call_decision(question: str, state: dict[str, Any], model: str, timeout: float) -> dict[str, Any]:
    packet: dict[str, Any] = {
        "route": "system_one",
        "state": state,
        "questions": {"verdict": {"type": "noul", "instructions": question}},
    }
    if model:
        packet["model"] = model
    result = _post_service_json("koreagent", "/api/work-packet", {"json_text": json.dumps(packet)}, timeout)
    answers = json.loads(str(result.get("response") or "{}"))
    verdict = answers.get("verdict") if isinstance(answers, dict) else None
    probability = float(verdict["noul"]) if isinstance(verdict, dict) and "noul" in verdict else None
    if probability is None:
        raise RuntimeError("System One response did not include verdict.noul")
    return {
        "probability": probability,
        "model": result.get("model"),
        "response": answers,
    }


def _execute_llm_node(node: dict, inputs: dict, timeout: float) -> dict[str, Any]:
    config = node.get("config") or {}
    prompt = _render_template(str(config.get("prompt_template") or ""), inputs)
    result = _call_llm(prompt, str(config.get("model") or "").strip(), timeout)
    return {
        "ok": True,
        "outputs": {
            "response": result.get("response"),
            "prompt": prompt,
            "model": result.get("model"),
            "prompt_tokens": result.get("prompt_tokens"),
            "completion_tokens": result.get("completion_tokens"),
            "tokens_per_second": result.get("tokens_per_second"),
        },
    }


def _execute_judge_node(node: dict, inputs: dict, timeout: float) -> dict[str, Any]:
    config = node.get("config") or {}
    question = _render_template(str(config.get("question") or ""), inputs)
    result = _call_decision(question, inputs, str(config.get("model") or "").strip(), timeout)
    probability = float(result["probability"])
    threshold = float(config.get("threshold", 0.7))
    return {
        "ok": True,
        "outputs": {
            "verdict": probability >= threshold,
            "probability": probability,
            "threshold": threshold,
            "question": question,
            "model": result.get("model"),
        },
    }


MAX_TRIGGER_CALLS = 50
_REGISTRY_LOCK = threading.Lock()
_CONTEXTS: dict[str, dict] = {}
_LOCKS: dict[str, int] = {}


class NetworkBusy(RuntimeError):
    """Raised when a network is already running."""


def is_locked(network_id: str) -> bool:
    with _REGISTRY_LOCK:
        return _LOCKS.get(network_id, 0) > 0


def _acquire(network_id: str) -> None:
    with _REGISTRY_LOCK:
        if network_id and _LOCKS.get(network_id, 0) > 0:
            raise NetworkBusy("This network is already running. Wait for it to finish.")
        _LOCKS[network_id] = _LOCKS.get(network_id, 0) + 1


def _release(network_id: str) -> None:
    with _REGISTRY_LOCK:
        _LOCKS[network_id] = _LOCKS.get(network_id, 1) - 1
        if _LOCKS[network_id] <= 0:
            del _LOCKS[network_id]


def _trigger_url() -> str:
    config = service_config()
    return f"http://{config['host']}:{config['port']}/api/internal/trigger"


def triggered_blocks(network: dict) -> set[str]:
    """Blocks that are trigger targets. They run only when a trigger reaches them."""
    return {item["target_node"] for item in network.get("triggers", []) if item.get("target_node")}


def _new_session(network: dict, outputs: dict | None = None) -> dict:
    return {"network": network, "outputs": dict(outputs or {}), "results": {}, "deadline": time.monotonic() + RUN_TIMEOUT_SECONDS}


def _flow_timeout(session: dict) -> float:
    """Per-block time limit, capped by what is left of the session's overall deadline."""
    remaining = session["deadline"] - time.monotonic()
    if remaining <= 0:
        raise ValueError("Network time limit reached")
    return min(NODE_TIMEOUT_SECONDS, remaining)


def _child_seconds(context: dict | None) -> float:
    """Seconds spent inside triggered flows, including flows still running."""
    if context is None:
        return 0.0
    now = time.monotonic()
    with context["lock"]:
        return context["child_seconds"] + sum(now - start for start in context["active"])


def _new_context(session: dict, node: dict) -> dict | None:
    if not any(item["source_node"] == node["id"] for item in session["network"].get("triggers", [])):
        return None
    return {"session": session, "node_id": node["id"], "child_seconds": 0.0, "active": [], "calls": [], "lock": threading.Lock()}


def _run_block(session: dict, node: dict, node_inputs: dict, timeout: float) -> tuple[dict, float]:
    """Run one block inside a session, recording its result and outputs. Returns (record, child seconds)."""
    context = _new_context(session, node)
    started = time.monotonic()
    try:
        result = _execute_node(node, node_inputs, timeout, context)
    except subprocess.TimeoutExpired:
        result = {"ok": False, "error": f"Node exceeded its {timeout:.0f}s limit"}
    except ValueError as exc:
        result = {"ok": False, "error": str(exc)}
    record: dict[str, Any] = {"inputs": node_inputs, "elapsed_seconds": round(time.monotonic() - started, 3)}
    if context is not None and context["calls"]:
        record["triggers"] = context["calls"]
    outputs = result.get("outputs") if result.get("ok") else None
    record["_skip"] = result.get("skip_triggers") or []
    missing = [port["name"] for port in node["outputs"] if isinstance(outputs, dict) and port["name"] not in outputs]
    if not result.get("ok"):
        record.update(status="failed", error=str(result.get("error") or "Node failed"))
    elif not isinstance(outputs, dict):
        record.update(status="failed", error="Node did not return an outputs dictionary")
    elif missing:
        record.update(status="failed", error=f"Node did not set declared outputs: {', '.join(missing)}")
    else:
        record.update(status="completed", outputs=outputs)
        session["outputs"][node["id"]] = outputs
    session["results"][node["id"]] = record
    return record, _child_seconds(context)


def _blocked_by(session: dict, node_id: str, incoming: dict[str, list[dict]]) -> list[str]:
    return [edge["source_node"] for edge in incoming[node_id] if session["results"].get(edge["source_node"], {}).get("status") != "completed"]


def _incoming(network: dict) -> dict[str, list[dict]]:
    incoming: dict[str, list[dict]] = defaultdict(list)
    for edge in network["edges"]:
        incoming[edge["target_node"]].append(edge)
    return incoming


def _run_flow(session: dict, node_id: str, payload: dict | None, timeout: float, ran: list[str]) -> float:
    """Run a block, then fire its triggers that the block did not already fire or disable. Returns child seconds."""
    network  = session["network"]
    node     = next(item for item in network["nodes"] if item["id"] == node_id)
    incoming = _incoming(network)
    node_inputs = _node_inputs(node, incoming, session["outputs"])
    unknown = sorted(set(payload or {}) - set(node_inputs))
    if unknown:
        raise ValueError(f"Block {node['label']} has no input named: {', '.join(unknown)}")
    node_inputs.update(payload or {})
    session["results"].pop(node_id, None)
    session["outputs"].pop(node_id, None)
    record, child_seconds = _run_block(session, node, node_inputs, timeout)
    skip = record.pop("_skip", [])
    ran.append(node_id)
    if record.get("status") != "completed" or skip is True:
        return child_seconds
    fired = {call["name"] for call in record.get("triggers", [])}
    for trigger in network.get("triggers", []):
        if trigger["source_node"] != node_id or not trigger.get("target_node") or trigger["name"] in fired or trigger["name"] in skip:
            continue
        try:
            limit = _flow_timeout(session)
        except ValueError as exc:
            session["results"][trigger["target_node"]] = {"status": "skipped", "error": str(exc)}
            continue
        _run_flow(session, trigger["target_node"], None, limit, ran)
    return child_seconds


def fire_trigger(token: str, name: str, payload: dict) -> dict[str, Any]:
    """Run the block a trigger points at (and its own auto triggers), blocking until the flow finishes."""
    with _REGISTRY_LOCK:
        context = _CONTEXTS.get(token)
    if context is None:
        raise ValueError("Unknown or finished run token")
    session = context["session"]
    network = session["network"]
    trigger = next((item for item in network.get("triggers", []) if item["name"] == name and item["source_node"] == context["node_id"]), None)
    if trigger is None or not trigger.get("target_node"):
        raise ValueError(f"This block has no connected trigger named {name}")
    if not isinstance(payload, dict):
        raise ValueError("Trigger payload must be an object")
    limit = _flow_timeout(session)
    started = time.monotonic()
    with context["lock"]:
        if len(context["calls"]) + len(context["active"]) >= MAX_TRIGGER_CALLS:
            raise ValueError(f"A block may fire at most {MAX_TRIGGER_CALLS} triggers per run")
        context["active"].append(started)
    nodes = {node["id"]: node for node in network["nodes"]}
    ran: list[str] = []
    try:
        _run_flow(session, trigger["target_node"], payload, limit, ran)
    finally:
        elapsed = time.monotonic() - started
        with context["lock"]:
            context["active"].remove(started)
            context["child_seconds"] += elapsed
    elapsed = round(elapsed, 3)
    failed = next((session["results"].get(node_id, {}) for node_id in ran if session["results"].get(node_id, {}).get("status") != "completed"), None)
    with context["lock"]:
        context["calls"].append({"name": name, "target": trigger["target_node"], "ok": failed is None, "elapsed_seconds": elapsed})
    return {
        "ok":      failed is None,
        "status":  "completed" if failed is None else "failed",
        "error":   None if failed is None else failed.get("error"),
        "outputs": session["outputs"].get(trigger["target_node"], {}),
        "blocks":  {nodes[node_id]["label"]: session["outputs"][node_id] for node_id in ran if node_id in session["outputs"]},
    }


def _execute_node(node: dict, inputs: dict, timeout: float, context: dict | None = None) -> dict:
    kind = str(node.get("kind") or "python").strip().lower()
    if kind == "llm":
        return _execute_llm_node(node, inputs, timeout)
    if kind == "judge":
        return _execute_judge_node(node, inputs, timeout)
    root = get_datauser_root()
    root.mkdir(parents=True, exist_ok=True)
    payload = {"code": node["code"], "inputs": inputs, "services": suite_services(), "root": str(root)}
    token = None
    if context is not None:
        token = uuid.uuid4().hex
        payload["trigger"] = {"url": _trigger_url(), "token": token}
        with _REGISTRY_LOCK:
            _CONTEXTS[token] = context
    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", _RUNNER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=root, env=_child_environment(),
    )
    pending: str | None = json.dumps(payload)
    try:
        while True:
            try:
                stdout, stderr = process.communicate(input=pending, timeout=1)
                break
            except subprocess.TimeoutExpired:
                pending = None
                if time.monotonic() - started - _child_seconds(context) > max(1, timeout):
                    process.kill()
                    process.communicate()
                    raise
    finally:
        if token is not None:
            with _REGISTRY_LOCK:
                _CONTEXTS.pop(token, None)
    try:
        result = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return {"ok": False, "error": (stderr or stdout or "Node produced no JSON result").strip()}
    if process.returncode != 0:
        return {"ok": False, "error": (stderr or "Python runner failed").strip()}
    return result if isinstance(result, dict) else {"ok": False, "error": "Node returned an invalid result"}


def run_node(node: dict, inputs: dict, network: dict | None = None, outputs: dict | None = None) -> dict[str, Any]:
    """Execute one block against explicit input values and return its result record.

    With a network and the current block outputs, the block may fire its triggers; the
    records of the triggered blocks come back under `triggered`.
    """
    session = _new_session(network or {"id": "", "nodes": [], "edges": [], "triggers": []}, outputs)
    network_id = session["network"]["id"]
    has_triggers = any(item["source_node"] == node["id"] for item in session["network"].get("triggers", []))
    if has_triggers:
        _acquire(network_id)
    try:
        if has_triggers:
            _run_flow(session, node["id"], None, _flow_timeout(session), [])
            record = session["results"][node["id"]]
        else:
            record, _ = _run_block(session, node, inputs, _flow_timeout(session))
            record.pop("_skip", None)
    finally:
        if has_triggers:
            _release(network_id)
    triggered = {key: value for key, value in session["results"].items() if key != node["id"]}
    if triggered:
        record["triggered"] = triggered
    return record


def run_network(network: dict) -> dict[str, Any]:
    """Execute a validated network in dependency order. Triggered chains only run when fired."""
    _acquire(network["id"])
    try:
        return _run_network(network)
    finally:
        _release(network["id"])


def _run_network(network: dict) -> dict[str, Any]:
    started  = time.monotonic()
    excluded = 0.0
    owned    = triggered_blocks(network)
    order    = [node_id for node_id in _execution_order(network) if node_id not in owned]
    nodes    = {node["id"]: node for node in network["nodes"]}
    incoming = _incoming(network)
    session  = _new_session(network)
    results  = session["results"]
    session["deadline"] = started + RUN_TIMEOUT_SECONDS
    for node_id in order:
        remaining = RUN_TIMEOUT_SECONDS - (time.monotonic() - started - excluded)
        if remaining <= 0:
            results[node_id] = {"status": "skipped", "error": "Network time limit reached"}
            continue
        blocked = _blocked_by(session, node_id, incoming)
        if blocked:
            results[node_id] = {"status": "blocked", "error": f"Upstream node did not complete: {', '.join(blocked)}"}
            continue
        session["deadline"] = started + excluded + RUN_TIMEOUT_SECONDS
        excluded += _run_flow(session, node_id, None, min(NODE_TIMEOUT_SECONDS, remaining), [])
    return {
        "ok":              all(result.get("status") == "completed" for result in results.values()),
        "network_id":      network["id"],
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "order":           order,
        "nodes":           results,
    }