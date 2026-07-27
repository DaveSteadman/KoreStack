from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_PROCESSING_SCHEDULES = frozenset({"manual", "daily", "weekly", "monthly"})


def _service_not_ready_error() -> dict:
    return {"error": "KoreDataGateway is still starting up — retry in a moment"}


async def get_rag_chunk(
    client,
    *,
    chunk_id: int,
) -> dict:
    if client is None:
        return _service_not_ready_error()
    response = await client.get(f"/chunks/{chunk_id}", timeout=10.0)
    if response.status_code == 404:
        return {"error": f"RAG chunk not found: id={chunk_id}"}
    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}"}
    return response.json()


def normalise_processing_schedule(value: object) -> str:
    """Return a supported RAG processing schedule, defaulting safely to manual."""
    schedule = str(value or "").strip().lower()
    return schedule if schedule in _PROCESSING_SCHEDULES else "manual"


async def enrich_databases(client, *, data_root: Path) -> list[dict[str, Any]]:
    """Add optional RAG database details without losing the base service payload."""
    if client is None:
        return []

    response = await client.get("/databases")
    if response.status_code != 200:
        return []
    databases = response.json() or []
    if not isinstance(databases, list):
        return []

    enriched: list[dict[str, Any]] = []
    for database in databases:
        if not isinstance(database, dict):
            continue
        item = dict(database)
        database_id = str(item.get("id") or "").strip()
        if not database_id:
            enriched.append(item)
            continue

        try:
            detail_response = await client.get(f"/databases/{database_id}/info")
            detail = detail_response.json() if detail_response.status_code == 200 else {}
        except Exception:
            detail = {}
        if isinstance(detail, dict):
            item.update({key: value for key, value in detail.items() if value is not None})

        if item.get("db_size_bytes") is None:
            db_path = data_root / "RAG" / "databases" / database_id / f"{database_id}.db"
            if db_path.is_file():
                item["db_size_bytes"] = db_path.stat().st_size
        enriched.append(item)
    return enriched


def list_processing_scripts(data_root: Path, database_ids: set[str]) -> list[dict[str, Any]]:
    """Read processing metadata for the selected RAG databases from their descriptors."""
    scripts: list[dict[str, Any]] = []
    databases_dir = data_root / "RAG" / "databases"
    for database_id in sorted(database_ids):
        database_dir = databases_dir / database_id
        descriptor_path = database_dir / f"{database_id}.json"
        descriptor: dict[str, Any] = {}
        if descriptor_path.is_file():
            try:
                parsed = json.loads(descriptor_path.read_text(encoding="utf-8"))
                descriptor = parsed if isinstance(parsed, dict) else {}
            except (OSError, json.JSONDecodeError):
                descriptor = {}
        sync = descriptor.get("sync") if isinstance(descriptor.get("sync"), dict) else {}
        scripts.append(
            {
                "id":                       database_id,
                "display_name":             descriptor.get("display_name") or database_id,
                "managed_by":               descriptor.get("managed_by") or "",
                "schedule":                 normalise_processing_schedule(descriptor.get("schedule")),
                "last_run":                 sync.get("last_run"),
                "last_ingest_completed_at": sync.get("last_ingest_completed_at"),
                "last_date_ingested":       sync.get("last_date_ingested"),
                "status":                   sync.get("status"),
                "has_database":             database_dir.is_dir(),
                "has_script":               (database_dir / "ingest.py").is_file(),
            }
        )
    return scripts
