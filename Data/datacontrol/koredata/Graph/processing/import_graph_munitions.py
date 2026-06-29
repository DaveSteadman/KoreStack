#!/usr/bin/env python3
"""
import_graph_munitions.py
========================
Imports military munitions connections from KoreReference into KoreGraph.

Covers: missiles (air-to-air, air-to-surface, cruise, ballistic, anti-tank),
        bombs, rockets, torpedoes, grenades, artillery shells, ammunition.

Strategy:
  1. Page through all articles (metadata only)
  2. Pre-filter by summary keywords
  3. Fetch full article for each candidate
  4. Confirm via infobox: place_of_origin (distinctive of Infobox weapon) + type
  5. Extract typed connections and submit to KoreGraph at score=2

Default connections (from infobox fact keys):
  type                              → "has_type"    (e.g. "Air-to-air missile")
  manufacturer                      → "made_by"     (e.g. "Raytheon")
  designer                          → "designed_by" (e.g. "Hughes Aircraft Company")
  place_of_origin / national_origin → "originated_in"
  wars                              → "used_in"     (e.g. "Vietnam War", "Gulf War")
  variants / variant                → "has_variant"
  used_by                           → "operated_by" (wikilink targets only)

With --extra, also adds:
  guidance_system                   → "guided_by"   (e.g. "Infrared homing")
  warhead                           → "has_warhead" (e.g. "Continuous-rod warhead")
  launch_platform / launched_from   → "launched_from"

Usage:
  python import_graph_munitions.py --dry-run
  python import_graph_munitions.py --dry-run --extra
  python import_graph_munitions.py               # live run

Endpoints:
    KoreReference: GET {REF_URL}/articles?offset=N&limit=200
                                 GET {REF_URL}/articles/{title}
    KoreGraph:     POST {GRAPH_URL}/api/connections/by-name/batch
"""
# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Import graph munitions helpers for datacontrol/koredata/Graph/processing.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

import argparse
import io
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


# ── Configuration ─────────────────────────────────────────────────────────────
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
    """Strip wikilinks and normalise to snake_case. Handles non-breaking spaces."""
    s = _strip_wikilinks(raw).strip().lower()
    return re.sub(r'[^a-z0-9]+', '_', s).strip('_')


# ── Predicate maps ────────────────────────────────────────────────────────────
BASE_PREDICATES: dict[str, str] = {
    # Type classification
    "type":                 "has_type",
    # Manufacturer
    "manufacturer":         "made_by",
    "manufacturers":        "made_by",
    # Designer (often different from manufacturer e.g. Hughes Aircraft → Boeing)
    "designer":             "designed_by",
    "designers":            "designed_by",
    # Country of origin  (key uses non-breaking spaces → normalises to place_of_origin)
    "place_of_origin":      "originated_in",
    "national_origin":      "originated_in",
    "country_of_origin":    "originated_in",
    # Conflicts — very rich wikilink lists
    "wars":                 "used_in",
    "conflict":             "used_in",
    "conflicts":            "used_in",
    # Operators (often "See Operators" plain text — wikilinks only will produce results)
    "used_by":              "operated_by",
    "operators":            "operated_by",
    "operator":             "operated_by",
    # Variants
    "variants":             "has_variant",
    "variant":              "has_variant",
}

EXTRA_PREDICATES: dict[str, str] = {
    # Guidance — wikilink targets like "Infrared homing", "Semi-active radar homing"
    "guidance_system":      "guided_by",
    "guidance":             "guided_by",
    # Warhead type — wikilink targets like "Continuous-rod warhead", "Shaped charge"
    "warhead":              "has_warhead",
    # Launch platforms
    "launch_platform":      "launched_from",
    "launched_from":        "launched_from",
}


# ── Summary pre-filter ────────────────────────────────────────────────────────
_SUMMARY_WORDS = [
    " missile", " rocket", "torpedo", " bomb ",
    "air-to-air", "air-to-surface", "surface-to-air",
    "anti-tank missile", "anti-ship missile",
    "cruise missile", "ballistic missile",
    "guided bomb", "unguided bomb",
    " grenade", "artillery shell", " ammunition",
    "anti-radiation missile", "air-launched",
]

def _is_munition_candidate(article: dict) -> bool:
    s = (article.get("summary") or "").lower()
    return any(w in s for w in _SUMMARY_WORDS)


# ── Munition confirmation (from facts) ───────────────────────────────────────
_MUNITION_TYPE_WORDS = {
    "missile", "bomb", "rocket", "torpedo", "ammunition", "shell",
    "grenade", "warhead", "air-to-air", "air-to-surface", "surface-to-air",
    "anti-tank", "anti-ship", "cruise", "ballistic", "guided", "mortar",
    "mine", "depth charge", "cluster munition", "incendiary",
    "cartridge", "bullet", "projectile", "munition",
}

# Type substrings that indicate a weapons PLATFORM, not a munition.
# Prevents self-propelled guns, rocket launchers, etc. from matching.
_PLATFORM_EXCLUSIONS = {
    "launcher", "cannon", "howitzer", "machine gun", "rifle",
    " gun ", "gun-type", "aircraft", "helicopter", "tank ",
}

def _is_confirmed_munition(facts: list) -> bool:
    """Weapon infobox articles have place_of_origin + type with munition keywords.
    Requiring both prevents vehicle/platform articles (tanks, guns, howitzers)
    from matching even though they share the same infobox template."""
    has_place_origin  = False
    has_munition_type = False
    for entry in (facts or []):
        if not isinstance(entry, list) or not entry:
            continue
        key = _norm_key(str(entry[0]))
        if key == "place_of_origin":
            has_place_origin = True
        if key == "type" and len(entry) > 1:
            v = str(entry[1]).lower()
            is_munition = any(w in v for w in _MUNITION_TYPE_WORDS)
            is_platform = any(w in v for w in _PLATFORM_EXCLUSIONS)
            if is_munition and not is_platform:
                has_munition_type = True
    # Require BOTH: Infobox weapon origin signature + munitions-class type
    return has_place_origin and has_munition_type


# ── Connection extraction ─────────────────────────────────────────────────────
def extract_connections(title: str, facts: list, predicate_map: dict) -> list[dict]:
    conns: list[dict] = []
    seen:  set[tuple] = set()

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
            # Plain text fallback — short, clean values only (e.g. "United States")
            plain = _strip_wikilinks(val_raw).strip()
            if plain and len(plain) <= 60 and "\n" not in plain:
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
    # Ensure stdout handles non-ASCII article titles on Windows
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Import military munitions connections from KoreReference")
    parser.add_argument("--limit",   type=int, default=0,  help="Max articles to scan (0 = all)")
    parser.add_argument("--dry-run", action="store_true",   help="Count only, no writes")
    parser.add_argument("--extra",   action="store_true",   help="Also include guided_by, has_warhead, launched_from")
    args = parser.parse_args()

    predicate_map = {**BASE_PREDICATES, **(EXTRA_PREDICATES if args.extra else {})}

    print("KoreReference -> KoreGraph: military munitions", flush=True)
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

    total_scanned  = 0
    total_fetched  = 0
    total_munitions = 0
    total_conns    = 0
    total_accepted = 0
    total_errors   = 0
    offset         = 0
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

            if not _is_munition_candidate(article):
                continue

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

            if not _is_confirmed_munition(facts):
                continue

            total_munitions += 1
            conns = extract_connections(title, facts, predicate_map)
            batch.extend(conns)
            total_conns += len(conns)

            print(
                f"  [{total_munitions:>3}] {title[:55]:55}  +{len(conns)} conns",
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
    print(f"  Munitions found   : {total_munitions:,}", flush=True)
    print(f"  Connections found : {total_conns:,}", flush=True)
    print(f"  Accepted          : {total_accepted:,}", flush=True)
    print(f"  Errors            : {total_errors}", flush=True)
    if args.dry_run:
        print("(dry-run: nothing written)", flush=True)


if __name__ == "__main__":
    main()
