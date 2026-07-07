from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from KoreCommon.suite_paths import get_suite_config_file as _get_suite_config_file_common
from KoreCommon.suite_paths import get_suite_datauser_dir as _get_suite_datauser_dir_common
from KoreCommon.suite_paths import get_suite_dataroot_dir as _get_suite_dataroot_dir_common
from KoreCommon.suite_paths import get_suite_root as _get_suite_root_common


class DataUserPathError(ValueError):
    """Raised when a caller supplies a path outside the shared datauser tree."""


class DataUserConflictError(RuntimeError):
    """Raised when an optimistic file operation detects a conflict."""


def _read_json_file(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def get_workspace_root() -> Path:
    return Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def get_suite_root() -> Path:
    return _get_suite_root_common()


@lru_cache(maxsize=1)
def get_suite_config_file() -> Path:
    return _get_suite_config_file_common()


@lru_cache(maxsize=1)
def get_bootstrap_defaults_file() -> Path:
    return get_workspace_root() / "config" / "llm_config.json"


@lru_cache(maxsize=1)
def _load_path_overrides() -> dict[str, Path]:
    overrides: dict[str, Path] = {}

    env_dataroot = os.environ.get("KORE_SUITE_DATAROOT", "").strip()
    env_datauser = os.environ.get("KORE_SUITE_DATAUSER", "").strip()
    if env_dataroot:
        overrides["DataRootFolder"] = Path(env_dataroot).resolve()
    if env_datauser:
        overrides["UserDataFolder"] = Path(env_datauser).resolve()

    bootstrap_root = get_workspace_root().resolve()
    bootstrap_raw = _read_json_file(get_bootstrap_defaults_file())
    for key in ("DataRootFolder", "UserDataFolder"):
        if key in overrides:
            continue
        value = bootstrap_raw.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        candidate = Path(value.strip())
        overrides[key] = candidate if candidate.is_absolute() else (bootstrap_root / candidate).resolve()

    suite_raw = _read_json_file(get_suite_config_file())
    paths = suite_raw.get("paths") if isinstance(suite_raw.get("paths"), dict) else {}
    if "DataRootFolder" not in overrides:
        dataroot = paths.get("dataroot")
        if isinstance(dataroot, str) and dataroot.strip():
            candidate = Path(dataroot.strip())
            if not any(part.lower() == "absolutepath" for part in candidate.parts):
                overrides["DataRootFolder"] = candidate if candidate.is_absolute() else (get_suite_root() / candidate).resolve()
    if "DataRootFolder" not in overrides:
        overrides["SuiteDataRootFolder"] = _get_suite_dataroot_dir_common()

    return overrides


@lru_cache(maxsize=1)
def get_datauser_root() -> Path:
    overrides = _load_path_overrides()
    if "UserDataFolder" in overrides:
        return overrides["UserDataFolder"]
    if "DataRootFolder" in overrides:
        return (overrides["DataRootFolder"] / "datauser").resolve()
    if "SuiteDataRootFolder" in overrides:
        return (overrides["SuiteDataRootFolder"] / "datauser").resolve()
    return _get_suite_datauser_dir_common()


def _coerce_root_dir(root_dir: str | Path | None = None) -> Path:
    return get_datauser_root() if root_dir is None else Path(root_dir).resolve()


def ensure_datauser_root(root_dir: str | Path | None = None) -> Path:
    root = _coerce_root_dir(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def sanitize_input_path(file_path: str | Path) -> str:
    cleaned = str(file_path or "").strip().strip('"').strip("'")
    if not cleaned:
        raise DataUserPathError("file_path cannot be empty")
    return cleaned.replace("\\", "/")


def normalize_datauser_relative_path(file_path: str | Path) -> str:
    normalized = sanitize_input_path(file_path)

    if normalized == "data" or normalized == "./data":
        return ""
    if normalized.startswith("./data/"):
        return normalized[7:]
    if normalized.startswith("data/"):
        return normalized[5:]
    if normalized == "datauser" or normalized == "./datauser":
        return ""
    if normalized.startswith("./datauser/"):
        return normalized[11:]
    if normalized.startswith("datauser/"):
        return normalized[9:]

    lowered = normalized.lower()
    if lowered == "koredocs" or lowered == "./koredocs":
        return ""
    if lowered.startswith("./koredocs/"):
        return normalized[11:]
    if lowered.startswith("koredocs/"):
        return normalized[9:]

    return normalized


def resolve_datauser_path(file_path: str | Path, *, root_dir: str | Path | None = None) -> Path:
    root = ensure_datauser_root(root_dir)
    normalized = normalize_datauser_relative_path(file_path)

    if normalized.startswith("./"):
        candidate = (get_workspace_root() / normalized[2:]).resolve()
    else:
        candidate_path = Path(normalized)
        if candidate_path.is_absolute():
            candidate = candidate_path.resolve()
        else:
            candidate = (root / normalized).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise DataUserPathError(f"Path escapes data directory and is not allowed: {file_path}") from exc

    return candidate


def resolve_datauser_directory(path: str | Path = "", *, root_dir: str | Path | None = None) -> Path:
    cleaned = str(path or "").strip()
    if cleaned in ("", "."):
        return ensure_datauser_root(root_dir)
    return resolve_datauser_path(cleaned.rstrip("/") + "/.keep", root_dir=root_dir).parent


def datauser_relative_path(target_path: str | Path, *, root_dir: str | Path | None = None) -> str:
    candidate = target_path if isinstance(target_path, Path) else resolve_datauser_path(target_path, root_dir=root_dir)
    resolved = candidate.resolve()
    root = ensure_datauser_root(root_dir)
    try:
        relative = resolved.relative_to(root)
        return "" if relative == Path(".") else relative.as_posix()
    except ValueError as exc:
        raise DataUserPathError(f"Path is outside datauser: {target_path}") from exc


def display_datauser_path(target_path: str | Path, *, root_dir: str | Path | None = None) -> str:
    relative = datauser_relative_path(target_path, root_dir=root_dir)
    root_name = ensure_datauser_root(root_dir).name
    return f"{root_name}/{relative}" if relative else root_name


def file_etag(target_path: str | Path, *, root_dir: str | Path | None = None) -> str:
    resolved = target_path if isinstance(target_path, Path) else resolve_datauser_path(target_path, root_dir=root_dir)
    stat = resolved.stat()
    return f'W/"{stat.st_mtime_ns}-{stat.st_size}"'


def list_datauser_files(
    *,
    search_root: str | Path = "",
    keywords: Iterable[str] | None = None,
    recursive: bool = True,
    allowed_extensions: set[str] | None = None,
    root_dir: str | Path | None = None,
) -> list[Path]:
    base = resolve_datauser_directory(search_root, root_dir=root_dir)
    keyword_list = [str(k).strip().lower() for k in (keywords or []) if str(k).strip()]
    ext_filter = {ext.lower() for ext in allowed_extensions} if allowed_extensions else None
    iterator = base.rglob("*") if recursive else base.iterdir()
    return [
        path
        for path in sorted(iterator)
        if path.is_file()
        and (ext_filter is None or path.suffix.lower() in ext_filter)
        and (not keyword_list or all(keyword in path.name.lower() for keyword in keyword_list))
    ]


def list_datauser_folders(
    *,
    search_root: str | Path = "",
    keywords: Iterable[str] | None = None,
    recursive: bool = True,
    root_dir: str | Path | None = None,
) -> list[Path]:
    base = resolve_datauser_directory(search_root, root_dir=root_dir)
    keyword_list = [str(k).strip().lower() for k in (keywords or []) if str(k).strip()]
    iterator = base.rglob("*") if recursive else base.iterdir()
    return [
        path
        for path in sorted(iterator)
        if path.is_dir()
        and (not keyword_list or all(keyword in path.name.lower() for keyword in keyword_list))
    ]


def read_text_file(
    path: str | Path,
    *,
    max_chars: int | None = None,
    encoding: str = "utf-8",
    root_dir: str | Path | None = None,
) -> str:
    resolved = resolve_datauser_path(path, root_dir=root_dir)
    content = resolved.read_text(encoding=encoding)
    if max_chars is not None and len(content) > max_chars:
        return content[:max_chars] + "\n[truncated]"
    return content


def read_binary_file(
    path: str | Path,
    *,
    max_bytes: int | None = None,
    root_dir: str | Path | None = None,
) -> tuple[bytes, bool, int]:
    resolved = resolve_datauser_path(path, root_dir=root_dir)
    raw = resolved.read_bytes()
    total_len = len(raw)
    truncated = False
    if max_bytes is not None and total_len > max_bytes:
        raw = raw[:max_bytes]
        truncated = True
    return raw, truncated, total_len


def write_text_file(
    path: str | Path,
    content: str,
    *,
    append: bool = False,
    ensure_trailing_newline: bool = False,
    overwrite: bool = True,
    expected_etag: str | None = None,
    encoding: str = "utf-8",
    root_dir: str | Path | None = None,
) -> Path:
    resolved = resolve_datauser_path(path, root_dir=root_dir)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    if expected_etag is not None and resolved.exists() and expected_etag != file_etag(resolved, root_dir=root_dir):
        raise DataUserConflictError("File changed on disk; reload before writing")

    text = str(content)
    if ensure_trailing_newline and not text.endswith("\n"):
        text += "\n"

    mode = "a" if append else ("x" if not overwrite else "w")
    try:
        with resolved.open(mode, encoding=encoding) as handle:
            handle.write(text)
    except FileExistsError as exc:
        raise DataUserConflictError("File already exists") from exc

    return resolved


def delete_file(path: str | Path, *, expected_etag: str | None = None, root_dir: str | Path | None = None) -> None:
    resolved = resolve_datauser_path(path, root_dir=root_dir)
    if expected_etag is not None and resolved.exists() and expected_etag != file_etag(resolved, root_dir=root_dir):
        raise DataUserConflictError("File changed on disk; reload before writing")
    resolved.unlink()


def create_folder(path: str | Path, *, root_dir: str | Path | None = None) -> Path:
    folder = resolve_datauser_directory(path, root_dir=root_dir)
    folder.mkdir(parents=True, exist_ok=True)
    return folder
