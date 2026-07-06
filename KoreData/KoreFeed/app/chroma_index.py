import logging
import shutil
import threading
from pathlib import Path
from typing import Any, Optional

try:
    import chromadb
except ModuleNotFoundError:
    chromadb = None

from app.database import (
    _sanitize_domain,
    get_entry_sentences,
    get_sentence,
    get_sentences_for_chroma,
    list_domains,
    mark_sentences_chroma_indexed,
    reset_sentence_chroma_index,
)
from app.config import cfg


LOG = logging.getLogger("korefeed.chroma")

_CHROMA_ROOT             = Path(cfg["data_dir"]) / "_chroma"
_COLLECTION_NAME         = "sentences"
_COLLECTION_CONFIGURATION = {"hnsw": {"space": "cosine"}}
_STORE_SCHEMA_VERSION    = "cosine-v2"
_STORE_SCHEMA_FILE       = ".schema"
_CLIENT_LOCK             = threading.Lock()
_CLIENTS: dict[str, Any] = {}


def chroma_available() -> bool:
    return chromadb is not None


def _distance_to_match_score(distance: Optional[float]) -> Optional[float]:
    if distance is None:
        return None
    # Cosine-space collections return cosine distance (0 best, 2 worst).
    # Expose cosine similarity-style scoring for UI thresholding.
    return max(0.0, min(1.0, 1.0 - float(distance)))


def _domain_chroma_path(domain: str) -> Path:
    return _CHROMA_ROOT / _sanitize_domain(domain)


def _domain_schema_marker_path(domain: str) -> Path:
    return _domain_chroma_path(domain) / _STORE_SCHEMA_FILE


def _domain_store_is_current(domain: str) -> bool:
    marker_path = _domain_schema_marker_path(domain)
    try:
        return marker_path.exists() and marker_path.read_text(encoding="utf-8").strip() == _STORE_SCHEMA_VERSION
    except Exception:
        return False


def _mark_domain_store_current(domain: str) -> None:
    marker_path = _domain_schema_marker_path(domain)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(_STORE_SCHEMA_VERSION, encoding="utf-8")


def _release_domain_client(domain: str) -> None:
    safe_domain = _sanitize_domain(domain)
    with _CLIENT_LOCK:
        client = _CLIENTS.pop(safe_domain, None)
    if client is None:
        return
    try:
        client.close()
    except Exception:
        pass
    try:
        server = getattr(client, "_server", None)
        if server is not None and hasattr(server, "stop"):
            server.stop()
    except Exception:
        pass


def _get_collection(domain: str):
    if chromadb is None:
        raise RuntimeError("chromadb is not installed")
    safe_domain = _sanitize_domain(domain)
    with _CLIENT_LOCK:
        client = _CLIENTS.get(safe_domain)
        if client is None:
            path = _domain_chroma_path(domain)
            path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(path))
            _CLIENTS[safe_domain] = client
        collection = client.get_or_create_collection(
            name          = _COLLECTION_NAME,
            configuration = _COLLECTION_CONFIGURATION,
        )
        if not _domain_store_is_current(domain):
            _mark_domain_store_current(domain)
        return collection


def _upsert_rows(domain: str, rows: list[dict]) -> int:
    if chromadb is None:
        return 0
    if not rows:
        return 0
    collection = _get_collection(domain)
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    sentence_ids: list[int] = []

    for row in rows:
        sentence_text = str(row.get("sentence_text") or "").strip()
        if not sentence_text:
            continue
        sentence_id = int(row["id"])
        ids.append(str(row["locator"]))
        documents.append(sentence_text)
        metadatas.append(
            {
                "service": "feeds",
                "domain": domain,
                "entry_id": int(row["entry_id"]),
                "sentence_id": sentence_id,
                "sentence_index": int(row["sentence_index"]),
                "source_field": str(row["source_field"]),
                "char_start": int(row["char_start"]),
                "char_end": int(row["char_end"]),
                "feed_name": str(row.get("feed_name") or ""),
                "headline": str(row.get("headline") or ""),
                "published": str(row.get("published") or ""),
                "url": str(row.get("url") or ""),
            }
        )
        sentence_ids.append(sentence_id)

    if not ids:
        return 0

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    mark_sentences_chroma_indexed(domain, sentence_ids)
    return len(sentence_ids)


def sync_entry_sentences(domain: str, entry_id: int) -> int:
    if chromadb is None:
        return 0
    sentence_ids = [int(row["id"]) for row in get_entry_sentences(domain, entry_id)]
    rows = get_sentences_for_chroma(
        domain,
        limit=max(len(sentence_ids), 1),
        sentence_ids=sentence_ids,
    )
    return _upsert_rows(domain, rows)


def sync_pending_sentences(domain: str, batch_size: int = 250, max_batches: Optional[int] = None) -> int:
    if chromadb is None:
        return 0
    synced = 0
    batches = 0
    while True:
        if max_batches is not None and batches >= max_batches:
            break
        rows = get_sentences_for_chroma(
            domain,
            limit=max(1, int(batch_size)),
            only_unindexed=True,
        )
        if not rows:
            break
        synced += _upsert_rows(domain, rows)
        batches += 1
    return synced


def sync_all_domains_pending(batch_size: int = 250, max_batches_per_domain: Optional[int] = None) -> dict[str, int]:
    if chromadb is None:
        return {domain: 0 for domain in list_domains()}
    counts: dict[str, int] = {}
    for domain in list_domains():
        try:
            counts[domain] = sync_pending_sentences(
                domain,
                batch_size=batch_size,
                max_batches=max_batches_per_domain,
            )
        except Exception as exc:
            LOG.warning("Chroma catchup failed for domain %s: %s", domain, exc)
            counts[domain] = 0
    return counts


def rebuild_domain_store(domain: str, batch_size: int = 250) -> dict[str, Any]:
    if chromadb is None:
        return {"domain": domain, "rebuilt": False, "reason": "chromadb unavailable", "indexed": 0}

    delete_domain_store(domain)
    reset_sentence_chroma_index(domain)
    _get_collection(domain)
    indexed = sync_pending_sentences(domain, batch_size=max(1, int(batch_size)))
    return {
        "domain":  domain,
        "rebuilt": True,
        "indexed": indexed,
    }


def rebuild_all_domain_stores(batch_size: int = 250) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for domain in list_domains():
        try:
            results[domain] = rebuild_domain_store(domain, batch_size=batch_size)
        except Exception as exc:
            results[domain] = {
                "domain":  domain,
                "rebuilt": False,
                "reason":  str(exc),
                "indexed": 0,
            }
    return results


def migrate_legacy_domain_stores(batch_size: int = 250) -> dict[str, dict[str, Any]]:
    if chromadb is None:
        return {
            domain: {"domain": domain, "rebuilt": False, "reason": "chromadb unavailable", "indexed": 0}
            for domain in list_domains()
        }

    results: dict[str, dict[str, Any]] = {}
    for domain in list_domains():
        path = _domain_chroma_path(domain)
        if path.exists() and not _domain_store_is_current(domain):
            try:
                results[domain] = rebuild_domain_store(domain, batch_size=batch_size)
            except Exception as exc:
                results[domain] = {
                    "domain":  domain,
                    "rebuilt": False,
                    "reason":  str(exc),
                    "indexed": 0,
                }
        else:
            results[domain] = {
                "domain":  domain,
                "rebuilt": False,
                "reason":  "already current",
                "indexed": 0,
            }
    return results


def semantic_search(
    domain: Optional[str],
    query: str,
    limit: int = 20,
    min_match: float = 0.0,
) -> list[dict]:
    if chromadb is None:
        LOG.info("Semantic search unavailable: chromadb is not installed.")
        return []
    text = str(query or "").strip()
    if not text:
        return []

    domains          = [domain] if domain else list_domains()
    per_domain_limit = max(1, int(limit))
    min_match        = max(0.0, min(1.0, float(min_match or 0.0)))
    results: list[dict] = []

    for current_domain in domains:
        path = _domain_chroma_path(current_domain)
        if not path.exists():
            continue
        try:
            collection = _get_collection(current_domain)
            if collection.count() <= 0:
                continue
            response = collection.query(
                query_texts=[text],
                n_results=per_domain_limit,
            )
        except Exception as exc:
            LOG.warning("Semantic search failed for domain %s: %s", current_domain, exc)
            continue

        ids = (response.get("ids") or [[]])[0]
        documents = (response.get("documents") or [[]])[0]
        metadatas = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]

        for idx, locator in enumerate(ids):
            metadata = metadatas[idx] if idx < len(metadatas) and metadatas[idx] else {}
            document = documents[idx] if idx < len(documents) else ""
            distance = distances[idx] if idx < len(distances) else None
            match_score = _distance_to_match_score(distance)
            if match_score is not None and match_score < min_match:
                continue
            sentence_id = metadata.get("sentence_id")
            sentence_row = None
            try:
                if sentence_id is not None:
                    sentence_row = get_sentence(current_domain, int(sentence_id))
            except Exception:
                sentence_row = None

            entry_id = metadata.get("entry_id")
            if sentence_row:
                entry_id = sentence_row.get("entry_id", entry_id)
            headline = metadata.get("headline") or (sentence_row.get("headline") if sentence_row else "")
            feed_name = metadata.get("feed_name") or (sentence_row.get("feed_name") if sentence_row else "")
            published = metadata.get("published") or (sentence_row.get("published") if sentence_row else "")
            url = metadata.get("url") or (sentence_row.get("url") if sentence_row else "")
            snippet = (sentence_row.get("sentence_text") if sentence_row else "") or document or ""

            results.append(
                {
                    "id": int(entry_id) if entry_id is not None else None,
                    "sentence_id": int(sentence_id) if sentence_id is not None else None,
                    "sentence_locator": str(locator or ""),
                    "domain": current_domain,
                    "feed_name": str(feed_name or ""),
                    "headline": str(headline or ""),
                    "published": str(published or ""),
                    "url": str(url or ""),
                    "snippet": str(snippet or ""),
                    "distance":    float(distance) if distance is not None else None,
                    "match_score": float(match_score) if match_score is not None else None,
                }
            )

    results.sort(key=lambda row: (row["match_score"] is None, -(row["match_score"] or 0.0), row["published"] or ""), reverse=False)
    return results[:limit]


def delete_sentence_ids(domain: str, sentence_ids: list[int]) -> int:
    if chromadb is None:
        return 0
    if not sentence_ids:
        return 0
    collection = _get_collection(domain)
    locators = [f"feeds/{domain}/{int(sentence_id)}" for sentence_id in sentence_ids]
    collection.delete(ids=locators)
    return len(locators)


def delete_domain_store(domain: str) -> bool:
    if chromadb is None:
        return False
    path = _domain_chroma_path(domain)
    _release_domain_client(domain)
    if not path.exists():
        return False
    shutil.rmtree(path)
    return True


def rename_domain_store(old: str, new: str) -> bool:
    if chromadb is None:
        return False
    old_path = _domain_chroma_path(old)
    if not old_path.exists():
        return False
    new_path = _domain_chroma_path(new)
    _release_domain_client(old)
    _release_domain_client(new)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)
    return True
