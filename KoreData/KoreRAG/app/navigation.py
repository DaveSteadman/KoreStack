import importlib.util
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from app.config import cfg
from app.registry import get_descriptor


_REPO_DATABASES_ROOT = (
    Path(__file__).resolve().parents[3]
    / "Data"
    / "datacontrol"
    / "koredata"
    / "RAG"
    / "databases"
)
_RUNTIME_DATABASES_ROOT = Path(cfg["data_dir"]) / "databases"


def _provider_roots() -> list[Path]:
    roots: list[Path] = []
    for candidate in (_RUNTIME_DATABASES_ROOT, _REPO_DATABASES_ROOT):
        resolved = candidate.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _provider_path(db_id: str) -> Optional[Path]:
    descriptor = get_descriptor(db_id) or {}
    candidates = [
        str(descriptor.get("ingestor") or "").strip(),
        str(db_id or "").strip(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        for root in _provider_roots():
            path = root / candidate / "navigation_access.py"
            if path.exists():
                return path
    return None


@lru_cache(maxsize=None)
def _load_provider_from_path(path_str: str) -> ModuleType:
    path = Path(path_str)
    spec = importlib.util.spec_from_file_location(f"korerag_nav_{path.parent.name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load navigation provider from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


def get_provider(db_id: str) -> Optional[ModuleType]:
    path = _provider_path(db_id)
    if path is None:
        return None
    return _load_provider_from_path(str(path.resolve()))


def provider_supports(db_id: str, attribute_name: str) -> bool:
    provider = get_provider(db_id)
    return provider is not None and hasattr(provider, attribute_name)


def provider_attribute(db_id: str, attribute_name: str, default: Any = None) -> Any:
    provider = get_provider(db_id)
    if provider is None:
        return default
    return getattr(provider, attribute_name, default)


def provider_call(db_id: str, function_name: str, *args: Any, **kwargs: Any) -> Any:
    provider = get_provider(db_id)
    if provider is None:
        raise LookupError(f"No navigation provider for database {db_id!r}")
    func = getattr(provider, function_name, None)
    if func is None:
        raise AttributeError(f"Navigation provider for {db_id!r} has no {function_name!r}")
    return func(*args, **kwargs)


def has_navigation(db_id: str) -> bool:
    provider = get_provider(db_id)
    if provider is None:
        return False
    func = getattr(provider, "has_navigation", None)
    if func is None:
        return False
    try:
        return bool(func(db=db_id))
    except TypeError:
        return bool(func(db_id))


def get_navigation_type(db_id: str) -> Optional[str]:
    descriptor = get_descriptor(db_id) or {}
    navigation = descriptor.get("navigation") or {}
    nav_type   = str(navigation.get("type") or "").strip().lower()
    if nav_type:
        return nav_type
    provider_type = provider_attribute(db_id, "NAVIGATION_TYPE")
    return str(provider_type).strip().lower() if provider_type else None
