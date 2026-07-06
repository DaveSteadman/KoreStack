import logging
import shutil
import threading
from pathlib import Path
from typing import Any, Optional

try:
    import chromadb
except ModuleNotFoundError:
    chromadb = None

from app.config import cfg
from app.database import (
    get_book_sentences,
    get_sentence,
    get_sentences_for_chroma,
    list_writable_catalogs,
    mark_sentences_chroma_indexed,
    reset_sentence_chroma_index,
)


LOG = logging.getLogger("korelibrary.chroma")

_CHROMA_ROOT              = Path(cfg["data_dir"]) / "_chroma"
_COLLECTION_NAME          = "sentences"
_COLLECTION_CONFIGURATION = {"hnsw": {"space": "cosine"}}
_STORE_SCHEMA_VERSION     = "cosine-v1"
_STORE_SCHEMA_FILE        = ".schema"
_CLIENT_LOCK              = threading.Lock()
_CLIENTS: dict[str, Any]  = {}


def chroma_available() -> bool:
    return chromadb is not None


def _distance_to_match_score(distance: Optional[float]) -> Optional[float]:
    if distance is None:
        return None
    return max(0.0, min(1.0, 1.0 - float(distance)))


def _catalog_chroma_path(catalog: str) -> Path:
    return _CHROMA_ROOT / str(catalog).strip().lower()


def _catalog_schema_marker_path(catalog: str) -> Path:
    return _catalog_chroma_path(catalog) / _STORE_SCHEMA_FILE


def _catalog_store_is_current(catalog: str) -> bool:
    marker_path = _catalog_schema_marker_path(catalog)
    try:
        return marker_path.exists() and marker_path.read_text(encoding="utf-8").strip() == _STORE_SCHEMA_VERSION
    except Exception:
        return False


def _mark_catalog_store_current(catalog: str) -> None:
    marker_path = _catalog_schema_marker_path(catalog)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(_STORE_SCHEMA_VERSION, encoding="utf-8")


def _release_catalog_client(catalog: str) -> None:
    safe_catalog = str(catalog).strip().lower()
    with _CLIENT_LOCK:
        client = _CLIENTS.pop(safe_catalog, None)
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


def _get_collection(catalog: str):
    if chromadb is None:
        raise RuntimeError("chromadb is not installed")
    safe_catalog = str(catalog).strip().lower()
    with _CLIENT_LOCK:
        client = _CLIENTS.get(safe_catalog)
        if client is None:
            path = _catalog_chroma_path(catalog)
            path.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(path))
            _CLIENTS[safe_catalog] = client
        collection = client.get_or_create_collection(
            name          = _COLLECTION_NAME,
            configuration = _COLLECTION_CONFIGURATION,
        )
        if not _catalog_store_is_current(catalog):
            _mark_catalog_store_current(catalog)
        return collection


def _upsert_rows(catalog: str, rows: list[dict]) -> int:
    if chromadb is None or not rows:
        return 0
    collection = _get_collection(catalog)
    ids:         list[str]  = []
    documents:   list[str]  = []
    metadatas:   list[dict] = []
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
                "service":        "library",
                "catalog":        str(catalog),
                "book_id":        int(row["book_id"]),
                "sentence_id":    sentence_id,
                "sentence_index": int(row["sentence_index"]),
                "source_field":   str(row["source_field"]),
                "char_start":     int(row["char_start"]),
                "char_end":       int(row["char_end"]),
                "title":          str(row.get("title") or ""),
                "author":         str(row.get("author") or ""),
                "year":           "" if row.get("year") is None else str(row.get("year")),
                "language":       str(row.get("language") or ""),
                "genre":          str(row.get("genre") or ""),
            }
        )
        sentence_ids.append(sentence_id)

    if not ids:
        return 0

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    mark_sentences_chroma_indexed(catalog, sentence_ids)
    return len(sentence_ids)


def sync_book_sentences(catalog: str, book_id: int) -> int:
    if chromadb is None:
        return 0
    sentence_ids = [int(row["id"]) for row in get_book_sentences(book_id, catalog=catalog)]
    rows         = get_sentences_for_chroma(catalog, limit=max(len(sentence_ids), 1), sentence_ids=sentence_ids)
    return _upsert_rows(catalog, rows)


def sync_pending_sentences(catalog: str, batch_size: int = 250, max_batches: Optional[int] = None) -> int:
    if chromadb is None:
        return 0
    synced  = 0
    batches = 0
    while True:
        if max_batches is not None and batches >= max_batches:
            break
        rows = get_sentences_for_chroma(catalog, limit=max(1, int(batch_size)), only_unindexed=True)
        if not rows:
            break
        synced += _upsert_rows(catalog, rows)
        batches += 1
    return synced


def sync_all_catalogs_pending(batch_size: int = 250, max_batches_per_catalog: Optional[int] = None) -> dict[str, int]:
    if chromadb is None:
        return {catalog: 0 for catalog in list_writable_catalogs()}
    counts: dict[str, int] = {}
    for catalog in list_writable_catalogs():
        try:
            counts[catalog] = sync_pending_sentences(catalog, batch_size=batch_size, max_batches=max_batches_per_catalog)
        except Exception as exc:
            LOG.warning("Chroma catchup failed for catalog %s: %s", catalog, exc)
            counts[catalog] = 0
    return counts


def rebuild_catalog_store(catalog: str, batch_size: int = 250) -> dict[str, Any]:
    if chromadb is None:
        return {"catalog": catalog, "rebuilt": False, "reason": "chromadb unavailable", "indexed": 0}
    delete_catalog_store(catalog)
    reset_sentence_chroma_index(catalog)
    _get_collection(catalog)
    indexed = sync_pending_sentences(catalog, batch_size=max(1, int(batch_size)))
    return {"catalog": catalog, "rebuilt": True, "indexed": indexed}


def migrate_legacy_catalog_stores(batch_size: int = 250) -> dict[str, dict[str, Any]]:
    if chromadb is None:
        return {
            catalog: {"catalog": catalog, "rebuilt": False, "reason": "chromadb unavailable", "indexed": 0}
            for catalog in list_writable_catalogs()
        }
    results: dict[str, dict[str, Any]] = {}
    for catalog in list_writable_catalogs():
        path = _catalog_chroma_path(catalog)
        if path.exists() and not _catalog_store_is_current(catalog):
            try:
                results[catalog] = rebuild_catalog_store(catalog, batch_size=batch_size)
            except Exception as exc:
                results[catalog] = {"catalog": catalog, "rebuilt": False, "reason": str(exc), "indexed": 0}
        else:
            results[catalog] = {"catalog": catalog, "rebuilt": False, "reason": "already current", "indexed": 0}
    return results


def semantic_search(catalog: Optional[str], query: str, limit: int = 20, min_match: float = 0.0) -> list[dict]:
    if chromadb is None:
        LOG.info("Semantic search unavailable: chromadb is not installed.")
        return []
    text      = str(query or "").strip()
    if not text:
        return []
    catalogs        = [catalog] if catalog else list_writable_catalogs()
    per_cat_limit   = max(1, int(limit))
    min_match_score = max(0.0, min(1.0, float(min_match or 0.0)))
    results: list[dict] = []

    for current_catalog in catalogs:
        path = _catalog_chroma_path(current_catalog)
        if not path.exists():
            continue
        try:
            collection = _get_collection(current_catalog)
            if collection.count() <= 0:
                continue
            response = collection.query(query_texts=[text], n_results=per_cat_limit)
        except Exception as exc:
            LOG.warning("Semantic search failed for catalog %s: %s", current_catalog, exc)
            continue

        ids       = (response.get("ids") or [[]])[0]
        documents = (response.get("documents") or [[]])[0]
        metadatas = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]

        for idx, locator in enumerate(ids):
            metadata    = metadatas[idx] if idx < len(metadatas) and metadatas[idx] else {}
            document    = documents[idx] if idx < len(documents) else ""
            distance    = distances[idx] if idx < len(distances) else None
            match_score = _distance_to_match_score(distance)
            if match_score is not None and match_score < min_match_score:
                continue

            sentence_id  = metadata.get("sentence_id")
            sentence_row = None
            try:
                if sentence_id is not None:
                    sentence_row = get_sentence(str(current_catalog), int(sentence_id))
            except Exception:
                sentence_row = None

            book_id   = metadata.get("book_id")
            if sentence_row:
                book_id = sentence_row.get("book_id", book_id)
            title    = metadata.get("title") or (sentence_row.get("title") if sentence_row else "")
            author   = metadata.get("author") or (sentence_row.get("author") if sentence_row else "")
            year     = metadata.get("year") or (sentence_row.get("year") if sentence_row else "")
            language = metadata.get("language") or (sentence_row.get("language") if sentence_row else "")
            genre    = metadata.get("genre") or (sentence_row.get("genre") if sentence_row else "")
            snippet  = (sentence_row.get("sentence_text") if sentence_row else "") or document or ""

            results.append(
                {
                    "id":               int(book_id) if book_id is not None else None,
                    "route_id":         f"{current_catalog}:{int(book_id)}" if book_id is not None else "",
                    "sentence_id":      int(sentence_id) if sentence_id is not None else None,
                    "sentence_locator": str(locator or ""),
                    "catalog":          str(current_catalog),
                    "title":            str(title or ""),
                    "author":           str(author or ""),
                    "year":             int(year) if str(year or "").isdigit() else year,
                    "language":         str(language or ""),
                    "genre":            str(genre or ""),
                    "snippet":          str(snippet or ""),
                    "match_score":      float(match_score) if match_score is not None else None,
                }
            )

    results.sort(
        key = lambda row: (
            row["match_score"] is None,
            -(row["match_score"] or 0.0),
            (row.get("title") or "").lower(),
        )
    )
    return results[:limit]


def delete_sentence_ids(catalog: str, sentence_ids: list[int]) -> int:
    if chromadb is None or not sentence_ids:
        return 0
    collection = _get_collection(catalog)
    locators   = [f"library/{str(catalog).strip().lower()}/{int(sentence_id)}" for sentence_id in sentence_ids]
    collection.delete(ids=locators)
    return len(locators)


def delete_catalog_store(catalog: str) -> bool:
    if chromadb is None:
        return False
    path = _catalog_chroma_path(catalog)
    _release_catalog_client(catalog)
    if not path.exists():
        return False
    shutil.rmtree(path)
    return True
