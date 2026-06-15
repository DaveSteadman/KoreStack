#!/usr/bin/env python3
"""
import_graph_companies.py
=========================
Imports company-to-product/service/brand connections from KoreReference into KoreGraph.

Strategy:
  1. Page through all articles (metadata only — fast)
  2. Pre-filter to company candidates using title/summary keywords (~2% of articles)
  3. Fetch full article for each candidate to read infobox facts
  4. Confirm it is a company via facts (company_type, ticker_symbol, or product facts)
  5. Extract wikilink targets from product/service/brand/subsidiary fact values
  6. Submit typed triples to KoreGraph at score=2 (higher quality than raw mentions)

Connection predicates generated (from infobox fact keys):
  products / product              → "makes"
  services / service              → "provides"
  brands / brand                  → "owns_brand"
  subsidiaries / subsidiary       → "owns"
  divisions / division            → "has_division"

With --all-facts, also adds:
  founders / founder              → "founded_by"
  industry                        → "in_industry"
  headquarters                    → "headquartered_in"
  key_people / key_person         → "has_key_person"
  parent / parent_company         → "owned_by"

Usage:
  python import_graph_companies.py --dry-run
  python import_graph_companies.py --dry-run --all-facts
  python import_graph_companies.py --limit 100 --dry-run
  python import_graph_companies.py                        # live run, products only
  python import_graph_companies.py --all-facts            # live run, full relation set

Endpoints:
    KoreReference: GET {REF_URL}/articles?offset=N&limit=200   (metadata)
                                 GET {REF_URL}/articles/{title}              (facts)
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
        if (_cfg / "default.json").exists() or (_cfg / "local.json").exists():
            return _cand

    return _here.parents[2]


def _load_suite_config() -> dict:
    _suite_root = _find_suite_root()
    _result: dict = {}
    for _name in ("default.json", "local.json"):
        try:
            _raw = _json.loads((_suite_root / "config" / _name).read_text(encoding="utf-8"))
        except Exception:
            continue
        for _k, _v in _raw.items():
            if isinstance(_v, dict) and isinstance(_result.get(_k), dict):
                _result[_k] = {**_result[_k], **_v}
            else:
                _result[_k] = _v
    return _result


# ── Configuration ────────────────────────────────────────────────────────────
_suite_cfg = _load_suite_config()
_svc_host  = _suite_cfg.get("network", {}).get("host", "127.0.0.1")
_services  = _suite_cfg.get("services", {})

_ref_cfg   = _services.get("korereference", {})
_graph_cfg = _services.get("koregraph", {})

# Legacy fallback keeps older config layouts working.
_legacy_data_port = _services.get("data", {}).get("port", 8620)

_ref_host  = _ref_cfg.get("host", _svc_host)
_ref_port  = _ref_cfg.get("port", _legacy_data_port + 4)
_graph_host = _graph_cfg.get("host", _svc_host)
_graph_port = _graph_cfg.get("port", 8626)

REF_URL    = f"http://{_ref_host}:{_ref_port}"
GRAPH_URL  = f"http://{_graph_host}:{_graph_port}"
BATCH_SIZE = 200
PAGE_SIZE  = 200

# ── Wikilink helpers ─────────────────────────────────────────────────────────
WIKILINK_RE = re.compile(r'\[\[([^\[\]|#]+?)(?:\|[^\[\]]*?)?\]\]')

def _wikilinks_in(text: str) -> list[str]:
    seen, out = set(), []
    for m in WIKILINK_RE.finditer(text or ""):
        t = m.group(1).strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out

def _strip_wikilinks(text: str) -> str:
    return WIKILINK_RE.sub(lambda m: m.group(0)[2:-2].split("|")[-1].strip(), text)

def _norm_key(raw: str) -> str:
    """Normalise a raw infobox key to a plain predicate string."""
    s = _strip_wikilinks(raw).strip().lower()
    return re.sub(r'[^a-z0-9]+', '_', s).strip('_')


# ── Predicate maps ───────────────────────────────────────────────────────────
PRODUCT_PREDICATES: dict[str, str] = {
    "products":      "makes",
    "product":       "makes",
    "services":      "provides",
    "service":       "provides",
    "brands":        "owns_brand",
    "brand":         "owns_brand",
    "subsidiaries":  "owns",
    "subsidiary":    "owns",
    "divisions":     "has_division",
    "division":      "has_division",
    # handle parenthetical variants like "division_business"
    "division_business": "has_division",
}

EXTRA_PREDICATES: dict[str, str] = {
    "founders":        "founded_by",
    "founder":         "founded_by",
    "industry":        "in_industry",
    "headquarters":    "headquartered_in",
    "key_people":      "has_key_person",
    "key_person":      "has_key_person",
    "parent":          "owned_by",
    "parent_company":  "owned_by",
}

# ── Company pre-filter (summary/title scan, no extra API call) ───────────────
_TITLE_MARKERS  = [" Inc.", " Corp.", " Ltd.", " Corporation", " Company", " GmbH", " plc", " LLC"]
_SUMMARY_WORDS  = [" company", " corporation", "manufacturer", "multinational", "conglomerate"]

def _is_company_candidate(article: dict) -> bool:
    title   = article.get("title", "")
    summary = (article.get("summary") or "").lower()
    if any(m in title for m in _TITLE_MARKERS):
        return True
    if any(w in summary for w in _SUMMARY_WORDS):
        return True
    return False

# ── Company confirmation (from infobox facts) ────────────────────────────────
_COMPANY_FACT_KEYS = {
    "company_type", "ticker_symbol", "traded_as",
    "products", "product", "services", "service",
    "brands", "brand", "subsidiaries", "subsidiary",
    "founded", "headquarters", "number_of_employees",
}

def _is_confirmed_company(facts: list) -> bool:
    for entry in (facts or []):
        if not isinstance(entry, list) or not entry:
            continue
        if _norm_key(str(entry[0])) in _COMPANY_FACT_KEYS:
            return True
    return False


# ── Connection extraction ────────────────────────────────────────────────────
def extract_connections(title: str, facts: list, predicate_map: dict) -> list[dict]:
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
        conns.append({
            "start":      title,
            "connection": predicate,
            "end":        end,
            "state":      0,
            "score":      2,       # score=2: fact-derived, higher quality than raw mentions
        })

    seen_pairs: set[tuple] = set()
    for entry in (facts or []):
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        key_raw, val_raw = str(entry[0]), str(entry[1])
        if (key_raw, val_raw) in seen_pairs:
            continue
        seen_pairs.add((key_raw, val_raw))

        key = _norm_key(key_raw)
        predicate = predicate_map.get(key)
        if not predicate:
            continue

        # Prefer wikilink targets — they are canonical article titles
        wl_targets = _wikilinks_in(val_raw)
        if wl_targets:
            for t in wl_targets:
                _add(predicate, t)
        else:
            # Plain text fallback — only for short, clean values
            plain = _strip_wikilinks(val_raw).strip()
            if plain and len(plain) <= 80 and "\n" not in plain:
                _add(predicate, plain)

    return conns


# ── Batch submit ─────────────────────────────────────────────────────────────
def submit_batch(session: requests.Session, batch: list[dict], dry_run: bool) -> tuple[int, int]:
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


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Import company-product connections from KoreReference")
    parser.add_argument("--limit",     type=int, default=0,        help="Max articles to scan (0 = all)")
    parser.add_argument("--dry-run",   action="store_true",         help="Count only, no writes")
    parser.add_argument("--all-facts", action="store_true",         help="Also include founders, headquarters, industry, parent")
    args = parser.parse_args()

    predicate_map = {**PRODUCT_PREDICATES, **(EXTRA_PREDICATES if args.all_facts else {})}

    print("KoreReference → KoreGraph: company connections", flush=True)
    print(f"  dry_run={args.dry_run}  all_facts={args.all_facts}  limit={args.limit or 'all'}", flush=True)
    print(f"  predicates: {sorted(set(predicate_map.values()))}", flush=True)
    print(flush=True)

    session = requests.Session()

    # Verify services
    for label, url in [("KoreReference", REF_URL), ("KoreGraph", GRAPH_URL)]:
        try:
            r = session.get(f"{url}/status", timeout=5)
            r.raise_for_status()
            print(f"  OK {label} reachable at {url}", flush=True)
        except Exception as exc:
            print(f"  FAIL {label}: {exc}", flush=True)
            sys.exit(1)
    print(flush=True)

    total_scanned   = 0
    total_skipped   = 0   # redirects
    total_fetched   = 0   # full article fetches
    total_companies = 0
    total_conns     = 0
    total_accepted  = 0
    total_errors    = 0
    offset          = 0
    batch: list[dict] = []
    t_start = time.time()

    while True:
        # ── Fetch article metadata page ──────────────────────────────────────
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
            if article.get("redirect_to"):
                total_skipped += 1
                continue

            total_scanned += 1

            # ── Phase 1: cheap pre-filter from summary/title ─────────────────
            if not _is_company_candidate(article):
                continue

            # ── Phase 2: fetch full article for facts ─────────────────────────
            try:
                r2 = session.get(
                    f"{REF_URL}/articles/{quote(title, safe='')}",
                    timeout=15,
                )
                r2.raise_for_status()
                full = r2.json()
            except Exception as exc:
                print(f"  [WARN] fetch failed for '{title}': {exc}", flush=True)
                continue

            total_fetched += 1
            facts = full.get("facts") or []

            # ── Phase 3: confirm it is a company ─────────────────────────────
            if not _is_confirmed_company(facts):
                continue

            total_companies += 1

            # ── Phase 4: extract connections ──────────────────────────────────
            conns = extract_connections(title, facts, predicate_map)
            batch.extend(conns)
            total_conns += len(conns)

            print(
                f"  [{total_companies:>4}] {title[:50]:50}  +{len(conns)} conns",
                flush=True,
            )

            if len(batch) >= BATCH_SIZE:
                accepted, errors = submit_batch(session, batch, args.dry_run)
                total_accepted += accepted
                total_errors   += errors
                batch = []

            if args.limit and total_scanned >= args.limit:
                break

        offset += PAGE_SIZE
        if args.limit and total_scanned >= args.limit:
            break
        if len(articles) < PAGE_SIZE:
            break

    # ── Flush remainder ───────────────────────────────────────────────────────
    if batch:
        accepted, errors = submit_batch(session, batch, args.dry_run)
        total_accepted += accepted
        total_errors   += errors

    elapsed = time.time() - t_start
    print(flush=True)
    print(f"Done in {elapsed:.1f}s", flush=True)
    print(f"  Articles scanned  : {total_scanned:,}", flush=True)
    print(f"  Redirects skipped : {total_skipped:,}", flush=True)
    print(f"  Full fetches      : {total_fetched:,}", flush=True)
    print(f"  Companies found   : {total_companies:,}", flush=True)
    print(f"  Connections found : {total_conns:,}", flush=True)
    print(f"  Accepted          : {total_accepted:,}", flush=True)
    print(f"  Errors            : {total_errors}", flush=True)
    if args.dry_run:
        print("(dry-run: nothing written)", flush=True)


if __name__ == "__main__":
    main()
