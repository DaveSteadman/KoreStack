#!/usr/bin/env python3
"""
import_graph_refs.py
====================
Phase 1 bulk-import: KoreReference → KoreGraph

For each non-redirect article in KoreReference:

  Links (default):   GET /articles/{title}/links
                     → (title, "mentions", to_title) connections

  Facts (--with-facts or --facts-only):  GET /articles/{title}
                     → facts field is [{"value": [key, val], "Count": N}]
                     → wikilink targets in values become graph endpoints
                     → (title, fact_key, fact_endpoint)

Usage:
    python import_graph_refs.py                    # links only
    python import_graph_refs.py --limit 200        # first 200 articles
    python import_graph_refs.py --dry-run          # count only, no writes
    python import_graph_refs.py --with-facts       # links + infobox facts
    python import_graph_refs.py --facts-only       # infobox facts only

Endpoints used:
    KoreReference: GET {REF_URL}/articles?offset=N&limit=100
                   GET {REF_URL}/articles/{title}/links
                   GET {REF_URL}/articles/{title}  (with --facts*)
    KoreGraph:     POST {GRAPH_URL}/api/connections/by-name/batch
"""
import argparse
import os
import re
import sys
import time
from urllib.parse import quote

import requests
import json as _json
from pathlib import Path as _Path


def _find_suite_root() -> _Path:
    _here = _Path(__file__).resolve()
    _candidates: list[_Path] = []

    _env_root = os.environ.get("KORESTACK_ROOT") or os.environ.get("KORESTACK_CONFIG_ROOT")
    if _env_root:
        _candidates.append(_Path(_env_root))

    _candidates.extend(_here.parents)

    try:
        _cwd = _Path.cwd().resolve()
        _candidates.append(_cwd)
        _candidates.extend(_cwd.parents)
    except Exception:
        pass

    for _p in _here.parents:
        if _p.parent != _p:
            _candidates.append(_p.parent / "KoreStack")
        if _p.name.endswith("-FullData"):
            _candidates.append(_p.with_name(_p.name.replace("-FullData", "")))

    _seen: set[str] = set()
    for _cand in _candidates:
        _key = str(_cand).lower()
        if _key in _seen:
            continue
        _seen.add(_key)
        _cfg = _cand / "config"
        if (_cfg / "korestack_config.json").exists():
            return _cand

    return _here.parents[2]


def _load_suite_config() -> dict:
    _suite_root = _find_suite_root()
    try:
        return _json.loads((_suite_root / "config" / "korestack_config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── Configuration ────────────────────────────────────────────────────────────
_suite_cfg = _load_suite_config()
_svc_host  = _suite_cfg.get("network", {}).get("host", "127.0.0.1")
_services  = _suite_cfg.get("services", {})

_ref_cfg   = _services.get("korereference", {})
_graph_cfg = _services.get("koregraph", {})

# Legacy fallback keeps older config layouts working.
_legacy_data_port = _services.get("data", {}).get("port", 8620)

_ref_host   = _ref_cfg.get("host", _svc_host)
_ref_port   = _ref_cfg.get("port", _legacy_data_port + 4)
_graph_host = _graph_cfg.get("host", _svc_host)
_graph_port = _graph_cfg.get("port", 8626)

REF_URL    = f"http://{_ref_host}:{_ref_port}"
GRAPH_URL  = f"http://{_graph_host}:{_graph_port}"
BATCH_SIZE = 200   # connections per POST to KoreGraph
PAGE_SIZE  = 100   # articles per GET from KoreReference

WIKILINK_RE = re.compile(r'\[\[([^\[\]|#]+?)(?:\|[^\[\]]*?)?\]\]')


def _wikilinks_in(text: str) -> list[str]:
    """Return unique wikilink targets found in a text string."""
    seen, out = set(), []
    for m in WIKILINK_RE.finditer(text or ""):
        t = m.group(1).strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _strip_wikilinks(text: str) -> str:
    """Replace [[A|B]] → B, [[A]] → A in a string."""
    def _rep(m: re.Match) -> str:
        inner = m.group(0)[2:-2]   # strip [[ and ]]
        return inner.split("|")[-1].strip()
    return WIKILINK_RE.sub(_rep, text)


def _fact_key(raw: str) -> str:
    """Normalise an infobox key to a short predicate string."""
    s = _strip_wikilinks(raw).strip().lower()
    s = re.sub(r'[^a-z0-9]+', '_', s).strip('_')
    return s or "fact"


def links_from_article(session: requests.Session, title: str) -> list[dict]:
    """Fetch pre-extracted outbound links for one article."""
    try:
        r = session.get(
            f"{REF_URL}/articles/{quote(title, safe='')}/links",
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json()
    except Exception as exc:
        print(f"  [WARN] links fetch failed for '{title}': {exc}", flush=True)
        return []
    return [
        {"start": title, "connection": "mentions", "end": item["to_title"], "state": 0, "score": 1}
        for item in raw
        if item.get("to_title") and item["to_title"] != title
    ]


def facts_from_article(session: requests.Session, title: str) -> list[dict]:
    """Fetch infobox facts for one article and return graph connections."""
    try:
        r = session.get(
            f"{REF_URL}/articles/{quote(title, safe='')}",
            timeout=15,
        )
        r.raise_for_status()
        article = r.json()
    except Exception as exc:
        print(f"  [WARN] article fetch failed for '{title}': {exc}", flush=True)
        return []

    facts_raw = article.get("facts") or []
    if not isinstance(facts_raw, list):
        return []

    conns: list[dict] = []
    seen: set[tuple] = set()

    def _add(predicate: str, end: str) -> None:
        end = end.strip()
        if not end or end == title:
            return
        k = (predicate, end)
        if k in seen:
            return
        seen.add(k)
        conns.append({"start": title, "connection": predicate, "end": end, "state": 0, "score": 1})

    # Deduplicate facts by value (each entry can appear multiple times — Count > 1
    # when the same key-value pair occurred in multiple infobox templates)
    seen_pairs: set[tuple] = set()
    for entry in facts_raw:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        key_raw, val_raw = str(entry[0]), str(entry[1])
        pair_key = (key_raw, val_raw)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        predicate = _fact_key(key_raw)
        if not predicate:
            continue

        # Prefer wikilink targets inside the value (they are clean canonical names)
        wl_targets = _wikilinks_in(val_raw)
        if wl_targets:
            for t in wl_targets:
                _add(predicate, t)
        else:
            # Plain string value — use the first meaningful token (avoid long dates etc.)
            plain = val_raw.strip()
            if plain and len(plain) <= 120:
                _add(predicate, plain)

    return conns


def submit_batch(session: requests.Session, batch: list[dict], dry_run: bool) -> tuple[int, int]:
    """POST a batch to KoreGraph. Returns (accepted, errors)."""
    if dry_run or not batch:
        return len(batch), 0
    try:
        r = session.post(
            f"{GRAPH_URL}/api/connections/by-name/batch",
            json=batch,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("accepted", 0), len(data.get("errors", []))
    except Exception as exc:
        print(f"  [WARN] Batch submit failed: {exc}", flush=True)
        return 0, len(batch)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import KoreReference wikilinks into KoreGraph")
    parser.add_argument("--limit",      type=int,  default=0,     help="Max articles (0=all)")
    parser.add_argument("--dry-run",    action="store_true",       help="Count only, no writes")
    parser.add_argument("--with-facts", action="store_true",       help="Include infobox facts (extra request per article)")
    parser.add_argument("--facts-only", action="store_true",       help="Infobox facts only, skip wikilinks")
    args = parser.parse_args()

    do_links = not args.facts_only
    do_facts = args.with_facts or args.facts_only

    print("KoreReference → KoreGraph import", flush=True)
    print(f"  dry_run={args.dry_run}  links={do_links}  facts={do_facts}  limit={args.limit or 'all'}", flush=True)
    print(flush=True)

    session = requests.Session()

    # ── Verify services ────────────────────────────────────────────────
    for label, url in [("KoreReference", REF_URL), ("KoreGraph", GRAPH_URL)]:
        try:
            r = session.get(f"{url}/status", timeout=5)
            r.raise_for_status()
            print(f"  OK {label} reachable at {url}", flush=True)
        except Exception as exc:
            print(f"  FAIL {label} not reachable at {url}: {exc}", flush=True)
            sys.exit(1)
    print(flush=True)

    total_articles = 0
    total_skipped  = 0
    total_conns    = 0
    total_accepted = 0
    total_errors   = 0
    offset         = 0
    batch: list[dict] = []
    t_start = time.time()

    while True:
        # ── Fetch article list page ───────────────────────────────────
        try:
            r = session.get(
                f"{REF_URL}/articles",
                params={"offset": offset, "limit": PAGE_SIZE},
                timeout=30,
            )
            r.raise_for_status()
            articles = r.json()
        except Exception as exc:
            print(f"[ERROR] articles list failed at offset {offset}: {exc}", flush=True)
            break

        if not articles:
            break

        for article in articles:
            title = (article.get("title") or "").strip()
            if not title:
                continue
            if article.get("redirect_to"):   # skip redirect stubs
                total_skipped += 1
                continue

            conns: list[dict] = []
            if do_links:
                conns.extend(links_from_article(session, title))
            if do_facts:
                conns.extend(facts_from_article(session, title))

            batch.extend(conns)
            total_articles += 1
            total_conns    += len(conns)

            if len(batch) >= BATCH_SIZE:
                accepted, errors = submit_batch(session, batch, args.dry_run)
                total_accepted += accepted
                total_errors   += errors
                batch = []
                elapsed = time.time() - t_start
                rate = total_articles / elapsed if elapsed > 0 else 0
                print(
                    f"  articles={total_articles:,}  conns={total_conns:,}"
                    f"  accepted={total_accepted:,}  errors={total_errors}"
                    f"  ({rate:.1f} art/s)",
                    flush=True,
                )

            if args.limit and total_articles >= args.limit:
                break

        offset += PAGE_SIZE
        if args.limit and total_articles >= args.limit:
            break
        if len(articles) < PAGE_SIZE:
            break

    # ── Flush remainder ───────────────────────────────────────────────
    if batch:
        accepted, errors = submit_batch(session, batch, args.dry_run)
        total_accepted += accepted
        total_errors   += errors

    elapsed = time.time() - t_start
    print(flush=True)
    print(f"Done in {elapsed:.1f}s", flush=True)
    print(f"  Articles processed : {total_articles:,}", flush=True)
    print(f"  Redirects skipped  : {total_skipped:,}", flush=True)
    print(f"  Connections found  : {total_conns:,}", flush=True)
    print(f"  Accepted           : {total_accepted:,}", flush=True)
    print(f"  Errors             : {total_errors}", flush=True)
    if args.dry_run:
        print("(dry-run: nothing written)", flush=True)


if __name__ == "__main__":
    main()
