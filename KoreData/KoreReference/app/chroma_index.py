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
    get_article_sentences,
    get_sentence,
    get_sentences_for_chroma,
    mark_sentences_chroma_indexed,
    reset_sentence_chroma_index,
)


LOG                       = logging.getLogger("korereference.chroma")
_CHROMA_ROOT              = Path(cfg["data_dir"]) / "_chroma"
_COLLECTION_NAME          = "sentences"
_COLLECTION_CONFIGURATION = {"hnsw": {"space": "cosine"}}
_STORE_SCHEMA_VERSION     = "cosine-v1"
_STORE_SCHEMA_FILE        = ".schema"
_CLIENT_LOCK              = threading.Lock()
_CLIENT: Any | None       = None


def chroma_available() -> bool:
    return chromadb is not None


def _distance_to_match_score(distance: Optional[float]) -> Optional[float]:
    if distance is None:
        return None
    return max(0.0, min(1.0, 1.0 - float(distance)))


def _store_path() -> Path:
    return _CHROMA_ROOT / "main"


def _schema_marker_path() -> Path:
    return _store_path() / _STORE_SCHEMA_FILE


def _store_is_current() -> bool:
    marker_path = _schema_marker_path()
    try:
        return marker_path.exists() and marker_path.read_text(encoding="utf-8").strip() == _STORE_SCHEMA_VERSION
    except Exception:
        return False


def _mark_store_current() -> None:
    marker_path = _schema_marker_path()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(_STORE_SCHEMA_VERSION, encoding="utf-8")


def _release_client() -> None:
    global _CLIENT
    with _CLIENT_LOCK:
        client  = _CLIENT
        _CLIENT = None
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


def _get_collection():
    global _CLIENT
    if chromadb is None:
        raise RuntimeError("chromadb is not installed")
    with _CLIENT_LOCK:
        if _CLIENT is None:
            path = _store_path()
            path.mkdir(parents=True, exist_ok=True)
            _CLIENT = chromadb.PersistentClient(path=str(path))
        collection = _CLIENT.get_or_create_collection(
            name          = _COLLECTION_NAME,
            configuration = _COLLECTION_CONFIGURATION,
        )
        if not _store_is_current():
            _mark_store_current()
        return collection


def _upsert_rows(rows: list[dict]) -> int:
    if chromadb is None or not rows:
        return 0
    collection   = _get_collection()
    ids: list[str]         = []
    documents: list[str]   = []
    metadatas: list[dict]  = []
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
                "service":        "reference",
                "database":       "main",
                "article_id":     int(row["article_id"]),
                "sentence_id":    sentence_id,
                "sentence_index": int(row["sentence_index"]),
                "source_field":   str(row["source_field"]),
                "char_start":     int(row["char_start"]),
                "char_end":       int(row["char_end"]),
                "title":          str(row.get("title") or ""),
                "word_count":     "" if row.get("word_count") is None else str(row.get("word_count")),
            }
        )
        sentence_ids.append(sentence_id)

    if not ids:
        return 0

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    mark_sentences_chroma_indexed(sentence_ids)
    return len(sentence_ids)


def sync_article_sentences(article_id: int) -> int:
    if chromadb is None:
        return 0
    sentence_ids = [int(row["id"]) for row in get_article_sentences(int(article_id))]
    rows         = get_sentences_for_chroma(limit=max(len(sentence_ids), 1), sentence_ids=sentence_ids)
    return _upsert_rows(rows)


def sync_pending_sentences(batch_size: int = 250, max_batches: Optional[int] = None) -> int:
    if chromadb is None:
        return 0
    synced  = 0
    batches = 0
    while True:
        if max_batches is not None and batches >= max_batches:
            break
        rows = get_sentences_for_chroma(limit=max(1, int(batch_size)), only_unindexed=True)
        if not rows:
            break
        synced += _upsert_rows(rows)
        batches += 1
    return synced


def rebuild_store(batch_size: int = 250) -> dict[str, Any]:
    if chromadb is None:
        return {"rebuilt": False, "reason": "chromadb unavailable", "indexed": 0}
    delete_store()
    reset_sentence_chroma_index()
    _get_collection()
    indexed = sync_pending_sentences(batch_size=max(1, int(batch_size)))
    return {"rebuilt": True, "indexed": indexed}


def semantic_search(query: str, limit: int = 20, min_match: float = 0.0) -> list[dict]:
    if chromadb is None:
        LOG.info("Semantic search unavailable: chromadb is not installed.")
        return []

    text = str(query or "").strip()
    if not text:
        return []

    path = _store_path()
    if not path.exists():
        try:
            sync_pending_sentences(batch_size=250)
        except Exception as exc:
            LOG.warning("Reference semantic catchup failed before first query: %s", exc)
        if not path.exists():
            return []

    try:
        collection = _get_collection()
        if collection.count() <= 0:
            sync_pending_sentences(batch_size=250)
            collection = _get_collection()
        if collection.count() <= 0:
            return []
        response = collection.query(query_texts=[text], n_results=max(1, int(limit)))
    except Exception as exc:
        LOG.warning("Semantic search failed for reference store: %s", exc)
        return []

    ids             = (response.get("ids") or [[]])[0]
    documents       = (response.get("documents") or [[]])[0]
    metadatas       = (response.get("metadatas") or [[]])[0]
    distances       = (response.get("distances") or [[]])[0]
    min_match_score = max(0.0, min(1.0, float(min_match or 0.0)))
    results: list[dict] = []

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
                sentence_row = get_sentence(int(sentence_id))
        except Exception:
            sentence_row = None

        article_id = metadata.get("article_id")
        if sentence_row:
            article_id = sentence_row.get("article_id", article_id)
        title   = metadata.get("title") or (sentence_row.get("title") if sentence_row else "")
        snippet = (sentence_row.get("sentence_text") if sentence_row else "") or document or ""
        results.append(
            {
                "id":               int(article_id) if article_id is not None else None,
                "sentence_id":      int(sentence_id) if sentence_id is not None else None,
                "sentence_locator": str(locator or ""),
                "title":            str(title or ""),
                "snippet":          str(snippet or ""),
                "word_count":       int(metadata["word_count"]) if str(metadata.get("word_count") or "").isdigit() else metadata.get("word_count"),
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


def delete_sentence_ids(sentence_ids: list[int]) -> int:
    if chromadb is None or not sentence_ids:
        return 0
    collection = _get_collection()
    locators   = [f"reference/main/{int(sentence_id)}" for sentence_id in sentence_ids]
    collection.delete(ids=locators)
    return len(locators)


def delete_store() -> bool:
    if chromadb is None:
        return False
    path = _store_path()
    _release_client()
    if not path.exists():
        return False
    shutil.rmtree(path)
    return True
