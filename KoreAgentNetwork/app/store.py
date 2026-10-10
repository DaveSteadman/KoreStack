from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .config import NETWORKS_DIR, RUN_STATE_DIR


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_NODE_KINDS = {"python", "llm", "judge"}
_WRITE_LOCK = threading.RLock()
_log = logging.getLogger(__name__)


class NetworkConflict(Exception):
    """Raised when a save is based on an out-of-date copy of the network."""


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file and replace so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return f"network_{uuid.uuid4().hex[:12]}"


def default_network() -> dict:
    """Return a small working example that also documents the node contract."""
    return {
        "koreAgentNetwork": "1.0",
        "id":              _new_id(),
        "title":           "KoreStack health flow",
        "created_at":      _utc_now(),
        "updated_at":      _utc_now(),
        "nodes": [
            {
                "id":       "agent_status",
                "label":    "Read agent status",
                "kind":     "python",
                "config":   {},
                "position": {"x": 90, "y": 130},
                "inputs":   [],
                "outputs":  [{"name": "status"}, {"name": "model"}],
                "code":     'status = api_get("koreagent", "/status")\noutputs["status"] = status\noutputs["model"] = status.get("runtime", {}).get("model")',
            },
            {
                "id":       "status_summary",
                "label":    "Create status summary",
                "kind":     "python",
                "config":   {},
                "position": {"x": 450, "y": 130},
                "inputs":   [{"name": "status", "default": {}}],
                "outputs":  [{"name": "summary"}],
                "code":     'runtime = inputs["status"].get("runtime", {})\noutputs["summary"] = {"model": runtime.get("model"), "system_one": runtime.get("system_one_model")}',
            },
        ],
        "edges": [
            {
                "id":          "edge_agent_status",
                "source_node": "agent_status",
                "source_port": "status",
                "target_node": "status_summary",
                "target_port": "status",
            },
        ],
    }


def _path(network_id: str) -> Path:
    if not _SAFE_ID.fullmatch(network_id):
        raise ValueError("Invalid network id")
    return (NETWORKS_DIR / f"{network_id}.json").resolve()


def _validate_port_list(value: object, kind: str, node_id: str) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError(f"Node {node_id} {kind} must be a list")
    result: list[dict] = []
    names: set[str]   = set()
    for port in value:
        if not isinstance(port, dict):
            raise ValueError(f"Node {node_id} has an invalid {kind} port")
        name = str(port.get("name") or "").strip()
        if not _SAFE_ID.fullmatch(name):
            raise ValueError(f"Node {node_id} has an invalid {kind} port name")
        if name in names:
            raise ValueError(f"Node {node_id} repeats {kind} port {name}")
        names.add(name)
        clean = {"name": name}
        if kind == "inputs" and "default" in port:
            clean["default"] = port["default"]
        result.append(clean)
    return result


def _validate_node_config(kind: str, config: object, node_id: str) -> dict:
    if config is None:
        data: dict[str, object] = {}
    elif isinstance(config, dict):
        data = deepcopy(config)
    else:
        raise ValueError(f"Node {node_id} config must be an object")
    if kind == "python":
        return {}
    if kind == "llm":
        return {
            "prompt_template": str(data.get("prompt_template") or ""),
            "model": str(data.get("model") or "").strip(),
        }
    threshold = data.get("threshold", 0.7)
    try:
        numeric_threshold = float(threshold)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Node {node_id} judge threshold must be numeric") from exc
    if numeric_threshold < 0 or numeric_threshold > 1:
        raise ValueError(f"Node {node_id} judge threshold must be between 0 and 1")
    return {
        "question": str(data.get("question") or ""),
        "threshold": numeric_threshold,
        "model": str(data.get("model") or "").strip(),
    }


def validate_node(node: object) -> dict:
    if not isinstance(node, dict):
        raise ValueError("Each node must be an object")
    node_id = str(node.get("id") or "").strip()
    if not _SAFE_ID.fullmatch(node_id):
        raise ValueError("Each node needs a unique safe id")
    kind = str(node.get("kind") or "python").strip().lower()
    if kind not in _NODE_KINDS:
        raise ValueError(f"Node {node_id} kind must be one of: {', '.join(sorted(_NODE_KINDS))}")
    position = node.get("position") if isinstance(node.get("position"), dict) else {}
    try:
        order = float(node["order"])
    except (KeyError, TypeError, ValueError):
        order = None
    result = {
        "id":       node_id,
        "label":    str(node.get("label") or node_id).strip() or node_id,
        "kind":     kind,
        "config":   _validate_node_config(kind, node.get("config"), node_id),
        "position": {"x": float(position.get("x") or 0), "y": float(position.get("y") or 0)},
        "inputs":   _validate_port_list(node.get("inputs", []), "inputs", node_id),
        "outputs":  _validate_port_list(node.get("outputs", []), "outputs", node_id),
        "code":     str(node.get("code") or ""),
    }
    if order is not None and order == order:
        result["order"] = int(order) if order == int(order) else order
    return result


def validate_network(network: object) -> dict:
    """Normalise and validate a serialisable network definition."""
    if not isinstance(network, dict):
        raise ValueError("Network must be a JSON object")
    result = deepcopy(network)
    result["koreAgentNetwork"] = "1.0"
    result["id"] = str(result.get("id") or _new_id()).strip()
    if not _SAFE_ID.fullmatch(result["id"]):
        raise ValueError("Network id must contain letters, numbers, _ or -")
    result["title"] = str(result.get("title") or "Untitled network").strip() or "Untitled network"
    result["protected"] = result.get("protected") is True

    nodes = result.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("Network nodes must be a list")
    node_ids: set[str] = set()
    cleaned_nodes: list[dict] = []
    for node in nodes:
        clean = validate_node(node)
        node_id = clean["id"]
        if node_id in node_ids:
            raise ValueError("Each node needs a unique safe id")
        node_ids.add(node_id)
        cleaned_nodes.append(clean)

    port_map = {
        node["id"]: {
            "inputs":  {port["name"] for port in node["inputs"]},
            "outputs": {port["name"] for port in node["outputs"]},
        }
        for node in cleaned_nodes
    }
    edges = result.get("edges")
    if not isinstance(edges, list):
        raise ValueError("Network edges must be a list")
    edge_ids: set[str] = set()
    destinations: set[tuple[str, str]] = set()
    cleaned_edges: list[dict] = []
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("Each connection must be an object")
        edge_id     = str(edge.get("id") or f"edge_{uuid.uuid4().hex[:10]}").strip()
        source_node = str(edge.get("source_node") or "").strip()
        source_port = str(edge.get("source_port") or "").strip()
        target_node = str(edge.get("target_node") or "").strip()
        target_port = str(edge.get("target_port") or "").strip()
        if not _SAFE_ID.fullmatch(edge_id) or edge_id in edge_ids:
            raise ValueError("Each connection needs a unique safe id")
        if source_node not in port_map or target_node not in port_map:
            raise ValueError("Connection refers to an unknown node")
        if source_port not in port_map[source_node]["outputs"]:
            raise ValueError("Connection refers to an unknown output port")
        if target_port not in port_map[target_node]["inputs"]:
            raise ValueError("Connection refers to an unknown input port")
        destination = (target_node, target_port)
        if destination in destinations:
            raise ValueError(f"Input {target_node}.{target_port} has more than one connection")
        edge_ids.add(edge_id)
        destinations.add(destination)
        cleaned_edges.append({
            "id":          edge_id,
            "source_node": source_node,
            "source_port": source_port,
            "target_node": target_node,
            "target_port": target_port,
        })

    triggers = result.get("triggers", [])
    if not isinstance(triggers, list):
        raise ValueError("Network triggers must be a list")
    cleaned_triggers: list[dict] = []
    trigger_names: set[tuple[str, str]] = set()
    graph: dict[str, set[str]] = {node_id: set() for node_id in port_map}
    for trigger in triggers:
        if not isinstance(trigger, dict):
            raise ValueError("Each trigger must be an object")
        name   = str(trigger.get("name") or "").strip()
        source = str(trigger.get("source_node") or "").strip()
        target = str(trigger.get("target_node") or "").strip()
        if not _SAFE_ID.fullmatch(name) or (source, name) in trigger_names:
            raise ValueError("Each trigger needs a unique name of letters, numbers, _ or -")
        if source not in port_map:
            raise ValueError(f"Trigger {name} refers to an unknown source block")
        if target and (target not in port_map or target == source):
            raise ValueError(f"Trigger {name} needs a different target block in this network")
        trigger_names.add((source, name))
        if target:
            graph[source].add(target)
        cleaned_triggers.append({"name": name, "source_node": source, "target_node": target})
    state: dict[str, int] = {}

    def visit(node_id: str) -> None:
        state[node_id] = 1
        for nxt in graph[node_id]:
            if state.get(nxt) == 1:
                raise ValueError("Triggers would create a loop (triggers form a loop)")
            if nxt not in state:
                visit(nxt)
        state[node_id] = 2

    for node_id in graph:
        if node_id not in state:
            visit(node_id)
    cleaned_triggers = [item for item in cleaned_triggers if item["target_node"]] + [item for item in cleaned_triggers if not item["target_node"]]
    result["nodes"]      = cleaned_nodes
    result["edges"]      = cleaned_edges
    result["triggers"]   = cleaned_triggers
    result["created_at"] = str(result.get("created_at") or _utc_now())
    result["updated_at"] = str(result.get("updated_at") or _utc_now())
    return result


def count_networks() -> int:
    return sum(1 for _ in NETWORKS_DIR.glob("*.json")) if NETWORKS_DIR.exists() else 0


def list_networks() -> list[dict]:
    NETWORKS_DIR.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        if not any(NETWORKS_DIR.glob("*.json")):
            save_network(default_network())
    networks: list[dict] = []
    for path in sorted(NETWORKS_DIR.glob("*.json")):
        try:
            network = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(network, dict) or not isinstance(network.get("id"), str):
                continue
            networks.append({
                "id":         network.get("id"),
                "title":      network.get("title"),
                "protected":  network.get("protected") is True,
                "updated_at": network.get("updated_at"),
                "node_count": len(network.get("nodes", [])),
            })
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("Skipping unreadable network file %s: %s", path.name, exc)
    # Protected networks lead the list; each group is alphabetical.
    networks.sort(key=lambda n: (not n["protected"], str(n.get("title") or "").casefold()))
    return networks


def load_network(network_id: str) -> dict:
    path = _path(network_id)
    if not path.exists():
        raise FileNotFoundError(network_id)
    return validate_network(json.loads(path.read_text(encoding="utf-8")))


def save_network(network: object, base_updated_at: str | None = None) -> dict:
    """Validate and save a network. A given base_updated_at must match the stored copy."""
    NETWORKS_DIR.mkdir(parents=True, exist_ok=True)
    clean = validate_network(network)
    with _WRITE_LOCK:
        try:
            stored = load_network(clean["id"])
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            stored = None
        if stored is not None and stored.get("protected") and clean["protected"]:
            raise NetworkConflict("This network is protected. Clear the padlock before changing it.")
        if base_updated_at is not None:
            current = stored.get("updated_at") if stored else None
            if current is not None and current != base_updated_at:
                raise NetworkConflict("This network was changed elsewhere. Reload the page to avoid overwriting it.")
        clean["updated_at"] = _utc_now()
        _atomic_write(_path(clean["id"]), json.dumps(clean, indent=2) + "\n")
    return clean


def _state_path(network_id: str) -> Path:
    if not _SAFE_ID.fullmatch(network_id):
        raise ValueError("Invalid network id")
    return RUN_STATE_DIR / f"{network_id}.json"


def load_run_state(network_id: str) -> dict:
    path = _state_path(network_id)
    if not path.exists():
        return {"run": None, "edits": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"run": None, "edits": {}}
    run, edits = data.get("run"), data.get("edits")
    return {"run": run if isinstance(run, dict) else None, "edits": edits if isinstance(edits, dict) else {}}


def save_run_state(network_id: str, run: object, edits: object) -> None:
    RUN_STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = {"run": run if isinstance(run, dict) else None, "edits": edits if isinstance(edits, dict) else {}}
    with _WRITE_LOCK:
        _atomic_write(_state_path(network_id), json.dumps(state))


def create_network(title: str = "Untitled network") -> dict:
    network = default_network()
    network["title"] = str(title or "Untitled network").strip() or "Untitled network"
    network["nodes"] = []
    network["edges"] = []
    return save_network(network)


def duplicate_network(network_id: str) -> dict:
    network = load_network(network_id)
    base    = re.sub(r"\s*\(\d+\)$", "", str(network.get("title") or "Untitled network")).strip() or "Untitled network"
    titles  = {str(n.get("title")) for n in list_networks()}
    number  = 2
    while f"{base} ({number})" in titles:
        number += 1
    network["id"]        = _new_id()
    network["title"]     = f"{base} ({number})"
    network["protected"] = False
    return save_network(network)


def delete_network(network_id: str) -> None:
    path = _path(network_id)
    if not path.exists():
        raise FileNotFoundError(network_id)
    with _WRITE_LOCK:
        try:
            if load_network(network_id).get("protected"):
                raise NetworkConflict("This network is protected. Clear the padlock before deleting it.")
        except (ValueError, json.JSONDecodeError):
            pass
        path.unlink(missing_ok=True)
        _state_path(network_id).unlink(missing_ok=True)
