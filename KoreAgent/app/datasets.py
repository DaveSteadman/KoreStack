# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Scratchpad Datasets runtime for KoreAgent.
#
# Datasets are session-scoped structured working sets stored alongside the existing string
# scratchpad. Small datasets persist inline inside the KoreChat scratchpad JSON; larger ones spill
# over to a local SQLite store while a compact manifest stays in the persisted session payload.
# ====================================================================================================

import json
import re
import threading
from datetime import datetime
from datetime import timezone
from functools import lru_cache
from uuid import uuid4

import httpx

import datasets_store
from session_runtime import get_active_session_id
from utils.workspace_utils import load_runtime_config


_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_DUPLICATE_RE = re.compile(r"^duplicate\s+by\s+([A-Za-z_][A-Za-z0-9_]*)$", re.IGNORECASE)
_MISSING_RE = re.compile(r"^missing\s+field\s+([A-Za-z_][A-Za-z0-9_]*)$", re.IGNORECASE)
_REGEX_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*~=\s*(.+)$")
_CMP_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*([<>])\s*(.+)$")

INLINE_THRESHOLD_BYTES = 50_000
_MANIFEST_HISTORY_LIMIT = 10
_FULL_TEXT_TIMEOUT_SECS = 60.0
_SESSION_DATASETS: dict[str, dict[str, dict]] = {}
_DATASET_LOCK: threading.RLock = threading.RLock()


def _resolve_session_id(session_id: str | None = None) -> str:
    cleaned = str(session_id or "").strip()
    return cleaned or get_active_session_id()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_name(name: str) -> str:
    cleaned = str(name or "").strip().lower()
    if not cleaned:
        raise ValueError("Dataset name cannot be empty.")
    if not _NAME_RE.match(cleaned):
        raise ValueError(
            f"Dataset name '{name}' contains invalid characters. Use letters, digits, and underscores only."
        )
    return cleaned


def _session_map(session_id: str | None = None) -> dict[str, dict]:
    resolved = _resolve_session_id(session_id)
    with _DATASET_LOCK:
        return _SESSION_DATASETS.setdefault(resolved, {})


def _coerce_records(records: object) -> list[dict]:
    candidate = records
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except Exception as exc:
            raise ValueError(f"records must be valid JSON when passed as a string: {exc}")
    if isinstance(candidate, dict):
        nested = candidate.get("results")
        if isinstance(nested, list):
            candidate = nested
        elif isinstance(candidate.get("records"), list):
            candidate = candidate.get("records")
    if not isinstance(candidate, list):
        raise ValueError("records must be a list of objects")
    normalized: list[dict] = []
    for index, item in enumerate(candidate):
        if not isinstance(item, dict):
            raise ValueError(f"records[{index}] is not an object")
        normalized.append(dict(item))
    return normalized


@lru_cache(maxsize=1)
def _get_koredata_gateway_base_url() -> str:
    runtime_config = load_runtime_config()
    connections = runtime_config.get("mcp_connections") if isinstance(runtime_config, dict) else None
    if not isinstance(connections, list):
        return ""

    for connection in connections:
        if not isinstance(connection, dict):
            continue
        expected_prefix = str(connection.get("expected_prefix") or "").strip().lower()
        name = str(connection.get("name") or "").strip().lower()
        url = str(connection.get("url") or "").strip()
        if not url:
            continue
        if expected_prefix == "koredata_" or name == "koredata":
            stripped = url.rstrip("/")
            return stripped[:-4] if stripped.endswith("/mcp") else stripped
    return ""


def _fetch_full_text_payload(refid: str, *, client: httpx.Client | None = None, base_url: str = "") -> dict:
    resolved_base = str(base_url or _get_koredata_gateway_base_url()).strip().rstrip("/")
    if not resolved_base:
        raise RuntimeError("KoreData gateway URL is not configured.")

    owns_client = client is None
    active_client = client or httpx.Client(timeout=_FULL_TEXT_TIMEOUT_SECS)
    try:
        response = active_client.post(f"{resolved_base}/api/full-text", json={"refid": refid})
    except httpx.HTTPError as exc:
        raise RuntimeError(f"KoreData full-text fetch failed: {exc}") from exc
    finally:
        if owns_client:
            active_client.close()

    if response.status_code != 200:
        raise RuntimeError(f"KoreData full-text fetch failed with HTTP {response.status_code}.")

    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(f"KoreData full-text fetch returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("KoreData full-text fetch returned a non-object payload.")
    return payload


def _expand_records_with_full_text(
    records: list[dict],
    *,
    client: httpx.Client | None = None,
    base_url: str = "",
) -> tuple[list[dict], list[str]]:
    resolved_base = str(base_url or _get_koredata_gateway_base_url()).strip()
    expanded: list[dict] = []
    failures: list[str] = []

    for index, record in enumerate(records, start=1):
        refid = str(record.get("artifact_ref") or "").strip()
        if not refid:
            failures.append(f"row {index} missing artifact_ref")
            continue
        try:
            payload = _fetch_full_text_payload(refid, client=client, base_url=resolved_base)
        except Exception as exc:
            failures.append(f"row {index} fetch failed: {exc}")
            continue

        error_text = str(payload.get("error") or "").strip()
        if error_text:
            failures.append(f"row {index} fetch failed: {error_text}")
            continue

        merged = dict(record)
        merged.update(payload)
        merged["artifact_ref"] = refid
        expanded.append(merged)

    return expanded, failures


def _infer_schema(records: list[dict]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for record in records:
        for key in record.keys():
            if key not in seen:
                seen.add(key)
                keys.append(str(key))
    return keys


def _new_dataset_id() -> str:
    return f"ds_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"


def _history_entry(
    *,
    op: str,
    prompt: str = "",
    kept: int = 0,
    dropped: int = 0,
    fields: list[str] | None = None,
    replaced: bool = False,
) -> dict:
    return {
        "op": op,
        "prompt": (prompt or "")[:200],
        "fields": list(fields or []),
        "kept": int(kept),
        "dropped": int(dropped),
        "replaced": bool(replaced),
        "at": _utc_now(),
    }


def _serialized_size(records: list[dict], history: list[dict], meta: dict) -> int:
    payload = {
        "records": records,
        "history": history,
        "meta": meta,
    }
    return len(json.dumps(payload, ensure_ascii=False))


def _derive_storage_mode(existing_mode: str | None, records: list[dict], history: list[dict], meta: dict) -> str:
    if existing_mode == "spillover":
        return "spillover"
    return "inline" if _serialized_size(records, history, meta) < INLINE_THRESHOLD_BYTES else "spillover"


def _manifest(dataset: dict) -> dict:
    return {
        "dataset_id": dataset["dataset_id"],
        "inline": dataset.get("storage_mode") == "inline",
        "count": int(dataset.get("count", len(dataset.get("records") or []))),
        "schema": list(dataset.get("schema") or []),
        "source_tool": dataset.get("source_tool") or "",
        "source_args": _coerce_source_args(dataset.get("source_args")),
        "parent_dataset_id": dataset.get("parent_dataset_id") or "",
        "created_at": dataset.get("created_at") or "",
        "updated_at": dataset.get("updated_at") or "",
        "auto_named": bool(dataset.get("auto_named")),
        "history_tail": list((dataset.get("history") or [])[-_MANIFEST_HISTORY_LIMIT:]),
    }


def _inline_entry(dataset: dict) -> dict:
    entry = _manifest(dataset)
    entry["records"] = dataset.get("records") or []
    entry["history"] = list(dataset.get("history") or [])
    return entry


def _ensure_loaded(dataset: dict) -> dict:
    if dataset.get("records") is not None:
        return dataset
    loaded = datasets_store.load_dataset(dataset["dataset_id"])
    if loaded is None:
        dataset["missing_spillover"] = True
        raise KeyError(dataset["dataset_id"])
    dataset["records"] = loaded.get("records") or []
    dataset["schema"] = loaded.get("schema") or []
    dataset["source_tool"] = loaded.get("source_tool") or dataset.get("source_tool") or ""
    dataset["source_args"] = loaded.get("source_args")
    dataset["parent_dataset_id"] = loaded.get("parent_dataset_id") or dataset.get("parent_dataset_id") or ""
    dataset["history"] = loaded.get("history") or dataset.get("history") or []
    dataset["created_at"] = loaded.get("created_at") or dataset.get("created_at") or _utc_now()
    dataset["updated_at"] = loaded.get("updated_at") or dataset.get("updated_at") or _utc_now()
    dataset["storage_mode"] = loaded.get("storage_mode") or "spillover"
    dataset["auto_named"] = bool(loaded.get("auto_named"))
    dataset["count"] = len(dataset.get("records") or [])
    dataset.pop("missing_spillover", None)
    return dataset


def _get_dataset(name: str, session_id: str | None = None) -> dict:
    validated = _validate_name(name)
    store = _session_map(session_id)
    with _DATASET_LOCK:
        dataset = store.get(validated)
    if dataset is None:
        raise LookupError(validated)
    try:
        return _ensure_loaded(dataset)
    except KeyError as exc:
        raise FileNotFoundError(str(exc)) from exc


def _project_record(record: dict, fields: list[str] | None, excerpt_chars: int) -> dict:
    excerpt_limit = max(0, int(excerpt_chars or 0))
    if fields:
        projected = {field: record.get(field) for field in fields if field in record}
    else:
        projected = {}
        for field in ("title", "source", "published_at", "url", "snippet"):
            if field in record:
                projected[field] = record.get(field)
        if not projected:
            for key in list(record.keys())[:5]:
                projected[key] = record.get(key)
    if "snippet" not in projected:
        for body_key in ("snippet", "summary", "body", "content", "text"):
            value = record.get(body_key)
            if isinstance(value, str) and value.strip():
                projected["snippet"] = value[:excerpt_limit] if excerpt_limit > 0 else value
                break
    return projected


def _derive_name(base_name: str, suffix: str, session_id: str | None = None) -> str:
    validated_base = _validate_name(base_name)
    store = _session_map(session_id)
    suffix_clean = _validate_name(suffix)
    candidate = f"{validated_base}_{suffix_clean}"
    if candidate not in store:
        return candidate
    index = 2
    while f"{candidate}_{index}" in store:
        index += 1
    return f"{candidate}_{index}"


def _coerce_non_negative_int(value: object, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _select_records(
    records: list[dict],
    *,
    indices: list[int] | None = None,
    offset: int = 0,
    limit: int = 0,
    max_records: int = 0,
) -> tuple[list[dict], dict]:
    total = len(records)
    if indices:
        valid_indices = [index for index in indices if isinstance(index, int) and 0 <= index < total]
        selected = [records[index] for index in valid_indices]
        return selected, {
            "selection_mode": "indices",
            "indices": valid_indices,
            "offset": 0,
            "limit": len(valid_indices),
            "returned": len(selected),
            "has_more": False,
            "next_offset": None,
        }

    start = _coerce_non_negative_int(offset)
    page_size = _coerce_non_negative_int(limit) or _coerce_non_negative_int(max_records) or 20
    end = min(total, start + page_size)
    selected = records[start:end]
    has_more = end < total
    return selected, {
        "selection_mode": "page",
        "indices": [],
        "offset": start,
        "limit": page_size,
        "returned": len(selected),
        "has_more": has_more,
        "next_offset": end if has_more else None,
    }


def _normalise_koredocs_folder(folder_path: str) -> str:
    cleaned = str(folder_path or "").strip().replace("\\", "/").strip("/")
    if not cleaned:
        return ""
    if cleaned.lower() == "koredocs":
        return ""
    if cleaned.lower().startswith("koredocs/"):
        return cleaned.split("/", 1)[1]
    return cleaned


def _normalise_koredoc_name(document_name: str, fallback_name: str) -> str:
    cleaned = str(document_name or "").strip()
    if not cleaned:
        cleaned = fallback_name
    if not cleaned.lower().endswith(".koredoc"):
        cleaned += ".koredoc"
    return cleaned


def _format_export_value(value: object) -> list[str]:
    if value is None:
        return [""]
    if isinstance(value, (dict, list)):
        text = json.dumps(value, indent=2, ensure_ascii=False)
    else:
        text = str(value)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.split("\n") or [""]


def _dataset_records_to_markdown(dataset: dict, selected: list[dict], selection: dict, fields: list[str]) -> str:
    lines = [
        f"# {dataset['name']}",
        "",
        f"- dataset_id: {dataset['dataset_id']}",
        f"- total_records: {selection['total_count']}",
        f"- exported_offset: {selection['offset']}",
        f"- exported_records: {selection['returned']}",
        "",
    ]

    start_index = selection["offset"] + 1 if selection["selection_mode"] == "page" else 1
    for ordinal, record in enumerate(selected, start=start_index):
        lines.append(f"## Record {ordinal}")
        for field in fields:
            value_lines = _format_export_value(record.get(field))
            if len(value_lines) == 1:
                lines.append(f"- **{field}:** {value_lines[0]}")
            else:
                lines.append(f"- **{field}:**")
                lines.extend(f"  {line}" for line in value_lines)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _auto_name(source_tool: str, session_id: str | None = None) -> str:
    tool_slug = re.sub(r"[^a-z0-9_]+", "_", (source_tool or "dataset").lower()).strip("_") or "dataset"
    return _derive_name(tool_slug, "1", session_id) if tool_slug in _session_map(session_id) else tool_slug + "_1"


def _coerce_source_args(source_args: object) -> object:
    if isinstance(source_args, str):
        stripped = source_args.strip()
        if not stripped:
            return None
        try:
            return json.loads(stripped)
        except Exception:
            return stripped
    return source_args


def coerce_persisted_scratchpad_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    named_scratch: dict[str, object] = {}
    for raw_key, raw_value in payload.items():
        key = str(raw_key)
        named_scratch[key] = raw_value
    return named_scratch


def coerce_persisted_datasets_payload(payload: object) -> dict[str, dict]:
    if not isinstance(payload, dict):
        return {}
    return {
        str(raw_name): dict(entry)
        for raw_name, entry in payload.items()
        if isinstance(entry, dict)
    }


def hydrate_session_state(
    scratchpad_payload: object,
    session_id: str | None = None,
    *,
    datasets_payload: object = None,
    scratch_clearer=None,
    scratch_restorer=None,
    warning_logger=None,
) -> dict[str, object]:
    resolved = _resolve_session_id(session_id)
    named_scratch = coerce_persisted_scratchpad_payload(scratchpad_payload)
    persisted_datasets = coerce_persisted_datasets_payload(datasets_payload)

    if scratch_clearer is not None:
        scratch_clearer(session_id=resolved)
    clear_session_datasets(resolved)
    restore_persisted_datasets(persisted_datasets, resolved)

    if scratch_restorer is None:
        return named_scratch

    for scratch_key, scratch_value in named_scratch.items():
        try:
            scratch_restorer(scratch_key, str(scratch_value), session_id=resolved)
        except TypeError:
            scratch_restorer(scratch_key, str(scratch_value), resolved)
        except Exception as exc:
            if warning_logger is not None:
                warning_logger(f"could not restore scratchpad key {scratch_key!r}: {exc}")
    return named_scratch


def _write_dataset(dataset: dict, session_id: str | None = None) -> None:
    resolved = _resolve_session_id(session_id)
    store = _session_map(resolved)
    with _DATASET_LOCK:
        store[dataset["name"]] = dataset
    if dataset.get("storage_mode") == "spillover":
        datasets_store.upsert_dataset(dataset)
    else:
        datasets_store.delete_dataset(dataset["dataset_id"])


def _save_dataset_internal(
    *,
    name: str,
    records: list[dict],
    source_tool: str = "",
    source_args: object = None,
    parent_dataset_id: str = "",
    history: list[dict] | None = None,
    replace: bool = False,
    auto_named: bool = False,
    existing_dataset: dict | None = None,
    session_id: str | None = None,
) -> dict:
    resolved = _resolve_session_id(session_id)
    validated_name = _validate_name(name)
    now = _utc_now()
    prior = existing_dataset
    if prior is None and replace:
        prior = _get_dataset(validated_name, resolved)

    created_at = prior.get("created_at") if prior else now
    dataset_id = prior.get("dataset_id") if prior else _new_dataset_id()
    schema = _infer_schema(records)
    history_items = list(history or (prior.get("history") if prior else []) or [])
    meta = {
        "schema": schema,
        "source_tool": source_tool,
        "source_args": _coerce_source_args(source_args),
        "parent_dataset_id": parent_dataset_id or (prior.get("parent_dataset_id") if prior else ""),
    }
    storage_mode = _derive_storage_mode(prior.get("storage_mode") if prior else None, records, history_items, meta)
    dataset = {
        "dataset_id": dataset_id,
        "session_id": resolved,
        "name": validated_name,
        "records": records,
        "count": len(records),
        "schema": schema,
        "source_tool": source_tool,
        "source_args": _coerce_source_args(source_args),
        "parent_dataset_id": parent_dataset_id or (prior.get("parent_dataset_id") if prior else ""),
        "history": history_items,
        "created_at": created_at,
        "updated_at": now,
        "storage_mode": storage_mode,
        "auto_named": bool(auto_named),
        "missing_spillover": False,
    }
    _write_dataset(dataset, resolved)
    return dataset


def get_prompt_dataset_manifests(session_id: str | None = None) -> list[dict]:
    resolved = _resolve_session_id(session_id)
    with _DATASET_LOCK:
        store = _SESSION_DATASETS.get(resolved, {})
        datasets = [
            {
                **dataset,
                "count": int(dataset.get("count", len(dataset.get("records") or []))),
            }
            for dataset in store.values()
        ]
    return sorted(datasets, key=lambda item: item.get("name", ""))


def get_persisted_datasets_payload(session_id: str | None = None) -> dict:
    payload: dict[str, dict] = {}
    for dataset in get_prompt_dataset_manifests(session_id):
        if dataset.get("storage_mode") == "inline":
            payload[dataset["name"]] = _inline_entry(dataset)
            continue
        if dataset.get("records") is not None:
            try:
                datasets_store.upsert_dataset(dataset)
            except Exception as exc:
                print(f"[dataset] Warning: could not refresh spillover row '{dataset['name']}': {exc}", flush=True)
        payload[dataset["name"]] = _manifest(dataset)
    return payload


def build_persisted_scratchpad_payload(named_scratch: dict[str, str]) -> dict:
    return dict(named_scratch)


def _coerce_history_items(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _coerce_schema(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _restore_dataset_entry(validated_name: str, entry: dict, resolved: str) -> dict:
    inline = bool(entry.get("inline"))
    records = None
    if inline:
        try:
            records = _coerce_records(entry.get("records") or [])
        except ValueError as exc:
            raise ValueError(f"invalid inline records: {exc}") from exc

    history = _coerce_history_items(entry.get("history")) or _coerce_history_items(entry.get("history_tail"))
    dataset_id = str(entry.get("dataset_id") or _new_dataset_id()).strip() or _new_dataset_id()
    return {
        "dataset_id": dataset_id,
        "session_id": resolved,
        "name": validated_name,
        "records": records,
        "count": _coerce_non_negative_int(entry.get("count"), len(records or [])),
        "schema": _coerce_schema(entry.get("schema")),
        "source_tool": str(entry.get("source_tool") or ""),
        "source_args": _coerce_source_args(entry.get("source_args")),
        "parent_dataset_id": str(entry.get("parent_dataset_id") or ""),
        "history": history,
        "created_at": str(entry.get("created_at") or _utc_now()),
        "updated_at": str(entry.get("updated_at") or _utc_now()),
        "storage_mode": "inline" if inline else "spillover",
        "auto_named": bool(entry.get("auto_named")),
        "missing_spillover": bool(entry.get("missing_spillover")),
    }


def restore_persisted_datasets(payload: object, session_id: str | None = None) -> None:
    resolved = _resolve_session_id(session_id)
    if not isinstance(payload, dict):
        return
    restored: dict[str, dict] = {}
    for raw_name, entry in payload.items():
        try:
            validated_name = _validate_name(raw_name)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            print(f"[dataset] Warning: skipping persisted dataset '{validated_name}' because its payload is not an object.", flush=True)
            continue
        try:
            dataset = _restore_dataset_entry(validated_name, entry, resolved)
        except ValueError as exc:
            print(f"[dataset] Warning: skipping persisted dataset '{validated_name}': {exc}", flush=True)
            continue
        restored[validated_name] = dataset
    with _DATASET_LOCK:
        _SESSION_DATASETS[resolved] = restored


def clear_session_datasets(session_id: str | None = None) -> None:
    resolved = _resolve_session_id(session_id)
    with _DATASET_LOCK:
        _SESSION_DATASETS[resolved] = {}


def delete_session_datasets(session_id: str | None = None) -> None:
    resolved = _resolve_session_id(session_id)
    clear_session_datasets(resolved)
    datasets_store.delete_session_datasets(resolved)


def _format_manifest_line(dataset: dict) -> str:
    fields = ",".join((dataset.get("schema") or [])[:5])
    last_history = (dataset.get("history") or [])[-1] if dataset.get("history") else {}
    last_op = last_history.get("op", "save")
    return (
        f"  {dataset['name']:<24} {int(dataset.get('count', len(dataset.get('records') or []))):>4} records  "
        f"fields=[{fields}]  last={last_op}"
    )


def _missing_spillover_error(name: str, dataset_id: str) -> str:
    return (
        f"Dataset '{name}' refers to missing spillover row {dataset_id}. "
        "Re-fetch the source data or delete this dataset handle."
    )


def _json_dataset_error(name: str, error_text: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "name": str(name or "").strip().lower(),
            "error": error_text,
        },
        indent=2,
        ensure_ascii=False,
    )


def dataset_save(
    name: str,
    records: list[dict],
    source_tool: str = "",
    source_args: dict = None,
    replace: bool = False,
    session_id: str | None = None,
) -> str:
    """Create a named dataset from structured records."""
    try:
        normalized_records = _coerce_records(records)
        dataset = _save_dataset_internal(
            name=name,
            records=normalized_records,
            source_tool=source_tool,
            source_args=source_args,
            history=[_history_entry(op="save", kept=len(normalized_records), dropped=0, replaced=replace)],
            replace=replace,
            session_id=session_id,
        )
    except ValueError as exc:
        return f"Error: {exc}"
    except LookupError:
        return f"Dataset '{_validate_name(name)}' not found."
    fields = ",".join(dataset.get("schema") or [])
    return f"Saved dataset '{dataset['name']}' ({len(normalized_records)} records, id={dataset['dataset_id']}, fields=[{fields}])."


def dataset_rename(name: str, new_name: str, session_id: str | None = None) -> str:
    """Rename a dataset without changing its immutable dataset_id."""
    validated_old = ""
    try:
        validated_old = _validate_name(name)
        dataset = _get_dataset(validated_old, session_id)
        validated_new = _validate_name(new_name)
    except ValueError as exc:
        return f"Error: {exc}"
    except LookupError:
        return f"Dataset '{validated_old or str(name).strip().lower()}' not found."
    except FileNotFoundError:
        store = _session_map(session_id)
        dataset_stub = store.get(validated_old, {})
        return _missing_spillover_error(validated_old, str(dataset_stub.get("dataset_id", "unknown")))

    store = _session_map(session_id)
    with _DATASET_LOCK:
        if validated_new in store:
            return f"Error: dataset '{validated_new}' already exists."
        old_name = dataset["name"]
        del store[old_name]
        dataset["name"] = validated_new
        dataset["updated_at"] = _utc_now()
        dataset.setdefault("history", []).append(_history_entry(op="rename", kept=len(dataset.get("records") or []), dropped=0))
        store[validated_new] = dataset
    if dataset.get("storage_mode") == "spillover":
        datasets_store.upsert_dataset(dataset)
    return f"Renamed dataset '{old_name}' -> '{validated_new}' (id={dataset['dataset_id']})."


def dataset_list(session_id: str | None = None) -> str:
    """List active dataset manifests for the session."""
    manifests = get_prompt_dataset_manifests(session_id)
    if not manifests:
        return "No datasets stored."
    return "Datasets:\n" + "\n".join(_format_manifest_line(item) for item in manifests)


def dataset_inspect(name: str, session_id: str | None = None) -> str:
    """Return a compact manifest plus sample records for one dataset."""
    try:
        dataset = _get_dataset(name, session_id)
    except ValueError as exc:
        return _json_dataset_error(name, f"Error: {exc}")
    except LookupError:
        return _json_dataset_error(name, f"Dataset '{_validate_name(name)}' not found.")
    except FileNotFoundError as exc:
        return _json_dataset_error(name, _missing_spillover_error(_validate_name(name), str(exc)))

    sample = [_project_record(record, list(dataset.get("schema") or [])[:5], 250) for record in (dataset.get("records") or [])[:3]]
    payload = {
        "ok": True,
        "dataset_id": dataset["dataset_id"],
        "name": dataset["name"],
        "count": len(dataset.get("records") or []),
        "schema": dataset.get("schema") or [],
        "source_tool": dataset.get("source_tool") or "",
        "parent_dataset_id": dataset.get("parent_dataset_id") or "",
        "updated_at": dataset.get("updated_at") or "",
        "storage_mode": dataset.get("storage_mode") or "inline",
        "history_tail": (dataset.get("history") or [])[-3:],
        "sample": sample,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def dataset_get(
    name: str,
    indices: list[int] = None,
    max_records: int = 0,
    fields: list[str] = None,
    offset: int = 0,
    limit: int = 0,
    session_id: str | None = None,
) -> str:
    """Return specific records or a bounded slice from a dataset."""
    try:
        dataset = _get_dataset(name, session_id)
    except ValueError as exc:
        return _json_dataset_error(name, f"Error: {exc}")
    except LookupError:
        return _json_dataset_error(name, f"Dataset '{_validate_name(name)}' not found.")
    except FileNotFoundError as exc:
        return _json_dataset_error(name, _missing_spillover_error(_validate_name(name), str(exc)))

    records = dataset.get("records") or []
    selected, selection = _select_records(records, indices=indices, offset=offset, limit=limit, max_records=max_records)
    if fields:
        selected = [{field: record.get(field) for field in fields if field in record} for record in selected]
    payload = {
        "ok": True,
        "dataset_id": dataset["dataset_id"],
        "name": dataset["name"],
        "total_count": len(records),
        "fields": list(fields or []),
        **selection,
        "records": selected,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def dataset_write_koredoc(
    name: str,
    folder_path: str,
    document_name: str = "",
    fields: list[str] = None,
    offset: int = 0,
    limit: int = 0,
    session_id: str | None = None,
) -> str:
    """Write dataset records directly to a KoreDocs .koredoc file without model-side reformatting."""
    try:
        dataset = _get_dataset(name, session_id)
    except ValueError as exc:
        return f"Error: {exc}"
    except LookupError:
        return f"Dataset '{_validate_name(name)}' not found."
    except FileNotFoundError as exc:
        return _missing_spillover_error(_validate_name(name), str(exc))

    export_fields = list(fields or dataset.get("schema") or _infer_schema(dataset.get("records") or []))
    records = dataset.get("records") or []
    start = _coerce_non_negative_int(offset)
    export_limit = _coerce_non_negative_int(limit)
    if export_limit <= 0:
        export_limit = max(0, len(records) - start)
    selected, selection = _select_records(records, offset=start, limit=export_limit)
    selection["total_count"] = len(records)
    markdown = _dataset_records_to_markdown(dataset, selected, selection, export_fields)

    target_folder = _normalise_koredocs_folder(folder_path)
    target_name = _normalise_koredoc_name(document_name, dataset["name"])

    from system_skills.FileAccess.file_access_skill import file_write

    target_path = f"{target_folder}/{target_name}" if target_folder else target_name
    write_result = file_write(target_path, markdown, skip_content_guard=True)
    if write_result.startswith("Error:"):
        return write_result

    exported_range_start = selection["offset"] + 1 if selection["returned"] else 0
    exported_range_end = selection["offset"] + selection["returned"]
    return (
        f"Exported dataset '{dataset['name']}' records {exported_range_start}-{exported_range_end} "
        f"of {len(records)} to KoreDocs document '{target_name}' at '{target_path}'."
    )


def dataset_expand_full_text(
    name: str,
    save_as: str = "",
    replace: bool = False,
    offset: int = 0,
    limit: int = 0,
    session_id: str | None = None,
) -> str:
    """Fetch full-text payloads for dataset rows that carry KoreData artifact_ref values."""
    if replace and save_as:
        return "Error: save_as and replace=True cannot be used together."
    try:
        dataset = _get_dataset(name, session_id)
    except ValueError as exc:
        return f"Error: {exc}"
    except LookupError:
        return f"Dataset '{_validate_name(name)}' not found."
    except FileNotFoundError as exc:
        return _missing_spillover_error(_validate_name(name), str(exc))

    records = dataset.get("records") or []
    start = _coerce_non_negative_int(offset)
    expand_limit = _coerce_non_negative_int(limit)
    if expand_limit <= 0:
        expand_limit = max(0, len(records) - start)
    selected, selection = _select_records(records, offset=start, limit=expand_limit)
    if not selected:
        return f"Error: dataset '{dataset['name']}' selection is empty."

    try:
        with httpx.Client(timeout=_FULL_TEXT_TIMEOUT_SECS) as client:
            expanded_records, failures = _expand_records_with_full_text(selected, client=client)
    except Exception as exc:
        return f"Error during dataset_expand_full_text: {exc}"

    if not expanded_records:
        detail = f" First failure: {failures[0]}." if failures else ""
        return f"Error during dataset_expand_full_text: no records were expanded.{detail}"

    target_name = dataset["name"] if replace else (save_as.strip().lower() if save_as else _derive_name(dataset["name"], "fulltext", session_id))
    history = list(dataset.get("history") or [])
    history.append(
        _history_entry(
            op="expand_full_text",
            prompt=f"offset={selection['offset']}, limit={selection['limit']}",
            kept=len(expanded_records),
            dropped=len(selected) - len(expanded_records),
            fields=["artifact_ref"],
            replaced=replace,
        )
    )
    saved = _save_dataset_internal(
        name=target_name,
        records=expanded_records,
        source_tool="dataset_expand_full_text",
        source_args={
            "from_dataset": dataset["name"],
            "offset": selection["offset"],
            "limit": selection["limit"],
        },
        parent_dataset_id="" if replace else dataset["dataset_id"],
        history=history,
        replace=replace,
        existing_dataset=dataset if replace else None,
        session_id=session_id,
    )
    action = "Replaced" if replace else "Created"
    message = (
        f"{action} dataset '{saved['name']}' from '{dataset['name']}' via full-text expansion - "
        f"expanded {len(expanded_records)}/{len(selected)} selected records."
    )
    if failures:
        message += f" Skipped {len(failures)} record(s): {'; '.join(failures[:3])}"
        if len(failures) > 3:
            message += " ..."
    return message


def dataset_delete(name: str, session_id: str | None = None) -> str:
    """Delete one dataset and its spillover row if present."""
    try:
        validated = _validate_name(name)
    except ValueError as exc:
        return f"Error: {exc}"
    store = _session_map(session_id)
    with _DATASET_LOCK:
        dataset = store.pop(validated, None)
    if dataset is None:
        return f"Dataset '{validated}' not found - nothing deleted."
    datasets_store.delete_dataset(dataset["dataset_id"])
    return f"Deleted dataset '{validated}'."


def _apply_drop_predicate(records: list[dict], predicate: str) -> tuple[list[dict], int, int]:
    cleaned = (predicate or "").strip()
    match = _DUPLICATE_RE.match(cleaned)
    if match:
        field = match.group(1)
        seen: set[str] = set()
        kept_records: list[dict] = []
        dropped = 0
        for record in records:
            value = str(record.get(field, "")).strip().lower()
            if value and value in seen:
                dropped += 1
                continue
            if value:
                seen.add(value)
            kept_records.append(record)
        return kept_records, len(kept_records), dropped

    match = _MISSING_RE.match(cleaned)
    if match:
        field = match.group(1)
        kept_records = [record for record in records if str(record.get(field, "")).strip()]
        return kept_records, len(kept_records), len(records) - len(kept_records)

    match = _REGEX_RE.match(cleaned)
    if match:
        field, pattern = match.groups()
        regex = re.compile(pattern, re.IGNORECASE)
        kept_records = [record for record in records if not regex.search(str(record.get(field, "")))]
        return kept_records, len(kept_records), len(records) - len(kept_records)

    match = _CMP_RE.match(cleaned)
    if match:
        field, operator, raw_value = match.groups()
        raw_value = raw_value.strip()

        def _compare(candidate: object) -> bool:
            left = candidate
            right: object = raw_value
            try:
                left_num = float(left)
                right_num = float(raw_value)
                return left_num < right_num if operator == "<" else left_num > right_num
            except Exception:
                left_text = str(left or "")
                return left_text < str(right) if operator == "<" else left_text > str(right)

        kept_records = [record for record in records if not _compare(record.get(field))]
        return kept_records, len(kept_records), len(records) - len(kept_records)

    raise ValueError(
        "Unsupported predicate. Use 'duplicate by <field>', 'missing field <field>', '<field> ~= <regex>', '<field> < <value>', or '<field> > <value>'."
    )


def dataset_drop_where(
    name: str,
    predicate: str,
    save_as: str = "",
    replace: bool = False,
    session_id: str | None = None,
) -> str:
    """Apply a deterministic drop predicate to a dataset."""
    try:
        dataset = _get_dataset(name, session_id)
        new_records, kept, dropped = _apply_drop_predicate(dataset.get("records") or [], predicate)
    except ValueError as exc:
        return f"Error: {exc}"
    except LookupError:
        return f"Dataset '{_validate_name(name)}' not found."
    except FileNotFoundError as exc:
        return _missing_spillover_error(_validate_name(name), str(exc))

    if replace and save_as:
        return "Error: save_as and replace=True cannot be used together."

    target_name = dataset["name"] if replace else (save_as.strip().lower() if save_as else _derive_name(dataset["name"], "drop", session_id))
    history = list(dataset.get("history") or [])
    history.append(_history_entry(op="drop_where", prompt=predicate, kept=kept, dropped=dropped, replaced=replace))
    saved = _save_dataset_internal(
        name=target_name,
        records=new_records,
        source_tool=dataset.get("source_tool") or "",
        source_args=dataset.get("source_args"),
        parent_dataset_id="" if replace else dataset["dataset_id"],
        history=history,
        replace=replace,
        existing_dataset=dataset if replace else None,
        session_id=session_id,
    )
    action = "Replaced" if replace else "Created"
    return f"{action} dataset '{saved['name']}' from '{dataset['name']}' via drop predicate '{predicate}' - kept {kept}/{kept + dropped} records."


def _extract_first_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON object found")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
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
                return text[start:index + 1]
    raise ValueError("Incomplete JSON object")


def _call_filter_llm(filter_prompt: str, projected_record: dict) -> tuple[bool, str]:
    try:
        from llm_client import call_llm_chat as _call_llm_chat
        from llm_client import get_active_model as _get_active_model
        from llm_client import get_active_num_ctx as _get_active_num_ctx
    except Exception as exc:
        raise RuntimeError(f"Error importing LLM client: {exc}") from exc

    model = _get_active_model()
    num_ctx = _get_active_num_ctx()
    if not model:
        raise RuntimeError("no active model available. Run a prompt first.")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a strict record filter. Given a user filter instruction and one projected record, "
                "return ONLY a JSON object with keys keep (boolean) and reason (string)."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Instruction: {filter_prompt}\n\n"
                f"Record:\n{json.dumps(projected_record, ensure_ascii=False)}\n\n"
                "Return JSON only."
            ),
        },
    ]
    result = _call_llm_chat(model_name=model, messages=messages, tools=None, num_ctx=min(num_ctx, 8192))
    raw = (result.response or "").strip()
    if not raw:
        raise RuntimeError("LLM returned an empty response")
    try:
        parsed = json.loads(_extract_first_json_object(raw))
    except Exception:
        lowered = raw.lower()
        return ("true" in lowered or lowered.startswith("keep"), raw[:120])
    return bool(parsed.get("keep")), str(parsed.get("reason") or "")[:120]


def dataset_filter(
    name: str,
    prompt: str,
    save_as: str = "",
    replace: bool = False,
    fields: list[str] = None,
    excerpt_chars: int = 300,
    session_id: str | None = None,
) -> str:
    """Apply one LLM-driven keep/drop pass over projected records."""
    if not prompt or not str(prompt).strip():
        return "Error: prompt cannot be empty."
    if replace and save_as:
        return "Error: save_as and replace=True cannot be used together."
    try:
        dataset = _get_dataset(name, session_id)
    except ValueError as exc:
        return f"Error: {exc}"
    except LookupError:
        return f"Dataset '{_validate_name(name)}' not found."
    except FileNotFoundError as exc:
        return _missing_spillover_error(_validate_name(name), str(exc))

    projected_fields = list(fields or [])
    kept_records: list[dict] = []
    dropped = 0
    for record in dataset.get("records") or []:
        projected = _project_record(record, projected_fields or None, int(excerpt_chars or 0))
        try:
            keep, _reason = _call_filter_llm(prompt, projected)
        except Exception as exc:
            return f"Error during dataset_filter: {exc}"
        if keep:
            kept_records.append(record)
        else:
            dropped += 1

    target_name = dataset["name"] if replace else (save_as.strip().lower() if save_as else _derive_name(dataset["name"], "filter", session_id))
    history = list(dataset.get("history") or [])
    history.append(
        _history_entry(
            op="filter",
            prompt=prompt,
            kept=len(kept_records),
            dropped=dropped,
            fields=projected_fields or list((_project_record((dataset.get("records") or [{}])[0], None, int(excerpt_chars or 0))).keys()),
            replaced=replace,
        )
    )
    saved = _save_dataset_internal(
        name=target_name,
        records=kept_records,
        source_tool=dataset.get("source_tool") or "",
        source_args=dataset.get("source_args"),
        parent_dataset_id="" if replace else dataset["dataset_id"],
        history=history,
        replace=replace,
        existing_dataset=dataset if replace else None,
        session_id=session_id,
    )
    action = "Replaced" if replace else "Created"
    return f"{action} dataset '{saved['name']}' from '{dataset['name']}' via filter - kept {len(kept_records)}/{len(dataset.get('records') or [])} records."


def ingest_auto_dataset(source_tool: str, source_args: dict, records: list[dict], session_id: str | None = None) -> str:
    auto_name = _auto_name(source_tool, session_id)
    normalized_records = _coerce_records(records)
    dataset = _save_dataset_internal(
        name=auto_name,
        records=normalized_records,
        source_tool=source_tool,
        source_args=source_args,
        history=[_history_entry(op="save", kept=len(normalized_records), dropped=0, replaced=False)],
        replace=False,
        auto_named=True,
        session_id=session_id,
    )
    fields = ",".join(dataset.get("schema") or [])
    return (
        f"Dataset '{dataset['name']}' created: {len(normalized_records)} records, fields=[{fields}]. "
        "If the user asked for a specific dataset name, use dataset_rename on this dataset "
        "instead of rebuilding records from a truncated preview."
    )


def auto_route_tool_result(tool_name: str, arguments: dict, raw_result: object, session_id: str | None = None) -> str | None:
    normalized_tool = str(tool_name or "").strip().lower()
    if normalized_tool.startswith("dataset_"):
        return None

    records: list[dict] | None = None
    parsed_result = raw_result
    if isinstance(parsed_result, str):
        stripped = parsed_result.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed_result = json.loads(stripped)
            except Exception:
                parsed_result = raw_result
    if isinstance(parsed_result, list) and parsed_result and all(isinstance(item, dict) for item in parsed_result):
        records = [dict(item) for item in parsed_result]
    elif isinstance(parsed_result, dict):
        nested = parsed_result.get("results")
        if isinstance(nested, list) and nested and all(isinstance(item, dict) for item in nested):
            records = [dict(item) for item in nested]
    if not records or len(records) < 5:
        return None
    shared_keys = set(records[0].keys()) if records else set()
    for item in records[1:]:
        shared_keys &= set(item.keys())
    if not shared_keys.intersection({"id", "url", "guid", "slug", "title", "published_at"}):
        return None
    return ingest_auto_dataset(tool_name, arguments, records, session_id=session_id)
