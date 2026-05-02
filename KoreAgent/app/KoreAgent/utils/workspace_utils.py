# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared workspace-root resolution and well-known directory accessors for the MiniAgentFramework.
#
# All modules that need to construct paths relative to the repository root should import
# the relevant accessor from here rather than rolling their own __file__-based computation.
# This ensures a single definition that is resilient to internal directory reorganisation
# and eliminates the three divergent implementations that previously existed in:
#   - skill_executor.py          (parent.parent)
#   - file_access_skill.py       (parents[3])
#
# Well-known directory accessors (all cached):
#   get_workspace_root()       ->  <repo_root>/
#   get_controldata_dir()      ->  <repo_root>/datacontrol/
#   get_logs_dir()             ->  <repo_root>/datacontrol/logs/
#   get_schedules_dir()        ->  <repo_root>/datacontrol/schedules/
#   get_test_prompts_dir()     ->  <repo_root>/datacontrol/test_prompts/
#   get_test_results_dir()     ->  <repo_root>/datacontrol/test_results/
#   get_chatsessions_dir()       ->  <repo_root>/datacontrol/chatsessions/
#   get_chatsessions_named_dir()  ->  <repo_root>/datacontrol/chatsessions/named/
#   get_chatsessions_day_dir()    ->  <repo_root>/datacontrol/chatsessions/<YYYY-MM-DD>/
#
# Related modules:
#   - file_access_skill.py  -- uses get_workspace_root() for path-safety checks
#   - skill_executor.py     -- uses get_workspace_root() to resolve skill module paths
#   - main.py               -- uses get_logs_dir(), get_schedules_dir()
#   - code/testing/test_wrapper.py -- uses get_test_results_dir()
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import os
from datetime import datetime
from functools import lru_cache
from pathlib import Path


# ====================================================================================================
# MARK: ROOT RESOLUTION
# ====================================================================================================
@lru_cache(maxsize=1)
def get_workspace_root() -> Path:
    """Return the absolute path to the repository root (the directory containing the code/ folder).

    Cached after first call so repeated lookups cost nothing - the root cannot change within
    a single process lifetime.
    """
    # This file lives at <repo_root>/code/KoreAgent/utils/workspace_utils.py
    return Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def get_suite_root() -> Path:
    """Return the consolidated suite root when one is configured, else the local repo root."""
    env_root = os.environ.get("KORE_SUITE_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()

    workspace_root = get_workspace_root()
    parent = workspace_root.parent
    if (parent / "config" / "default.json").exists():
        return parent.resolve()
    return workspace_root


@lru_cache(maxsize=1)
def get_suite_config_dir() -> Path:
    return get_suite_root() / "config"


@lru_cache(maxsize=1)
def get_suite_defaults_file() -> Path:
    return Path(os.environ.get("KORE_SUITE_CONFIG", str(get_suite_config_dir() / "default.json"))).resolve()


@lru_cache(maxsize=1)
def get_suite_local_file() -> Path:
    return get_suite_config_dir() / "local.json"


# ====================================================================================================
# MARK: DEFAULTS BOOTSTRAP
# ====================================================================================================
@lru_cache(maxsize=1)
def get_bootstrap_defaults_file() -> Path:
    """Return the agent LLM config file (model, ctx, llmhost, and agent-specific tuning)."""
    return get_workspace_root() / "llm_config.json"


def _read_json_file(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _flatten_suite_config(raw: dict) -> dict:
    flattened: dict[str, object] = {}
    if not isinstance(raw, dict):
        return flattened

    paths = raw.get("paths") if isinstance(raw.get("paths"), dict) else {}
    if isinstance(paths.get("datacontrol"), str):
        flattened["ControlDataFolder"] = paths["datacontrol"]
    if isinstance(paths.get("datauser"), str):
        flattened["UserDataFolder"] = paths["datauser"]

    services = raw.get("services") if isinstance(raw.get("services"), dict) else {}
    agent = services.get("agent") if isinstance(services.get("agent"), dict) else {}
    if agent.get("port") is not None:
        flattened["agentport"] = agent["port"]

    connections = raw.get("connections") if isinstance(raw.get("connections"), dict) else {}
    if isinstance(connections.get("korechat"), str):
        flattened["koreconvurl"] = connections["korechat"]
    if isinstance(connections.get("korecomms"), str):
        flattened["korecommsurl"] = connections["korecomms"]

    mcp = raw.get("mcp") if isinstance(raw.get("mcp"), dict) else {}
    if isinstance(mcp.get("connections"), list):
        flattened["mcp_connections"] = mcp["connections"]

    # Stash services and host so load_runtime_config can resolve service-ref MCP URLs
    # after all config layers (including local port overrides) have been merged.
    if isinstance(raw.get("services"), dict):
        flattened["_suite_services"] = raw["services"]
    network = raw.get("network") if isinstance(raw.get("network"), dict) else {}
    if isinstance(network.get("host"), str):
        flattened["_suite_host"] = network["host"]

    return flattened


def _resolve_mcp_service_refs(config: dict) -> None:
    """Resolve service-reference MCP connections to full URLs and clean up private keys.

    Connections that declare {"service": "docs", "path": "/mcp/sse"} are resolved using
    the final merged services ports and host.  This means a port override in local.json
    flows through automatically without duplicating port numbers in the MCP connection list.

    Mutates *config* in-place.  Removes the private "_suite_services" and "_suite_host"
    keys after use so they are not visible to the rest of the application.
    """
    connections = config.get("mcp_connections")
    services    = config.pop("_suite_services", {})
    host        = config.pop("_suite_host", "127.0.0.1")
    if not isinstance(connections, list):
        return
    for conn in connections:
        if not isinstance(conn, dict):
            continue
        if "url" not in conn and "service" in conn:
            svc  = services.get(conn["service"], {})
            port = svc.get("port")
            path = conn.get("path", "/mcp")
            if port:
                conn["url"] = f"http://{host}:{port}{path}"


@lru_cache(maxsize=1)
def load_runtime_config() -> dict:
    """Return merged runtime config from legacy agent defaults plus top-level suite config."""
    merged = dict(_read_json_file(get_bootstrap_defaults_file()))

    suite_defaults = get_suite_defaults_file()
    if suite_defaults.exists():
        merged.update(_flatten_suite_config(_read_json_file(suite_defaults)))

    suite_local = get_suite_local_file()
    if suite_local.exists():
        merged.update(_flatten_suite_config(_read_json_file(suite_local)))

    # Resolve any MCP connections that use service references instead of hardcoded URLs.
    # This runs after all config layers are merged so local.json port overrides are honoured.
    _resolve_mcp_service_refs(merged)

    return merged


@lru_cache(maxsize=1)
def _load_path_overrides() -> dict:
    """Load ControlDataFolder/UserDataFolder overrides from env, bootstrap defaults, then suite config."""

    overrides: dict[str, Path] = {}
    env_cd = os.environ.get("KORE_SUITE_DATACONTROL", "").strip()
    env_ud = os.environ.get("KORE_SUITE_DATAUSER", "").strip()
    if env_cd:
        overrides["ControlDataFolder"] = Path(env_cd).resolve()
    if env_ud:
        overrides["UserDataFolder"] = Path(env_ud).resolve()

    bootstrap_root = get_bootstrap_defaults_file().parent.resolve()
    bootstrap_raw = _read_json_file(get_bootstrap_defaults_file())
    for key in ("ControlDataFolder", "UserDataFolder"):
        if key in overrides:
            continue
        value = bootstrap_raw.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        path_value = Path(value.strip())
        overrides[key] = path_value if path_value.is_absolute() else (bootstrap_root / path_value).resolve()

    raw = load_runtime_config()
    for key in ("ControlDataFolder", "UserDataFolder"):
        if key in overrides:
            continue
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        p = Path(value.strip())
        overrides[key] = p if p.is_absolute() else (get_suite_root() / p).resolve()
    return overrides


# ====================================================================================================
# MARK: CONTROLDATA DIRECTORY ACCESSORS
# ====================================================================================================
@lru_cache(maxsize=1)
def get_controldata_dir() -> Path:
    """Return the absolute path to the datacontrol/ directory."""
    return _load_path_overrides().get("ControlDataFolder", get_suite_root() / "datacontrol")


@lru_cache(maxsize=1)
def get_user_data_dir() -> Path:
    """Return the absolute path to the user-data directory."""
    return _load_path_overrides().get("UserDataFolder", get_suite_root() / "datauser")


@lru_cache(maxsize=1)
def get_logs_dir() -> Path:
    """Return the absolute path to the datacontrol/logs/ directory."""
    return get_controldata_dir() / "logs"


@lru_cache(maxsize=1)
def get_schedules_dir() -> Path:
    """Return the absolute path to the datacontrol/schedules/ directory."""
    return get_controldata_dir() / "schedules"


@lru_cache(maxsize=1)
def get_test_prompts_dir() -> Path:
    """Return the absolute path to the datacontrol/test_prompts/ directory."""
    return get_controldata_dir() / "test_prompts"


@lru_cache(maxsize=1)
def get_test_results_dir() -> Path:
    """Return the absolute path to the datacontrol/test_results/ directory."""
    return get_controldata_dir() / "test_results"


@lru_cache(maxsize=1)
def get_chatsessions_dir() -> Path:
    """Return the absolute path to the datacontrol/chatsessions/ directory."""
    return get_controldata_dir() / "chatsessions"


@lru_cache(maxsize=1)
def get_chatsessions_named_dir() -> Path:
    """Return the absolute path to the named sessions subdirectory (datacontrol/chatsessions/named/)."""
    return get_chatsessions_dir() / "named"


def get_chatsessions_day_dir() -> Path:
    """Return the absolute path to today's chatsessions subdirectory (datacontrol/chatsessions/YYYY-MM-DD/)."""
    return get_chatsessions_dir() / datetime.now().strftime("%Y-%m-%d")


# ====================================================================================================
# MARK: PATH UTILITIES
# ====================================================================================================
def normalize_module_path(module_path: str) -> str:
    """Normalise a skill module path to a canonical form for allow-list comparisons.

    Strips leading ./ prefixes and any trailing .py extension so paths from different
    sources (skills_summary catalog vs LLM planner output) compare equal.
    """
    normalized = str(module_path).strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.endswith(".py"):
        normalized = normalized[:-3]
    return normalized


# ====================================================================================================
# MARK: STRING UTILITIES
# ====================================================================================================
def trunc(s: str, n: int) -> str:
    """Return s capped to n characters, appending '...' when truncated."""
    if len(s) <= n:
        return s
    return s[:n - 3] + "..."
