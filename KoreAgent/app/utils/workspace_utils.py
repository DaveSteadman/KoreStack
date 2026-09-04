# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared workspace-root resolution and well-known directory accessors for KoreAgent.
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
#   get_chatsessions_dir()       ->  <repo_root>/datacontrol/chatsessions/
#   get_chatsessions_named_dir()  ->  <repo_root>/datacontrol/chatsessions/named/
#   get_chatsessions_day_dir()    ->  <repo_root>/datacontrol/chatsessions/<YYYY-MM-DD>/
#
# Related modules:
#   - file_access_skill.py  -- uses get_workspace_root() for path-safety checks
#   - skill_executor.py     -- uses get_workspace_root() to resolve skill module paths
#   - main.py               -- uses get_logs_dir()
# MARK: FUNCTIONS
# Function inventory:
# - get_workspace_root: Returns workspace root for this module.
# - get_suite_root: Returns suite root for this module.
# - get_suite_config_dir: Returns suite config dir for this module.
# - get_suite_defaults_file: Returns suite defaults file for this module.
# - get_agent_config_file: Returns agent config file for this module.
# - _read_json_file: Implements the  read json file operation for this module.
# - _flatten_suite_config: Implements the  flatten suite config operation for this module.
# - _merge_runtime_config_layer: Implements the  merge runtime config layer operation for this module.
# - load_runtime_config: Loads runtime config for this module.
# - _load_path_overrides: Implements the  load path overrides operation for this module.
# - get_controldata_dir: Returns controldata dir for this module.
# - get_user_data_dir: Returns user data dir for this module.
# - get_logs_dir: Returns logs dir for this module.
# - get_plans_dir: Returns plans dir for this module.
# - get_chatsessions_dir: Returns chatsessions dir for this module.
# - get_chatsessions_named_dir: Returns chatsessions named dir for this module.
# - get_chatsessions_day_dir: Returns chatsessions day dir for this module.
# - normalize_module_path: Normalizes module path for this module.
# - trunc: Implements the trunc operation for this module.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import os
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from KoreCommon.suite_paths import get_suite_datacontrol_dir as _get_suite_datacontrol_dir_common
from KoreCommon.suite_paths import get_suite_datauser_dir as _get_suite_datauser_dir_common
from KoreCommon.suite_paths import get_suite_root as _get_suite_root_common


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
    return _get_suite_root_common()


@lru_cache(maxsize=1)
def get_suite_config_dir() -> Path:
    return get_suite_root() / "config"


@lru_cache(maxsize=1)
def get_suite_defaults_file() -> Path:
    return Path(os.environ.get("KORE_SUITE_CONFIG", str(get_suite_config_dir() / "korestack_config.json"))).resolve()


# ====================================================================================================
# MARK: AGENT CONFIGURATION
# ====================================================================================================
@lru_cache(maxsize=1)
def get_agent_config_file() -> Path:
    """Return the agent LLM config file (model, ctx, llmhost, and agent-specific tuning)."""
    return get_workspace_root() / "config" / "koreagent_config.json"


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
    if isinstance(paths.get("dataroot"), str) and paths["dataroot"].strip():
        flattened["DataRootFolder"] = paths["dataroot"].strip()

    services = raw.get("services") if isinstance(raw.get("services"), dict) else {}
    agent = services.get("koreagent") if isinstance(services.get("koreagent"), dict) else {}
    if agent.get("port") is not None:
        flattened["agentport"] = agent["port"]

    connections = raw.get("connections") if isinstance(raw.get("connections"), dict) else {}
    if isinstance(connections.get("korechat"), str):
        flattened["korechaturl"] = connections["korechat"]
    elif isinstance(services.get("korechat"), dict):
        _net = raw.get("network") if isinstance(raw.get("network"), dict) else {}
        _kc_host = str(_net.get("host") or "127.0.0.1")
        _kc_port = services["korechat"].get("port")
        if _kc_port is not None:
            flattened["korechaturl"] = f"http://{_kc_host}:{_kc_port}"

    return flattened


def _merge_runtime_config_layer(merged: dict, layer: dict) -> None:
    """Merge one flattened suite configuration layer into the runtime configuration."""
    if not isinstance(layer, dict):
        return

    merged.update(layer)


@lru_cache(maxsize=1)
def load_runtime_config() -> dict:
    """Return runtime configuration merged from Agent and suite configuration files."""
    merged = dict(_read_json_file(get_agent_config_file()))

    suite_config = get_suite_defaults_file()
    if suite_config.exists():
        _merge_runtime_config_layer(merged, _flatten_suite_config(_read_json_file(suite_config)))

    return merged


@lru_cache(maxsize=1)
def _load_path_overrides() -> dict:
    """Load dataroot overrides from the environment, Agent configuration, then suite config."""

    overrides: dict[str, Path] = {}
    env_dr = os.environ.get("KORE_SUITE_DATAROOT", "").strip()
    env_cd = os.environ.get("KORE_SUITE_DATACONTROL", "").strip()
    env_ud = os.environ.get("KORE_SUITE_DATAUSER", "").strip()
    if env_dr:
        overrides["DataRootFolder"] = Path(env_dr).resolve()
    if env_cd:
        overrides["ControlDataFolder"] = Path(env_cd).resolve()
    if env_ud:
        overrides["UserDataFolder"] = Path(env_ud).resolve()

    agent_root = get_workspace_root().resolve()
    agent_config = _read_json_file(get_agent_config_file())
    for key in ("DataRootFolder", "ControlDataFolder", "UserDataFolder"):
        if key in overrides:
            continue
        value = agent_config.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        path_value = Path(value.strip())
        overrides[key] = path_value if path_value.is_absolute() else (agent_root / path_value).resolve()

    raw = load_runtime_config()
    for key in ("DataRootFolder", "ControlDataFolder", "UserDataFolder"):
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
    overrides = _load_path_overrides()
    if "ControlDataFolder" in overrides:
        return overrides["ControlDataFolder"]
    if "DataRootFolder" in overrides:
        return overrides["DataRootFolder"] / "datacontrol"
    return _get_suite_datacontrol_dir_common()


@lru_cache(maxsize=1)
def get_user_data_dir() -> Path:
    """Return the absolute path to the user-data directory."""
    overrides = _load_path_overrides()
    if "UserDataFolder" in overrides:
        return overrides["UserDataFolder"]
    if "DataRootFolder" in overrides:
        return overrides["DataRootFolder"] / "datacontrol" / "datauser"
    return _get_suite_datauser_dir_common()


@lru_cache(maxsize=1)
def get_logs_dir() -> Path:
    """Return the absolute path to the datacontrol/logs/ directory."""
    return get_controldata_dir() / "logs"


@lru_cache(maxsize=1)
def get_plans_dir() -> Path:
    """Return the absolute path to the datacontrol/plans/ directory."""
    return get_controldata_dir() / "plans"


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
