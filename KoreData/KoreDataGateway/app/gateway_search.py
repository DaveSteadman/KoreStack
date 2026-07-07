from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, unquote

from app.gateway_graph import search_graph
from app.gateway_library import search_library
from app.gateway_scrape import search_scrape


SEMANTIC_SEARCH_DOMAINS = {"feeds", "library", "reference"}


def build_artifact_ref(kind: str, **parts: Any) -> str:
    ref_parts = [kind]
    for key, value in parts.items():
        encoded = quote("" if value is None else str(value), safe="")
        ref_parts.append(f"{key}={encoded}")
    return "|".join(ref_parts)


def parse_artifact_ref(refid: str) -> tuple[str, dict[str, str]]:
    text = str(refid or "").strip()
    if not text:
        raise ValueError("Artifact ref is empty.")
    segments = text.split("|")
    kind     = segments[0].strip()
    if not kind:
        raise ValueError("Artifact ref is missing its kind.")
    values: dict[str, str] = {}
    for segment in segments[1:]:
        if "=" not in segment:
            raise ValueError(f"Malformed artifact ref component: {segment!r}")
        key, encoded = segment.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("Artifact ref contains an empty key.")
        values[key] = unquote(encoded)
    return kind, values


def parse_sentence_locator(locator: str) -> tuple[str, str, int]:
    text  = str(locator or "").strip().strip("/")
    parts = [part.strip() for part in text.split("/") if part.strip()]
    if len(parts) != 3:
        raise ValueError("Sentence locator must look like <service>/<database>/<sentence_id>.")
    service, database, raw_id = parts
    try:
        sentence_id = int(raw_id)
    except ValueError as exc:
        raise ValueError(f"Sentence locator has non-numeric sentence_id: {raw_id!r}") from exc
    return service.lower(), database, sentence_id


def _map_feed_entry(entry: dict, cfg: dict[str, Any]) -> dict:
    domain = entry.get("domain", "")
    entry_id = entry.get("id", "")
    body = entry.get("page_text") or entry.get("content") or entry.get("body") or entry.get("summary") or ""
    return {
        "domain":       "feeds",
        "type":         "feed_entry",
        "artifact_ref": build_artifact_ref("feed_entry", domain=domain, id=entry_id),
        "id":           entry_id,
        "title":        entry.get("headline") or entry.get("title", ""),
        "source":       entry.get("feed_name") or entry.get("source_name") or domain,
        "published_at": entry.get("published") or entry.get("published_at") or entry.get("ingested_at"),
        "snippet":      body[:300].strip(),
        "url":          f"{cfg['korefeed_url']}/ui/feeds/{domain}/{entry_id}",
        "score":        entry.get("score"),
    }


def _map_reference_article(article: dict, cfg: dict[str, Any]) -> dict:
    title = article.get("title", "")
    return {
        "domain":       "reference",
        "type":         "reference_article",
        "artifact_ref": build_artifact_ref("reference_article", title=title),
        "title":        title,
        "summary":      article.get("summary", ""),
        "snippet":      article.get("snippet") or (article.get("summary") or "")[:300],
        "word_count":   article.get("word_count"),
        "url":          f"{cfg['korereference_url']}/ui/reference/{quote(title, safe='')}",
        "score":        article.get("score"),
    }


def _map_rag_chunk(chunk: dict, cfg: dict[str, Any]) -> dict:
    db_id = chunk.get("db", "default")
    return {
        "domain":       "rag",
        "type":         "rag_chunk",
        "artifact_ref": build_artifact_ref("rag_chunk", id=chunk.get("id")),
        "id":           chunk.get("id"),
        "title":        chunk.get("title", ""),
        "source":       chunk.get("source", ""),
        "tags":         chunk.get("tags", ""),
        "snippet":      chunk.get("snippet") or "",
        "url":          f"{cfg['korerag_url']}/ui/rag/{chunk.get('id', '')}?db={db_id}",
        "score":        chunk.get("score"),
    }


def _search_query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for term in re.findall(r'"([^"]+)"|([A-Za-z0-9_:+.-]+)', str(query or "")):
        value = (term[0] or term[1] or "").strip().lower()
        if not value or value in {"and", "or", "not"}:
            continue
        terms.append(value)
    return terms


def _parse_search_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _result_match_score(item: dict, query: str, query_terms: list[str]) -> float:
    text_fields = [
        str(item.get("title", "")),
        str(item.get("snippet", "")),
        str(item.get("summary", "")),
        str(item.get("source", "")),
        str(item.get("start", "")),
        str(item.get("connection", "")),
        str(item.get("end", "")),
    ]
    haystack = "\n".join(text_fields).lower()
    title_l  = str(item.get("title", "")).lower()
    query_l  = str(query or "").strip().lower()
    score    = 0.0

    raw_score = item.get("score")
    if isinstance(raw_score, (int, float)):
        score += -float(raw_score)

    if query_l and query_l in title_l:
        score += 18.0
    elif query_l and query_l in haystack:
        score += 9.0

    for term in query_terms:
        if term in title_l:
            score += 6.0
        elif term in haystack:
            score += 2.0

    timestamp = _parse_search_timestamp(item.get("published_at")) or _parse_search_timestamp(item.get("captured_at"))
    if timestamp is not None:
        age_days = max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds() / 86400.0)
        score += max(0.0, 5.0 - min(age_days, 30.0) / 6.0)

    domain = str(item.get("domain", "")).lower()
    if domain == "reference":
        score += 1.0
    if domain == "feeds":
        score += 0.5
    return score


def _merge_search_results(results_by_domain: dict[str, list[dict]], query: str, limit: int) -> list[dict]:
    query_terms = _search_query_terms(query)
    merged: list[tuple[float, float, int, dict]] = []
    ordinal = 0

    for domain in ("feeds", "reference", "library", "rag", "scrape", "graph"):
        items = results_by_domain.get(domain) or []
        for item in items:
            if not isinstance(item, dict):
                continue
            annotated = dict(item)
            annotated.setdefault("domain", domain)
            timestamp = _parse_search_timestamp(annotated.get("published_at")) or _parse_search_timestamp(annotated.get("captured_at"))
            recency = timestamp.timestamp() if timestamp is not None else 0.0
            merged.append((_result_match_score(annotated, query, query_terms), recency, ordinal, annotated))
            ordinal += 1

    merged.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return [item for _score, _recency, _ordinal, item in merged[:limit]]


async def run_search(
    req,
    *,
    cfg: dict[str, Any],
    feed_client,
    lib_client,
    ref_client,
    rag_client,
    scrape_client,
    graph_client,
) -> dict:
    requested_domains = [domain.lower() for domain in req.domains] if req.domains else ["feeds", "reference", "library", "rag", "scrape"]
    search_mode       = "semantic" if str(req.mode or "").strip().lower() == "semantic" else "keyword"
    min_match         = max(0.0, min(1.0, float(req.min_match or 0.0)))
    limit             = req.limit

    if search_mode == "semantic":
        unsupported_domains = [domain for domain in requested_domains if domain not in SEMANTIC_SEARCH_DOMAINS]
        search_domains      = [domain for domain in requested_domains if domain in SEMANTIC_SEARCH_DOMAINS]
        if not search_domains:
            search_domains = sorted(SEMANTIC_SEARCH_DOMAINS)
    else:
        unsupported_domains = []
        search_domains      = requested_domains

    async def _feeds():
        if search_mode == "semantic":
            params: dict[str, Any] = {"q": req.query, "limit": limit, "min_match": min_match}
            response = await feed_client.get("/api/semantic-search", params=params, timeout=10.0)
        else:
            params = {"q": req.query, "limit": limit, "full": "true"}
            if req.since:
                params["since"] = req.since
            if req.until:
                params["until"] = req.until
            response = await feed_client.get("/api/search", params=params, timeout=10.0)
        if search_mode == "semantic" and response.status_code == 503:
            detail = ""
            try:
                detail = str((response.json() or {}).get("detail") or "")
            except Exception:
                detail = ""
            warning = detail or "Semantic search unavailable."
            return {"status": "partial", "results": [], "error": "", "warnings": [f"Feed semantic search unavailable: {warning}"]}
        if response.status_code != 200:
            return {"status": "error", "results": [], "error": f"HTTP {response.status_code}", "warnings": []}
        payload = response.json() or []
        if not isinstance(payload, list):
            return {"status": "error", "results": [], "error": "Feed search returned a non-list payload.", "warnings": []}
        failed_domains = [part.strip() for part in str(response.headers.get("X-Kore-Failed-Domains", "")).split(",") if part.strip()]
        warnings: list[str] = []
        if failed_domains:
            warnings.append(f"Feed search skipped failing domains: {', '.join(failed_domains)}")
        if search_mode == "semantic":
            results = [
                {
                    "domain":           "feeds",
                    "feed_domain":      entry.get("domain") or "",
                    "type":             "feed_entry",
                    "artifact_ref":     build_artifact_ref("feed_entry", domain=entry.get("domain"), id=entry.get("id")),
                    "id":               entry.get("id"),
                    "title":            entry.get("headline") or "",
                    "headline":         entry.get("headline") or "",
                    "source":           entry.get("feed_name") or "",
                    "published_at":     entry.get("published") or "",
                    "snippet":          entry.get("snippet") or "",
                    "url":              entry.get("url") or "",
                    "sentence_id":      entry.get("sentence_id"),
                    "sentence_locator": entry.get("sentence_locator") or "",
                    "match_score":      entry.get("match_score"),
                }
                for entry in payload[:limit]
            ]
            return {"status": "partial" if failed_domains else "ok", "results": results, "error": "", "warnings": warnings}
        return {"status": "partial" if failed_domains else "ok", "results": [_map_feed_entry(entry, cfg) for entry in payload[:limit]], "error": "", "warnings": warnings}

    async def _reference():
        params: dict[str, Any] = {"q": req.query, "limit": limit}
        if search_mode == "semantic":
            params["min_match"] = min_match
            response = await ref_client.get("/api/semantic-search", params=params, timeout=10.0)
        else:
            response = await ref_client.get("/api/search", params=params, timeout=10.0)
        if search_mode == "semantic" and response.status_code == 503:
            detail = ""
            try:
                detail = str((response.json() or {}).get("detail") or "")
            except Exception:
                detail = ""
            warning = detail or "Semantic search unavailable."
            return {"status": "partial", "results": [], "error": "", "warnings": [f"Reference semantic search unavailable: {warning}"]}
        if response.status_code != 200:
            return {"status": "error", "results": [], "error": f"HTTP {response.status_code}", "warnings": []}
        payload = response.json() or []
        if not isinstance(payload, list):
            return {"status": "error", "results": [], "error": "Reference search returned a non-list payload.", "warnings": []}
        if search_mode == "semantic":
            results = [
                {
                    "domain":           "reference",
                    "type":             "reference_article",
                    "artifact_ref":     build_artifact_ref("reference_article", title=article.get("title") or ""),
                    "id":               article.get("id"),
                    "title":            article.get("title", ""),
                    "snippet":          article.get("snippet") or "",
                    "word_count":       article.get("word_count"),
                    "url":              f"{cfg['korereference_url']}/ui/reference/{quote(article.get('title') or '', safe='')}",
                    "sentence_id":      article.get("sentence_id"),
                    "sentence_locator": article.get("sentence_locator") or "",
                    "match_score":      article.get("match_score"),
                }
                for article in payload[:limit]
            ]
            return {"status": "ok", "results": results, "error": "", "warnings": []}
        return {"status": "ok", "results": [_map_reference_article(article, cfg) for article in payload[:limit]], "error": "", "warnings": []}

    async def _library():
        return await search_library(
            lib_client,
            query              = req.query,
            limit              = limit,
            search_mode        = search_mode,
            min_match          = min_match,
            cfg                = cfg,
            build_artifact_ref = build_artifact_ref,
        )

    async def _rag():
        params: dict[str, Any] = {"q": req.query, "limit": limit}
        response = await rag_client.get("/api/search/all", params=params, timeout=10.0)
        if response.status_code != 200:
            return {"status": "error", "results": [], "error": f"HTTP {response.status_code}", "warnings": []}
        payload = response.json() or []
        if not isinstance(payload, list):
            return {"status": "error", "results": [], "error": "RAG search returned a non-list payload.", "warnings": []}
        return {"status": "ok", "results": [_map_rag_chunk(chunk, cfg) for chunk in payload[:limit]], "error": "", "warnings": []}

    async def _scrape():
        return await search_scrape(
            scrape_client,
            query              = req.query,
            limit              = limit,
            cfg                = cfg,
            build_artifact_ref = build_artifact_ref,
        )

    async def _graph():
        return await search_graph(
            graph_client,
            query = req.query,
            limit = limit,
        )

    tasks: list[tuple[str, Any]] = []
    if "feeds" in search_domains:
        tasks.append(("feeds", _feeds()))
    if "reference" in search_domains:
        tasks.append(("reference", _reference()))
    if "library" in search_domains:
        tasks.append(("library", _library()))
    if "rag" in search_domains:
        tasks.append(("rag", _rag()))
    if "scrape" in search_domains:
        tasks.append(("scrape", _scrape()))
    if "graph" in search_domains and search_mode != "semantic":
        tasks.append(("graph", _graph()))

    gathered          = await asyncio.gather(*(coro for _, coro in tasks), return_exceptions=True)
    results_by_domain: dict[str, list[dict]] = {}
    domain_statuses:  dict[str, dict[str, Any]] = {}
    warnings:         list[str] = []

    for (key, _task), value in zip(tasks, gathered):
        if isinstance(value, Exception):
            results_by_domain[key] = []
            domain_statuses[key]   = {"status": "error", "count": 0, "error": str(value), "warnings": []}
            warnings.append(f"{key} search failed: {value}")
            continue

        payload          = value if isinstance(value, dict) else {}
        results          = payload.get("results") if isinstance(payload.get("results"), list) else []
        status           = str(payload.get("status") or "ok")
        error            = str(payload.get("error") or "")
        domain_warnings  = [str(item) for item in (payload.get("warnings") or []) if str(item).strip()]

        results_by_domain[key] = results
        domain_statuses[key]   = {
            "status":   status,
            "count":    len(results),
            "error":    error,
            "warnings": domain_warnings,
        }
        warnings.extend(domain_warnings)
        if error:
            warnings.append(f"{key} search failed: {error}")

    non_ok_statuses = [item.get("status") for item in domain_statuses.values() if item.get("status") != "ok"]
    total_results   = sum(len(items) for items in results_by_domain.values())
    if unsupported_domains:
        warnings.append(
            f"Semantic search is currently available only for: {', '.join(sorted(SEMANTIC_SEARCH_DOMAINS))}. "
            f"Ignored: {', '.join(unsupported_domains)}"
        )
    if any(status == "error" for status in non_ok_statuses) and total_results == 0:
        overall_status = "error"
    elif non_ok_statuses:
        overall_status = "partial"
    else:
        overall_status = "ok"

    return {
        "query":                    req.query,
        "mode":                     search_mode,
        "min_match":                min_match if search_mode == "semantic" else None,
        "semantic_capable_domains": sorted(SEMANTIC_SEARCH_DOMAINS),
        "domains_searched":         [key for key, _ in tasks],
        "status":                   overall_status,
        "partial_failure":          overall_status != "ok",
        "result_counts_by_domain":  {key: len(value) for key, value in results_by_domain.items()},
        "domain_statuses":          domain_statuses,
        "warnings":                 warnings,
        "results":                  _merge_search_results(results_by_domain, req.query, limit),
        "results_by_domain":        results_by_domain,
    }
