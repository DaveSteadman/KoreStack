# ====================================================================================================
# hansard_access.py — shared library for Hansard database ingestion
#
# Shared by all year-specific Hansard databases (Hansard2025, Hansard2026, …).
# Contains: schema init, progress tracking, Parliament API access, speech parsing,
#           member and debate ingestion, and descriptor writing.
#
# Each year folder contains its own copy of this file so the folder is
# fully self-contained and can be deleted independently.
#
#
# Public API
# ----------
#   get_conn(db_path)                                               → sqlite3.Connection
#   init_db(conn)                                                   → None
#   get_meta(conn, key, default=None)                              → str | None
#   set_meta(conn, key, value)                                      → None
#   ingest_members(conn, limit)                                     → int
#   build_name_lookup(conn)                                         → dict[str, int]
#   get_sitting_debates(sitting_date, max_debates)                  → tuple | None
#   parse_speeches(text)                                            → list[dict]
#   ingest_debate(conn, debate, sitting_date, max_speeches, ...)    → int
#   ingest_sitting_day(conn, sitting_date, max_debates, ...)        → int
#   write_descriptor(json_path, db_id, total_chunks, last_date)     → None
# ====================================================================================================
import re
import json
import zlib
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

import requests

# ---------------------------------------------------------------------------
# Parliament API
# ---------------------------------------------------------------------------

MEMBERS_API  = "https://members-api.parliament.uk/api"
HANSARD_BASE = "https://hansard.parliament.uk"

_HEADERS = {
    "User-Agent": "KoreStack/1.0 (hansard ingest; open-source)",
    "Accept": "text/html,application/xhtml+xml,text/plain",
}

# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------

def _compress(text: Optional[str]) -> Optional[bytes]:
    if not text:
        return None
    return zlib.compress(text.encode("utf-8"), level=6)


def _word_count(text: str) -> int:
    return len(text.split())


_RETRY_STATUSES = {429, 503}
_MAX_RETRIES    = 3


def _get(url: str, as_text: bool = True) -> Optional[str]:
    """GET with exponential-backoff retry on 429/503 and transient network errors."""
    delay = 5.0
    for attempt in range(_MAX_RETRIES + 1):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=20)
            if r.status_code == 200:
                if as_text:
                    return r.content.decode("utf-8", errors="replace")
                return r
            if r.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                print(f"    HTTP {r.status_code} — retrying in {delay:.0f}s: {url}")
                time.sleep(delay)
                delay *= 2
                continue
            print(f"    HTTP {r.status_code}: {url}")
            return None
        except Exception as exc:
            if attempt < _MAX_RETRIES:
                print(f"    Error ({exc}) — retrying in {delay:.0f}s: {url}")
                time.sleep(delay)
                delay *= 2
                continue
            print(f"    Error: {url}: {exc}")
            return None
    return None


def _sleep(secs: float = 0.4) -> None:
    time.sleep(secs)

# ---------------------------------------------------------------------------
# Database connection and schema
# ---------------------------------------------------------------------------

def get_conn(db_path: Path) -> sqlite3.Connection:
    """Open (or create) a Hansard SQLite database at db_path."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if they do not already exist."""
    conn.executescript("""
        -- Progress / metadata
        CREATE TABLE IF NOT EXISTS _meta (
            key     TEXT PRIMARY KEY,
            value   TEXT
        );

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
            sitting_date    TEXT NOT NULL,
            house           TEXT NOT NULL DEFAULT 'Commons',
            volume          INTEGER,
            debate_count    INTEGER DEFAULT 0,
            ingested_at     TEXT DEFAULT (datetime('now','utc')),
            PRIMARY KEY (sitting_date, house)
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
# Progress tracking
# ---------------------------------------------------------------------------

def get_meta(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    """Read a value from the _meta progress table."""
    row = conn.execute("SELECT value FROM _meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Write a value to the _meta progress table."""
    conn.execute("INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)", (key, value))
    conn.commit()

# ---------------------------------------------------------------------------
# Name → member_id matching
# ---------------------------------------------------------------------------

_INGEST_HONORIFICS = re.compile(
    r'^(Mr|Mrs|Ms|Miss|Dame|Sir|Lord|Baroness|Dr|Rt\.?\s+Hon\.?|Right\s+Hon\.?|The\s+\S+)\s+',
    flags=re.IGNORECASE,
)


def _bare_name(name: str) -> str:
    return _INGEST_HONORIFICS.sub('', name).strip()


def build_name_lookup(conn: sqlite3.Connection) -> dict[str, int]:
    """Return {bare_lower_name: member_id} for all ingested members."""
    rows = conn.execute("SELECT member_id, display_name FROM h_members").fetchall()
    lookup: dict[str, int] = {}
    for row in rows:
        lookup[_bare_name(row["display_name"]).lower()] = row["member_id"]
    return lookup


def _match_member(speaker_raw: str, lookup: dict[str, int]) -> Optional[int]:
    name = re.sub(r'\s*\([^)]+\)', '', speaker_raw).strip()
    return lookup.get(_bare_name(name).lower())

# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

def _member_bio(v: dict, hm: dict, party: str, house: str = "Commons") -> str:
    lines = [
        f"Name: {v.get('nameDisplayAs', '')}",
        f"Full title: {v.get('nameFullTitle', '')}",
        f"Party: {party}",
    ]
    if hm.get("membershipFrom"):
        label = "Constituency" if house == "Commons" else "Peerage / seat"
        lines.append(f"{label}: {hm['membershipFrom']}")
    lines.append(f"House: House of {house}")
    s = hm.get("membershipStartDate", "")
    if s:
        lines.append(f"Member since: {s[:10]}")
    return "\n".join(lines)


def ingest_members(conn: sqlite3.Connection, limit: int = 650, house: str = "Commons") -> int:
    """Fetch and store current members for the given house. Skips members already present in h_members."""
    label = "MPs" if house == "Commons" else "Lords"
    print(f"\n  Fetching {limit} {label} from Members API (paginated, page_size=20)...")
    PAGE = 20
    items: list[dict] = []
    skip = 0
    while len(items) < limit:
        url = (f"{MEMBERS_API}/Members/Search"
               f"?House={house}&IsCurrentMember=true&take={PAGE}&skip={skip}")
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
        if conn.execute("SELECT 1 FROM h_members WHERE member_id=?", (member_id,)).fetchone():
            continue

        hm           = v.get("latestHouseMembership", {}) or {}
        party        = (v.get("latestParty") or {}).get("name", "Unknown")
        constituency = hm.get("membershipFrom", "")
        start_date   = hm.get("membershipStartDate", "")
        end_date     = hm.get("membershipEndDate", "") or ""
        display_name = v.get("nameDisplayAs", "")

        bio        = _member_bio(v, hm, party, house=house)
        safe_party = re.sub(r"[^a-zA-Z0-9_]", "_", party.lower())
        safe_house = house.lower()
        tags       = f"type:member,house:{safe_house},party:{safe_party},member_id:{member_id}"
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
                "start_date, end_date, chunk_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (member_id, display_name, house, party, constituency, start_date, end_date, chunk_id),
            )
            ingested += 1
        except Exception as exc:
            print(f"    Error inserting member {display_name}: {exc}")

    conn.commit()
    print(f"  Ingested {ingested} new members")
    return ingested

# ---------------------------------------------------------------------------
# Sitting day discovery
# ---------------------------------------------------------------------------

_SKIP_SLUGS = {
    "HouseOfCommons", "WestminsterHall", "WrittenStatements", "Petitions",
    "WrittenCorrections", "BusinessWithoutDebate", "Prorogation",
    "MessageToAttendTheLordsCommissioners", "RoyalAssent", "DeferredDivision",
    "GeneralCommittees", "PublicBillCommittees", "GrandCommittee",
}

_SKIP_SLUGS_LORDS = {
    "HouseOfLords", "WrittenStatements", "WrittenCorrections",
    "BusinessWithoutDebate", "Prorogation", "RoyalAssent",
    "GrandCommittee", "PublicBillCommittees",
}


def _is_sitting_day(html: str) -> bool:
    low = html.lower()
    return "did not sit" not in low and "will not sit" not in low


def _extract_debates_from_html(html: str, sitting_date: str, house: str = "Commons") -> list[dict]:
    seen: set[str] = set()
    debates: list[dict] = []
    skip_slugs = _SKIP_SLUGS_LORDS if house == "Lords" else _SKIP_SLUGS
    pattern = re.compile(
        r'<a\b[^>]*\bhref="(?:https://hansard\.parliament\.uk)?/'
        + re.escape(house)
        + r'/'
        + re.escape(sitting_date)
        + r'/debates/'
        r'([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})'
        r'/([^"?#]*)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    for idx, m in enumerate(pattern.finditer(html)):
        uuid = m.group(1).upper()
        slug = m.group(2).strip("/")
        raw_stripped = re.sub(r'<[^>]+>', '', m.group(3))
        first_lines = [l.strip() for l in raw_stripped.split('\n') if l.strip()]
        raw_text = first_lines[0] if first_lines else ""
        if uuid in seen:
            continue
        if any(slug.startswith(s) for s in skip_slugs):
            continue
        seen.add(uuid)
        title = unquote(raw_text) if raw_text else unquote(
            re.sub(r'([A-Z])', r' \1', slug).strip()
        )
        debates.append({
            "uuid":        uuid,
            "title":       title,
            "item_number": idx + 1,
            "url":         f"{HANSARD_BASE}/{house}/{sitting_date}/debates/{uuid}/{slug}",
        })
    return debates


def get_sitting_debates(sitting_date: str, max_debates: int = 25, house: str = "Commons") -> Optional[tuple]:
    """Return (debates, sitting_info) or None if not a sitting day."""
    url  = f"{HANSARD_BASE}/{house}/{sitting_date}"
    html = _get(url)
    _sleep(0.5)
    if not html or not _is_sitting_day(html):
        return None
    debates = _extract_debates_from_html(html, sitting_date, house=house)
    if not debates:
        return None
    vol_m  = re.search(r'Volume (\d+)', html)
    volume = int(vol_m.group(1)) if vol_m else None
    sitting_info = {"volume": volume, "total_debates": len(debates)}
    return debates[:max_debates], sitting_info

# ---------------------------------------------------------------------------
# Speech parsing
# ---------------------------------------------------------------------------

_SPEAKER_LINE_RE = re.compile(
    r'^(?:The |Rt Hon |Right Hon |Mr |Mrs |Ms |Dr |Sir |Dame |'
    r'Lord |Lady |Baroness |Earl |Viscount |Bishop |Archbishop )?'
    r'[A-Z][A-Za-z\'\.\-\s]{1,60}'
    r'(?:\s*\([^)\n]{1,80}\))?'
    r'(?:\s*\([^)\n]{1,25}\))?'
    r'\s*$',
)

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
    if re.match(r'^\d{2}:\d{2}', line):
        return False
    if _NOT_SPEAKER.match(line):
        return False
    return bool(_SPEAKER_LINE_RE.match(line))


def parse_speeches(text: str) -> list[dict]:
    """Split raw debate text into list of {speaker, content} dicts."""
    text = text.replace('\r\n', '\n').replace('\r', '\n')
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
        if re.match(r'^\d{2}:\d{2}:\d{2}$', first):
            continue
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
    max_speeches: int = 50,
    name_lookup: Optional[dict] = None,
    house: str = "Commons",
) -> int:
    uuid  = debate["uuid"]
    title = debate["title"]

    count = conn.execute(
        "SELECT COUNT(*) FROM h_speeches WHERE debate_uuid=?", (uuid,)
    ).fetchone()[0]
    if count:
        print(f"      ✓ already ingested ({count} speeches)")
        return 0

    url  = f"{HANSARD_BASE}/debates/GetDebateAsText/{uuid}"
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
        safe_spk    = re.sub(r"[^a-zA-Z0-9_]", "_", speaker_raw[:40])
        safe_house  = house.lower()
        tags        = (
            f"type:speech,house:{safe_house},date:{sitting_date},"
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
                "INSERT OR IGNORE INTO h_speeches "
                "(chunk_id, debate_uuid, member_id, speaker_raw, speech_order) "
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
    house: str = "Commons",
) -> int:
    print(f"\n  [{house}] {sitting_date}")
    result = get_sitting_debates(sitting_date, max_debates=max_debates, house=house)
    if result is None:
        print(f"    (not a sitting day)")
        return 0

    debates, sitting_info = result
    if not debates:
        print(f"    (no content debates found after filtering)")
        return 0

    print(f"    Volume {sitting_info['volume']}  "
          f"{len(debates)} debates (of {sitting_info['total_debates']} total)")

    conn.execute(
        "INSERT OR REPLACE INTO h_sittings (sitting_date, house, volume, debate_count) "
        "VALUES (?, ?, ?, ?)",
        (sitting_date, house, sitting_info["volume"], sitting_info["total_debates"]),
    )
    conn.commit()

    total = 0
    for debate in debates:
        conn.execute(
            "INSERT OR IGNORE INTO h_debates (uuid, sitting_date, house, title, item_number, url) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (debate["uuid"], sitting_date, house, debate["title"],
             debate["item_number"], debate["url"]),
        )
        conn.commit()

        print(f"    [{debate['item_number']:2d}] {debate['title'][:65]}")
        n = ingest_debate(conn, debate, sitting_date,
                          max_speeches=max_speeches, name_lookup=name_lookup,
                          house=house)
        print(f"         → {n} speech chunks")
        total += n
        _sleep(0.3)

    return total

# ---------------------------------------------------------------------------
# Descriptor
# ---------------------------------------------------------------------------

def write_descriptor(
    json_path: Path,
    db_id: str,
    total_chunks: int,
    last_date: str,
    status: str = "complete",
) -> None:
    d = {
        "id":           db_id,
        "display_name": f"Hansard — UK Parliament ({db_id})",
        "description":  "Official verbatim record of UK Parliamentary debates. "
                        "Sourced from developer.parliament.uk and hansard.parliament.uk.",
        "source_url":   "https://hansard.parliament.uk/",
        "licence":      "Open Parliament Licence",
        "managed_by":   "ingestor",
        "ingestor":     db_id,
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
            "last_run":           date.today().isoformat(),
            "last_date_ingested": last_date,
            "status":             status,
            "total_chunks":       total_chunks,
        },
    }
    json_path.write_text(json.dumps(d, indent=2), encoding="utf-8")
    print(f"\n  Wrote descriptor → {json_path.name}")
