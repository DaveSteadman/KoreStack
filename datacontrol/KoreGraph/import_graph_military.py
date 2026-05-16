#!/usr/bin/env python3
"""
import_graph_military.py
========================
Imports military platform connections from KoreReference into KoreGraph.

Covers: fighter jets, bombers, helicopters, transport aircraft, tanks,
        armored vehicles, warships, submarines, drones.

Strategy:
  1. Page through all articles (metadata only)
  2. Pre-filter by summary keywords (~80 candidates from 18,827 articles)
  3. Fetch full article for each candidate
  4. Confirm it is a military platform via infobox facts
  5. Extract typed connections and submit to KoreGraph at score=2

Default connections (from infobox fact keys):
  manufacturer / aerospace_manufacturer / built_by  → "made_by"
  designer                                          → "designed_by"
  primary_users / primary_user / operators          → "operated_by"
  type / role                                       → "has_type"
  variants / variant                                → "has_variant"
  national_origin                                   → "originated_in"

With --extra, also adds:
  developed_from / predecessor                      → "derived_from"
  developed_into / successor                        → "developed_into"

Usage:
  python import_graph_military.py --dry-run
  python import_graph_military.py --dry-run --extra
  python import_graph_military.py                    # live run

Endpoints:
  KoreReference: GET http://127.0.0.1:8624/articles?offset=N&limit=200
                 GET http://127.0.0.1:8624/articles/{title}
  KoreGraph:     POST http://127.0.0.1:8626/api/connections/by-name/batch
"""
import argparse
import re
import sys
import time
from urllib.parse import quote

import requests

# ── Configuration ─────────────────────────────────────────────────────────────
REF_URL    = "http://127.0.0.1:8624"
GRAPH_URL  = "http://127.0.0.1:8626"
BATCH_SIZE = 200
PAGE_SIZE  = 200

# ── Wikilink helpers ──────────────────────────────────────────────────────────
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
    s = _strip_wikilinks(raw).strip().lower()
    return re.sub(r'[^a-z0-9]+', '_', s).strip('_')


# ── Predicate maps ────────────────────────────────────────────────────────────
BASE_PREDICATES: dict[str, str] = {
    # Manufacturers (multiple key variants in the wild)
    "manufacturer":            "made_by",
    "manufacturers":           "made_by",
    "aerospace_manufacturer":  "made_by",   # [[Manufacturer|Aerospace manufacturer]]
    "built_by":                "made_by",
    "designer":                "designed_by",
    "designers":               "designed_by",
    # Operators
    "primary_users":           "operated_by",
    "primary_user":            "operated_by",
    "operators":               "operated_by",
    "operator":                "operated_by",
    "users":                   "operated_by",
    # Platform type / role
    "type":                    "has_type",
    "role":                    "has_type",
    # Variants
    "variants":                "has_variant",
    "variant":                 "has_variant",
    # Country of origin (plain text — creates country vocab nodes)
    "national_origin":         "originated_in",
    "country_of_origin":       "originated_in",
}

EXTRA_PREDICATES: dict[str, str] = {
    "developed_from":          "derived_from",
    "predecessor":             "derived_from",
    "developed_into":          "developed_into",
    "successor":               "developed_into",
}

# ── Summary pre-filter ────────────────────────────────────────────────────────
_SUMMARY_WORDS = [
    "fighter aircraft", "fighter jet", "multirole fighter", "combat aircraft",
    "attack aircraft", "ground-attack", " bomber", "strategic bomber",
    "military helicopter", "attack helicopter", "utility helicopter",
    "transport helicopter", "helicopter gunship",
    "main battle tank", "battle tank", "armored vehicle", "armoured vehicle",
    "infantry fighting vehicle", "armored personnel carrier",
    "warship", " destroyer", " frigate", "aircraft carrier", " submarine",
    "military transport", "airlifter", "tactical airlifter",
    "unmanned aerial vehicle", "combat drone",
    "strike aircraft", "interceptor aircraft", "jet trainer",
]

def _is_platform_candidate(article: dict) -> bool:
    s = (article.get("summary") or "").lower()
    return any(w in s for w in _SUMMARY_WORDS)


# ── Platform confirmation (from facts) ───────────────────────────────────────
_OPERATOR_KEYS    = {"primary_users", "primary_user", "operators", "operator", "users"}
_TYPE_KEYS        = {"type", "role"}
_MANUFACTURER_KEYS = {"manufacturer", "manufacturers", "aerospace_manufacturer", "built_by"}
_TYPE_WORDS    = {
    "fighter", "bomber", "helicopter", "tank", "airlifter", "multirole",
    "attack aircraft", "interceptor", "trainer", "destroyer", "submarine",
    "frigate", "carrier", "transport aircraft", "patrol", "gunship",
    "combat aircraft", "drone", "uav",
}

def _is_confirmed_platform(facts: list) -> bool:
    has_operators     = False
    has_military_type = False
    has_manufacturer  = False
    for entry in (facts or []):
        if not isinstance(entry, list) or not entry:
            continue
        key = _norm_key(str(entry[0]))
        if key in _OPERATOR_KEYS:
            has_operators = True
        if key in _MANUFACTURER_KEYS:
            has_manufacturer = True
        if key in _TYPE_KEYS and len(entry) > 1:
            v = str(entry[1]).lower()
            if any(w in v for w in _TYPE_WORDS):
                has_military_type = True
    # Require a manufacturer to exclude units, bases, and incidents
    return has_manufacturer and (has_operators or has_military_type)


# ── Connection extraction ─────────────────────────────────────────────────────
def extract_connections(title: str, facts: list, predicate_map: dict) -> list[dict]:
    conns: list[dict] = []
    seen: set[tuple]  = set()

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
            "score":      2,
        })

    seen_pairs: set[tuple] = set()
    for entry in (facts or []):
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        key_raw, val_raw = str(entry[0]), str(entry[1])
        if (key_raw, val_raw) in seen_pairs:
            continue
        seen_pairs.add((key_raw, val_raw))

        key       = _norm_key(key_raw)
        predicate = predicate_map.get(key)
        if not predicate:
            continue

        # Prefer wikilink targets (canonical article titles)
        wl_targets = _wikilinks_in(val_raw)
        if wl_targets:
            for t in wl_targets:
                _add(predicate, t)
        else:
            # Plain text fallback (e.g. national_origin = "United States")
            plain = _strip_wikilinks(val_raw).strip()
            if plain and len(plain) <= 80 and "\n" not in plain:
                _add(predicate, plain)

    return conns


# ── Batch submit ──────────────────────────────────────────────────────────────
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


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Import military platform connections from KoreReference")
    parser.add_argument("--limit",   type=int, default=0,   help="Max articles to scan (0 = all)")
    parser.add_argument("--dry-run", action="store_true",    help="Count only, no writes")
    parser.add_argument("--extra",   action="store_true",    help="Also include derived_from / developed_into / predecessor / successor")
    args = parser.parse_args()

    predicate_map = {**BASE_PREDICATES, **(EXTRA_PREDICATES if args.extra else {})}

    print("KoreReference → KoreGraph: military platforms", flush=True)
    print(f"  dry_run={args.dry_run}  extra={args.extra}  limit={args.limit or 'all'}", flush=True)
    print(f"  predicates: {sorted(set(predicate_map.values()))}", flush=True)
    print(flush=True)

    session = requests.Session()

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
    total_fetched   = 0
    total_platforms = 0
    total_conns     = 0
    total_accepted  = 0
    total_errors    = 0
    offset          = 0
    batch: list[dict] = []
    t_start = time.time()

    while True:
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
            if not title or article.get("redirect_to"):
                continue

            total_scanned += 1

            if not _is_platform_candidate(article):
                continue

            # Fetch full article for facts
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

            if not _is_confirmed_platform(facts):
                continue

            total_platforms += 1
            conns = extract_connections(title, facts, predicate_map)
            batch.extend(conns)
            total_conns += len(conns)

            print(
                f"  [{total_platforms:>3}] {title[:55]:55}  +{len(conns)} conns",
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

    if batch:
        accepted, errors = submit_batch(session, batch, args.dry_run)
        total_accepted += accepted
        total_errors   += errors

    elapsed = time.time() - t_start
    print(flush=True)
    print(f"Done in {elapsed:.1f}s", flush=True)
    print(f"  Articles scanned  : {total_scanned:,}", flush=True)
    print(f"  Full fetches      : {total_fetched:,}", flush=True)
    print(f"  Platforms found   : {total_platforms:,}", flush=True)
    print(f"  Connections found : {total_conns:,}", flush=True)
    print(f"  Accepted          : {total_accepted:,}", flush=True)
    print(f"  Errors            : {total_errors}", flush=True)
    if args.dry_run:
        print("(dry-run: nothing written)", flush=True)


if __name__ == "__main__":
    main()
