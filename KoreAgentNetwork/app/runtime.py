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

from .config import NODE_TIMEOUT_SECONDS, RUN_TIMEOUT_SECONDS, suite_services


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

def _url(service, path):
    if service not in services:
        raise ValueError("Unknown KoreStack service: " + str(service))
    suffix = str(path or "")
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return services[service].rstrip("/") + suffix

def _call(request, timeout):
    try:
        with urllib.request.urlopen(request, timeout=min(max(float(timeout), 1), 30)) as response:
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
    return api_post("koreagent", "/api/work-packet", {"json_text": json.dumps(packet)}, timeout=min(max(float(timeout), 1), 120))

def llm(prompt, model="", timeout=60):
    return llm_result(prompt, model=model, timeout=timeout)["response"]

def decide(question, state=None, model="", timeout=60):
    payload = {"route": "system_one", "state": inputs if state is None else state, "questions": {"verdict": {"type": "noul", "instructions": str(question)}}}
    if str(model).strip():
        payload["model"] = str(model).strip()
    result = api_post("koreagent", "/api/work-packet", {"json_text": json.dumps(payload)}, timeout=min(max(float(timeout), 1), 120))
    answers = json.loads(result["response"])
    return float(answers["verdict"]["noul"])

judge = decide

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
safe_builtins = {
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
    print(json.dumps({"ok": True, "outputs": outputs}, default=str))
except Exception as exc:
    print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
'''

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_-]+)\}")


def _execution_order(network: dict) -> list[str]:
    node_ids = [node["id"] for node in network["nodes"]]
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, int] = {node_id: 0 for node_id in node_ids}
    for edge in network["edges"]:
        outgoing[edge["source_node"]].append(edge["target_node"])
        incoming[edge["target_node"]] += 1
    ready = deque(node_id for node_id in node_ids if incoming[node_id] == 0)
    ordered: list[str] = []
    while ready:
        node_id = ready.popleft()
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


def _execute_node(node: dict, inputs: dict, timeout: float) -> dict:
    kind = str(node.get("kind") or "python").strip().lower()
    if kind == "llm":
        return _execute_llm_node(node, inputs, timeout)
    if kind == "judge":
        return _execute_judge_node(node, inputs, timeout)
    root = get_datauser_root()
    root.mkdir(parents=True, exist_ok=True)
    payload = {"code": node["code"], "inputs": inputs, "services": suite_services(), "root": str(root)}
    completed = subprocess.run(
        [sys.executable, "-I", "-c", _RUNNER],
        input           = json.dumps(payload),
        text            = True,
        capture_output  = True,
        timeout         = max(1, timeout),
        cwd             = root,
        env             = _child_environment(),
        check           = False,
    )
    try:
        result = json.loads(completed.stdout.strip())
    except json.JSONDecodeError:
        return {"ok": False, "error": (completed.stderr or completed.stdout or "Node produced no JSON result").strip()}
    if completed.returncode != 0:
        return {"ok": False, "error": (completed.stderr or "Python runner failed").strip()}
    return result if isinstance(result, dict) else {"ok": False, "error": "Node returned an invalid result"}


def run_node(node: dict, inputs: dict) -> dict[str, Any]:
    """Execute one block against explicit input values and return its result record."""
    started = time.monotonic()
    try:
        result = _execute_node(node, inputs, NODE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        result = {"ok": False, "error": f"Node exceeded its {NODE_TIMEOUT_SECONDS:.0f}s limit"}
    record = {"inputs": inputs, "elapsed_seconds": round(time.monotonic() - started, 3)}
    outputs = result.get("outputs") if result.get("ok") else None
    if not result.get("ok"):
        return {**record, "status": "failed", "error": str(result.get("error") or "Node failed")}
    if not isinstance(outputs, dict):
        return {**record, "status": "failed", "error": "Node did not return an outputs dictionary"}
    missing = [port["name"] for port in node["outputs"] if port["name"] not in outputs]
    if missing:
        return {**record, "status": "failed", "error": f"Node did not set declared outputs: {', '.join(missing)}"}
    return {**record, "status": "completed", "outputs": outputs}


def run_network(network: dict) -> dict[str, Any]:
    """Execute a validated acyclic network in dependency order."""
    started  = time.monotonic()
    order    = _execution_order(network)
    nodes    = {node["id"]: node for node in network["nodes"]}
    incoming: dict[str, list[dict]] = defaultdict(list)
    for edge in network["edges"]:
        incoming[edge["target_node"]].append(edge)

    results: dict[str, dict] = {}
    outputs: dict[str, dict] = {}
    for node_id in order:
        elapsed   = time.monotonic() - started
        remaining = RUN_TIMEOUT_SECONDS - elapsed
        if remaining <= 0:
            results[node_id] = {"status": "skipped", "error": "Network time limit reached"}
            continue
        node = nodes[node_id]
        blocked = [edge["source_node"] for edge in incoming[node_id] if results.get(edge["source_node"], {}).get("status") != "completed"]
        if blocked:
            results[node_id] = {"status": "blocked", "error": f"Upstream node did not complete: {', '.join(blocked)}"}
            continue
        node_started = time.monotonic()
        node_inputs  = _node_inputs(node, incoming, outputs)
        try:
            result = _execute_node(node, node_inputs, min(NODE_TIMEOUT_SECONDS, remaining))
        except subprocess.TimeoutExpired:
            result = {"ok": False, "error": f"Node exceeded its {min(NODE_TIMEOUT_SECONDS, remaining):.0f}s limit"}
        elapsed_node = round(time.monotonic() - node_started, 3)
        results[node_id] = {"inputs": node_inputs}
        if result.get("ok"):
            node_outputs = result.get("outputs")
            if not isinstance(node_outputs, dict):
                results[node_id].update(status="failed", error="Node did not return an outputs dictionary", elapsed_seconds=elapsed_node)
                continue
            missing = [port["name"] for port in node["outputs"] if port["name"] not in node_outputs]
            if missing:
                results[node_id].update(status="failed", error=f"Node did not set declared outputs: {', '.join(missing)}", elapsed_seconds=elapsed_node)
                continue
            outputs[node_id] = node_outputs
            results[node_id].update(status="completed", outputs=node_outputs, elapsed_seconds=elapsed_node)
        else:
            results[node_id].update(status="failed", error=str(result.get("error") or "Node failed"), elapsed_seconds=elapsed_node)
    return {
        "ok":              all(result.get("status") == "completed" for result in results.values()),
        "network_id":      network["id"],
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "order":           order,
        "nodes":           results,
    }
