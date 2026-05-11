#!/usr/bin/env python3
# ====================================================================================================
# ingest_hansard.py — test ingest: 50 MPs and last week of Commons debates into hansard.db
#
# Creates:
#   datacontrol/koredata/RAG/databases/hansard.db   (Layer 1 chunk tables + Layer 2 h_* nav tables)
#   datacontrol/koredata/RAG/databases/hansard.json  (descriptor for KoreRAG registry)
#
# Usage (from KoreStack root, venv active):
#   python KoreData/KoreRAG/ingest_hansard.py
#   python KoreData/KoreRAG/ingest_hansard.py --members 20 --days 7 --max-debates 3
#
# Options:
#   --members N       MPs to fetch from Members API  (default 50)
#   --days N          Calendar days to look back for sitting days (default 20)
#   --max-debates N   Max debates per sitting day  (default 5)
#   --max-speeches N  Max speeches per debate  (default 30)
# ====================================================================================================
import sys
import re
import json
import zlib
import sqlite3
import time
import argparse
from pathlib import Path
from datetime import date, timedelta
from typing import Optional
from urllib.parse import unquote

import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE  = Path(__file__).resolve().parent          # .../KoreData/KoreRAG/
_ROOT  = _HERE.parents[1]                         # KoreStack root
_DB_DIR   = _ROOT / "datacontrol" / "koredata" / "RAG" / "databases"
_DB_PATH  = _DB_DIR / "hansard.db"
_JSON_PATH = _DB_DIR / "hansard.json"

# ---------------------------------------------------------------------------
# Parliament API
# ---------------------------------------------------------------------------

MEMBERS_API  = "https://members-api.parliament.uk/api"
HANSARD_BASE = "https://hansard.parliament.uk"
_HEADERS = {
    "User-Agent": "KoreStack/1.0 (hansard test ingest; open-source)",
    "Accept": "text/html,application/xhtml+xml,text/plain",
}

# ---------------------------------------------------------------------------
# Compression (same as CommonCode/compress.py)
# ---------------------------------------------------------------------------

def _compress(text: Optional[str]) -> Optional[bytes]:
    if not text:
        return None
    return zlib.compress(text.encode("utf-8"), level=6)


def _word_count(text: str) -> int:
    return len(text.split())

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str, as_text: bool = True) -> Optional[str]:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=20)
        if r.status_code == 200:
            if as_text:
                # Force UTF-8 regardless of what the server claims
                return r.content.decode("utf-8", errors="replace")
            return r
        print(f"    HTTP {r.status_code}: {url}")
        return None
    except Exception as exc:
        print(f"    Error: {url}: {exc}")
        return None


def _sleep(secs: float = 0.4) -> None:
    time.sleep(secs)

# ---------------------------------------------------------------------------
# Name → member_id matching (for populating h_speeches.member_id)
# ---------------------------------------------------------------------------

_INGEST_HONORIFICS = re.compile(
    r'^(Mr|Mrs|Ms|Miss|Dame|Sir|Lord|Baroness|Dr|Rt\.?\s+Hon\.?|Right\s+Hon\.?|The\s+\S+)\s+',
    flags=re.IGNORECASE,
)


def _bare_name(name: str) -> str:
    """Strip leading honorific from a display name."""
    return _INGEST_HONORIFICS.sub('', name).strip()


def _build_name_lookup(conn: sqlite3.Connection) -> dict[str, int]:
    """Return {bare_lower_name: member_id} for all ingested members."""
    rows = conn.execute("SELECT member_id, display_name FROM h_members").fetchall()
    lookup: dict[str, int] = {}
    for row in rows:
        lookup[_bare_name(row["display_name"]).lower()] = row["member_id"]
    return lookup


def _match_member(speaker_raw: str, lookup: dict[str, int]) -> Optional[int]:
    """Strip parentheticals + honorific from speaker_raw and look up member_id."""
    name = re.sub(r'\s*\([^)]+\)', '', speaker_raw).strip()
    return lookup.get(_bare_name(name).lower())


# ---------------------------------------------------------------------------
# Database init
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        -- Layer 1: standard KoreRAG tables (required by KoreRAG service)
        CREATE TABLE IF NOT EXISTS chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT,
            source      TEXT,
            tags        TEXT,
            content     BLOB,
            word_count  INTEGER,
            created_at  TEXT DEFAULT (datetime('now','utc'))
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            title, source, tags, content,
            tokenize='unicode61 remove_diacritics 1',
            content=''
        );

        -- Layer 2: Hansard navigation tables (invisible to KoreRAG service)
        CREATE TABLE IF NOT EXISTS h_sittings (
            sitting_date    TEXT PRIMARY KEY,
            house           TEXT NOT NULL DEFAULT 'Commons',
            volume          INTEGER,
            debate_count    INTEGER DEFAULT 0,
            ingested_at     TEXT DEFAULT (datetime('now','utc'))
        );
        CREATE TABLE IF NOT EXISTS h_debates (
            uuid            TEXT PRIMARY KEY,
            sitting_date    TEXT NOT NULL,
            house           TEXT NOT NULL DEFAULT 'Commons',
            title           TEXT,
            item_number     INTEGER,
            url             TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_h_debates_date ON h_debates(sitting_date);
        CREATE TABLE IF NOT EXISTS h_speeches (
            chunk_id        INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
            debate_uuid     TEXT NOT NULL,
            member_id       INTEGER,
            speaker_raw     TEXT,
            speech_order    INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_h_speeches_debate  ON h_speeches(debate_uuid);
        CREATE INDEX IF NOT EXISTS idx_h_speeches_member  ON h_speeches(member_id);
        CREATE TABLE IF NOT EXISTS h_members (
            member_id       INTEGER PRIMARY KEY,
            display_name    TEXT NOT NULL,
            house           TEXT,
            party           TEXT,
            constituency    TEXT,
            start_date      TEXT,
            end_date        TEXT,
            chunk_id        INTEGER REFERENCES chunks(id)
        );
    """)
    conn.commit()

# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

def _member_bio(v: dict, hm: dict, party: str) -> str:
    lines = [
        f"Name: {v.get('nameDisplayAs', '')}",
        f"Full title: {v.get('nameFullTitle', '')}",
        f"Party: {party}",
    ]
    if hm.get("membershipFrom"):
        lines.append(f"Constituency: {hm['membershipFrom']}")
    lines.append("House: House of Commons")
    s = hm.get("membershipStartDate", "")
    if s:
        lines.append(f"Member since: {s[:10]}")
    return "\n".join(lines)


def ingest_members(conn: sqlite3.Connection, limit: int = 50) -> int:
    print(f"\n  Fetching {limit} MPs from Members API (paginated, page_size=20)...")
    PAGE = 20  # API hard cap per page
    items: list[dict] = []
    skip = 0
    while len(items) < limit:
        url = (f"{MEMBERS_API}/Members/Search"
               f"?House=Commons&IsCurrentMember=true&take={PAGE}&skip={skip}")
        resp_text = _get(url)
        _sleep(0.3)
        if not resp_text:
            print("  FAILED to fetch members page")
            break
        page_items = json.loads(resp_text).get("items", [])
        if not page_items:
            break
        items.extend(page_items)
        skip += len(page_items)
    items = items[:limit]
    print(f"  API returned {len(items)} member records")
    ingested = 0

    for item in items:
        v = item.get("value", {})
        member_id = v.get("id")
        if not member_id:
            continue

        # Skip if already in db
        if conn.execute("SELECT 1 FROM h_members WHERE member_id=?", (member_id,)).fetchone():
            continue

        hm   = v.get("latestHouseMembership", {}) or {}
        party = (v.get("latestParty") or {}).get("name", "Unknown")
        constituency = hm.get("membershipFrom", "")
        start_date   = hm.get("membershipStartDate", "")
        end_date     = hm.get("membershipEndDate", "") or ""
        display_name = v.get("nameDisplayAs", "")

        bio        = _member_bio(v, hm, party)
        safe_party = re.sub(r"[^a-zA-Z0-9_]", "_", party.lower())
        tags       = f"type:member,house:commons,party:{safe_party},member_id:{member_id}"
        source     = f"{MEMBERS_API}/Members/{member_id}"
        wc         = _word_count(bio)
        compressed = _compress(bio)

        try:
            cur = conn.execute(
                "INSERT INTO chunks (title, source, tags, content, word_count) VALUES (?, ?, ?, ?, ?)",
                (display_name, source, tags, compressed, wc),
            )
            chunk_id = cur.lastrowid
            conn.execute(
                "INSERT INTO chunks_fts(rowid, title, source, tags, content) VALUES (?, ?, ?, ?, ?)",
                (chunk_id, display_name, source, tags, bio),
            )
            conn.execute(
                "INSERT INTO h_members (member_id, display_name, house, party, constituency, "
                "start_date, end_date, chunk_id) VALUES (?, ?, 'Commons', ?, ?, ?, ?, ?)",
                (member_id, display_name, party, constituency, start_date, end_date, chunk_id),
            )
            ingested += 1
        except Exception as exc:
            print(f"    Error inserting member {display_name}: {exc}")

    conn.commit()
    print(f"  Ingested {ingested} members")
    return ingested

# ---------------------------------------------------------------------------
# Sitting day discovery
# ---------------------------------------------------------------------------

# Procedural debate slugs to skip (they contain no speeches we care about)
_SKIP_SLUGS = {
    "HouseOfCommons", "WestminsterHall", "WrittenStatements", "Petitions",
    "WrittenCorrections", "BusinessWithoutDebate", "Prorogation",
    "MessageToAttendTheLordsCommissioners", "RoyalAssent", "DeferredDivision",
    "GeneralCommittees", "PublicBillCommittees", "GrandCommittee",
}


def _is_sitting_day(html: str) -> bool:
    low = html.lower()
    return "did not sit" not in low and "will not sit" not in low


def _extract_debates_from_html(html: str, sitting_date: str) -> list[dict]:
    """Extract unique debate records from href patterns in the day-index HTML."""
    seen: set[str] = set()
    debates: list[dict] = []

    # Match anchor tags whose href contains a debate UUID for this date
    # e.g. href="/Commons/2026-04-28/debates/UUID/Slug" or full https URL
    pattern = re.compile(
        r'<a\b[^>]*\bhref="(?:https://hansard\.parliament\.uk)?/Commons/'
        + re.escape(sitting_date)
        + r'/debates/'
        r'([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})'
        r'/([^"?#]*)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    for idx, m in enumerate(pattern.finditer(html)):
        uuid  = m.group(1).upper()
        slug  = m.group(2).strip("/")
        # Strip inner HTML tags then take only the FIRST non-empty line
        raw_stripped = re.sub(r'<[^>]+>', '', m.group(3))
        first_lines = [l.strip() for l in raw_stripped.split('\n') if l.strip()]
        raw_text = first_lines[0] if first_lines else ""

        if uuid in seen:
            continue
        if any(slug.startswith(s) for s in _SKIP_SLUGS):
            continue

        seen.add(uuid)
        title = unquote(raw_text) if raw_text else unquote(
            re.sub(r'([A-Z])', r' \1', slug).strip()
        )
        debates.append({
            "uuid":        uuid,
            "title":       title,
            "item_number": idx + 1,
            "url":         f"{HANSARD_BASE}/Commons/{sitting_date}/debates/{uuid}/{slug}",
        })

    return debates


def get_sitting_debates(sitting_date: str, max_debates: int = 5) -> Optional[tuple]:
    """Return (debates, sitting_info) or None if not a sitting day."""
    url = f"{HANSARD_BASE}/Commons/{sitting_date}"
    html = _get(url)
    _sleep(0.5)
    if not html or not _is_sitting_day(html):
        return None

    debates = _extract_debates_from_html(html, sitting_date)
    if not debates:
        return None

    vol_m  = re.search(r'Volume (\d+)', html)
    volume = int(vol_m.group(1)) if vol_m else None

    sitting_info = {"volume": volume, "total_debates": len(debates)}
    return debates[:max_debates], sitting_info

# ---------------------------------------------------------------------------
# Speech parsing
# ---------------------------------------------------------------------------

# A speaker attribution line looks like:
#   James Naish (Rushcliffe) (Lab)
#   The Prime Minister
#   Mr Rishi Sunak (Richmond and Northallerton) (Con)
# It: starts with capital, has <= 2 parenthetical groups, ends at line boundary.
_SPEAKER_LINE_RE = re.compile(
    r'^(?:The |Rt Hon |Right Hon |Mr |Mrs |Ms |Dr |Sir |Dame |'
    r'Lord |Lady |Baroness |Earl |Viscount |Bishop |Archbishop )?'
    r'[A-Z][A-Za-z\'\.\-\s]{1,60}'     # name body
    r'(?:\s*\([^)\n]{1,80}\))?'         # optional (Constituency)
    r'(?:\s*\([^)\n]{1,25}\))?'         # optional (Party)
    r'\s*$',
)

# Lines that look like speaker attributions but aren't
_NOT_SPEAKER = re.compile(
    r'^(?:That |Ordered|Bill |Motion |Amendment |Question |It |I |We |He |She |'
    r'This |There |In |On |At |By |With |From |For |The House|Proceedings|'
    r'Debate |\d)',
    re.IGNORECASE,
)


def _looks_like_speaker(line: str) -> bool:
    line = line.strip()
    if not line or len(line) < 4 or len(line) > 110:
        return False
    if not line[0].isupper():
        return False
    if line.endswith(':') or line.endswith(',') or line.endswith('.'):
        return False
    if re.match(r'^\d{2}:\d{2}', line):    # timestamp
        return False
    if _NOT_SPEAKER.match(line):
        return False
    return bool(_SPEAKER_LINE_RE.match(line))


def parse_speeches(text: str) -> list[dict]:
    """Split raw debate text into list of {speaker, content} dicts."""
    # Normalise line endings
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # Split into blank-line-separated paragraphs/blocks
    raw_blocks: list[str] = []
    buf: list[str] = []
    for line in text.split('\n'):
        if line.strip():
            buf.append(line)
        else:
            if buf:
                raw_blocks.append('\n'.join(buf))
                buf = []
    if buf:
        raw_blocks.append('\n'.join(buf))

    speeches: list[dict] = []
    current_speaker = "(Chamber)"
    current_parts: list[str] = []

    def _flush():
        content = '\n\n'.join(current_parts).strip()
        if content and _word_count(content) >= 5:
            speeches.append({"speaker": current_speaker, "content": content})

    for block in raw_blocks:
        first = block.split('\n')[0].strip()

        # Pure timestamp block — skip
        if re.match(r'^\d{2}:\d{2}:\d{2}$', first):
            continue

        # Check if this block starts with a speaker attribution
        if _looks_like_speaker(first):
            _flush()
            current_speaker = first
            current_parts   = []
            rest = '\n'.join(block.split('\n')[1:]).strip()
            if rest:
                current_parts.append(rest)
        else:
            current_parts.append(block)

    _flush()
    return speeches

# ---------------------------------------------------------------------------
# Debate ingestion
# ---------------------------------------------------------------------------

def ingest_debate(
    conn: sqlite3.Connection,
    debate: dict,
    sitting_date: str,
    max_speeches: int = 30,
    name_lookup: Optional[dict] = None,
) -> int:
    uuid  = debate["uuid"]
    title = debate["title"]

    # Skip if already ingested
    count = conn.execute(
        "SELECT COUNT(*) FROM h_speeches WHERE debate_uuid=?", (uuid,)
    ).fetchone()[0]
    if count:
        print(f"      ✓ already ingested ({count} speeches)")
        return 0

    url = f"{HANSARD_BASE}/debates/GetDebateAsText/{uuid}"
    text = _get(url)
    _sleep(0.5)
    if not text:
        return 0

    speeches = parse_speeches(text)
    if not speeches:
        print(f"      (no speeches parsed from {len(text)} chars)")
        return 0

    speeches = speeches[:max_speeches]
    ingested = 0

    for order, sp in enumerate(speeches):
        speaker_raw = sp["speaker"]
        content     = sp["content"]

        safe_spk = re.sub(r"[^a-zA-Z0-9_]", "_", speaker_raw[:40])
        tags = (
            f"type:speech,house:commons,date:{sitting_date},"
            f"debate:{uuid[:8].lower()},speaker:{safe_spk}"
        )
        chunk_title = f"{title[:60]} — {speaker_raw[:50]}"
        source      = debate["url"]
        wc          = _word_count(content)
        compressed  = _compress(content)

        try:
            cur = conn.execute(
                "INSERT INTO chunks (title, source, tags, content, word_count) VALUES (?, ?, ?, ?, ?)",
                (chunk_title, source, tags, compressed, wc),
            )
            chunk_id = cur.lastrowid
            conn.execute(
                "INSERT INTO chunks_fts(rowid, title, source, tags, content) VALUES (?, ?, ?, ?, ?)",
                (chunk_id, chunk_title, source, tags, content),
            )
            member_id = _match_member(speaker_raw, name_lookup) if name_lookup else None
            conn.execute(
                "INSERT OR IGNORE INTO h_speeches (chunk_id, debate_uuid, member_id, speaker_raw, speech_order) "
                "VALUES (?, ?, ?, ?, ?)",
                (chunk_id, uuid, member_id, speaker_raw, order),
            )
            ingested += 1
        except Exception as exc:
            print(f"        Error inserting speech {order}: {exc}")

    conn.commit()
    return ingested


def ingest_sitting_day(
    conn: sqlite3.Connection,
    sitting_date: str,
    max_debates: int = 25,
    max_speeches: int = 50,
    name_lookup: Optional[dict] = None,
) -> int:
    print(f"\n  {sitting_date}")
    result = get_sitting_debates(sitting_date, max_debates=max_debates)
    if result is None:
        print(f"    (not a sitting day)")
        return 0

    debates, sitting_info = result
    if not debates:
        print(f"    (no content debates found after filtering)")
        return 0

    print(f"    Volume {sitting_info['volume']}  "
          f"{len(debates)} debates (of {sitting_info['total_debates']} total)")

    # Upsert h_sittings
    conn.execute(
        "INSERT OR REPLACE INTO h_sittings (sitting_date, house, volume, debate_count) "
        "VALUES (?, 'Commons', ?, ?)",
        (sitting_date, sitting_info["volume"], sitting_info["total_debates"]),
    )
    conn.commit()

    total = 0
    for debate in debates:
        conn.execute(
            "INSERT OR IGNORE INTO h_debates (uuid, sitting_date, house, title, item_number, url) "
            "VALUES (?, ?, 'Commons', ?, ?, ?)",
            (debate["uuid"], sitting_date, debate["title"],
             debate["item_number"], debate["url"]),
        )
        conn.commit()

        print(f"    [{debate['item_number']:2d}] {debate['title'][:65]}")
        n = ingest_debate(conn, debate, sitting_date, max_speeches=max_speeches, name_lookup=name_lookup)
        print(f"         → {n} speech chunks")
        total += n
        _sleep(0.3)

    return total

# ---------------------------------------------------------------------------
# Descriptor
# ---------------------------------------------------------------------------

def _write_descriptor(total_chunks: int, last_date: str) -> None:
    d = {
        "id":           "hansard",
        "display_name": "Hansard — UK Parliament",
        "description":  "Official verbatim record of UK Parliamentary debates. "
                        "Sourced from developer.parliament.uk and hansard.parliament.uk.",
        "source_url":   "https://hansard.parliament.uk/",
        "licence":      "Open Parliament Licence",
        "managed_by":   "ingestor",
        "ingestor":     "ingest_hansard.py",
        "schedule":     "daily",
        "chunk_types":  [
            {"type": "speech",
             "required_tags": ["house", "date", "debate", "speaker"]},
            {"type": "member",
             "required_tags": ["house", "party", "member_id"]},
        ],
        "navigation":   {
            "type":   "hansard",
            "tables": ["h_sittings", "h_debates", "h_speeches", "h_members"],
        },
        "sync": {
            "last_run":            date.today().isoformat(),
            "last_date_ingested":  last_date,
            "status":              "ok",
            "total_chunks":        total_chunks,
        },
    }
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    _JSON_PATH.write_text(json.dumps(d, indent=2), encoding="utf-8")
    print(f"\n  Wrote descriptor → {_JSON_PATH.relative_to(_ROOT)}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Ingest Hansard data (MPs + debates) into hansard.db"
    )
    ap.add_argument("--members",      type=int, default=650,
                    help="MPs to fetch  (default 650 = all current Commons members)")
    ap.add_argument("--days",         type=int, default=20,
                    help="Calendar days to look back  (ignored when --from-date is set)")
    ap.add_argument("--from-date",    type=str, default=None,
                    metavar="YYYY-MM-DD",
                    help="Start date for ingest range (inclusive); scans forward to today")
    ap.add_argument("--max-debates",  type=int, default=25,
                    help="Max debates per sitting day; 0 = unlimited  (default 25)")
    ap.add_argument("--max-speeches", type=int, default=50,
                    help="Max speeches per debate  (default 50)")
    ap.add_argument("--reset",        action="store_true",
                    help="Delete and recreate the database before ingesting")
    args = ap.parse_args()

    print("=" * 65)
    print("  HANSARD INGEST")
    print(f"  DB:           {_DB_PATH}")
    print(f"  Members:      {args.members}")
    if args.from_date:
        print(f"  Date range:   {args.from_date} → today")
    else:
        print(f"  Lookback:     {args.days} calendar days")
    print(f"  Max debates:  {args.max_debates or 'unlimited'} per day")
    print(f"  Max speeches: {args.max_speeches} per debate")
    if args.reset:
        print("  Mode:         RESET (will delete existing DB)")
    print("=" * 65)

    if args.reset and _DB_PATH.exists():
        _DB_PATH.unlink()
        print(f"  Deleted existing DB: {_DB_PATH.name}")

    conn = _get_conn()
    init_db(conn)
    print("  Tables ready")

    # Phase 1: MPs
    member_count = ingest_members(conn, limit=args.members)
    name_lookup = _build_name_lookup(conn)
    print(f"  Name lookup: {len(name_lookup)} entries")

    # Phase 2: Sitting days
    print("\n  Scanning for sitting days...")
    total_speech_chunks = 0
    sitting_days_found  = 0
    last_date_ingested  = None

    today = date.today()
    if args.from_date:
        start = date.fromisoformat(args.from_date)
        num_days = (today - start).days + 1
        date_iter = [(start + timedelta(days=i)).isoformat() for i in range(num_days)]
    else:
        date_iter = [(today - timedelta(days=i)).isoformat() for i in range(args.days)]

    max_deb = args.max_debates if args.max_debates > 0 else 9999
    for check_date in date_iter:
        n = ingest_sitting_day(
            conn, check_date,
            max_debates=max_deb,
            max_speeches=args.max_speeches,
            name_lookup=name_lookup,
        )
        if n > 0:
            total_speech_chunks += n
            sitting_days_found  += 1
            if last_date_ingested is None:
                last_date_ingested = check_date

    # Summary
    total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    sitting_rows = conn.execute("SELECT sitting_date FROM h_sittings ORDER BY sitting_date DESC").fetchall()
    debate_rows  = conn.execute("SELECT COUNT(*) FROM h_debates").fetchone()[0]
    member_rows  = conn.execute("SELECT COUNT(*) FROM h_members").fetchone()[0]

    print("\n" + "=" * 65)
    print("  RESULTS")
    print(f"  Sitting days ingested:  {sitting_days_found}")
    for row in sitting_rows:
        print(f"    {row[0]}")
    print(f"  Debates:                {debate_rows}")
    print(f"  Members in h_members:   {member_rows}")
    print(f"  Speech chunks:          {total_speech_chunks}")
    print(f"  Total chunks in DB:     {total_chunks}")
    print("=" * 65)

    _write_descriptor(total_chunks, last_date_ingested or "")
    conn.close()
    print("  Done.\n")


if __name__ == "__main__":
    main()
