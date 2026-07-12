import re
from typing import Optional

from app.database import _decompress, db_connection


NAVIGATION_TYPE  = "hansard"
EXPLORE_TEMPLATE = "rag_explore.html"
SITTING_TEMPLATE = "rag_explore_sitting.html"
DEBATE_TEMPLATE  = "rag_explore_debate.html"
MEMBER_TEMPLATE  = "rag_explore_member.html"
EXPLORE_CONTEXT  = {
    "sittings":  [],
    "members":   [],
    "databases": [],
    "db_info":   {},
    "errors":    [],
    "timings":   [],
}
SITTING_CONTEXT  = {
    "debates":   [],
    "databases": [],
    "db_info":   {},
    "errors":    [],
    "timings":   [],
}
DEBATE_CONTEXT   = {
    "debate":    {},
    "speeches":  [],
    "databases": [],
    "db_info":   {},
    "errors":    [],
    "timings":   [],
}
MEMBER_CONTEXT   = {
    "member":    {},
    "speeches":  [],
    "databases": [],
    "db_info":   {},
    "errors":    [],
    "timings":   [],
}

_HONORIFICS = re.compile(
    r'^(Mr|Mrs|Ms|Miss|Dame|Sir|Lord|Baroness|Dr|The\s+\S+)\s+',
    flags = re.IGNORECASE,
)


def has_navigation(db: str = "default") -> bool:
    with db_connection(db) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='h_sittings'"
        ).fetchone()
    return row is not None


def get_sittings(db: str = "default") -> list[dict]:
    with db_connection(db) as conn:
        rows = conn.execute(
            "SELECT sitting_date, house, debate_count FROM h_sittings ORDER BY sitting_date DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_sitting_debates(date: str, db: str = "default") -> list[dict]:
    with db_connection(db) as conn:
        rows = conn.execute(
            """
            SELECT d.uuid, d.title, d.item_number, d.url,
                   COUNT(s.chunk_id) AS speech_count
            FROM h_debates d
            LEFT JOIN h_speeches s ON s.debate_uuid = d.uuid
            WHERE d.sitting_date = ?
            GROUP BY d.uuid
            ORDER BY d.item_number
            """,
            (date,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_members(db: str = "default") -> list[dict]:
    with db_connection(db) as conn:
        rows = conn.execute(
            """
            SELECT m.member_id, m.display_name, m.party, m.constituency, m.chunk_id,
                   COUNT(s.chunk_id) AS speech_count
            FROM h_members m
            LEFT JOIN h_speeches s ON s.member_id = m.member_id
            GROUP BY m.member_id
            ORDER BY speech_count DESC, m.display_name
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_debate(debate_uuid: str, db: str = "default") -> Optional[dict]:
    with db_connection(db) as conn:
        row = conn.execute(
            "SELECT uuid, title, item_number, url, sitting_date FROM h_debates WHERE uuid = ?",
            (debate_uuid,),
        ).fetchone()
    return dict(row) if row else None


def get_debate_speeches(debate_uuid: str, db: str = "default") -> list[dict]:
    with db_connection(db) as conn:
        rows = conn.execute(
            """
            SELECT s.chunk_id, s.speech_order, s.speaker_raw,
                   m.member_id, m.display_name AS member_name, m.party, m.constituency,
                   c.title, c.word_count, c.content
            FROM h_speeches s
            JOIN chunks c ON c.id = s.chunk_id
            LEFT JOIN h_members m ON m.member_id = s.member_id
            WHERE s.debate_uuid = ?
            ORDER BY s.speech_order
            """,
            (debate_uuid,),
        ).fetchall()
    result = []
    for row in rows:
        item            = dict(row)
        item["content"] = _decompress(item.get("content"))
        result.append(item)
    return result


def _bare_name(display_name: str) -> str:
    return _HONORIFICS.sub("", display_name).strip()


def get_member_by_id(member_id: int, db: str = "default") -> Optional[dict]:
    with db_connection(db) as conn:
        row = conn.execute(
            "SELECT member_id, display_name, house, party, constituency, chunk_id "
            "FROM h_members WHERE member_id = ?",
            (member_id,),
        ).fetchone()
        if row is None:
            return None
        item = dict(row)
        if item["chunk_id"]:
            bio = conn.execute(
                "SELECT content, word_count FROM chunks WHERE id = ?",
                (item["chunk_id"],),
            ).fetchone()
            if bio:
                item["bio"]            = _decompress(bio["content"])
                item["bio_word_count"] = bio["word_count"]
    return item


def get_member_speeches(member_id: int, db: str = "default") -> list[dict]:
    with db_connection(db) as conn:
        member = conn.execute(
            "SELECT member_id FROM h_members WHERE member_id = ?",
            (member_id,),
        ).fetchone()
        if member is None:
            return []
        rows = conn.execute(
            """
            SELECT s.chunk_id, s.speech_order, s.speaker_raw,
                   d.uuid AS debate_uuid, d.title AS debate_title, d.sitting_date,
                   c.word_count, c.content
            FROM h_speeches s
            JOIN h_debates d ON d.uuid = s.debate_uuid
            JOIN chunks c ON c.id = s.chunk_id
            WHERE s.member_id = ?
            ORDER BY d.sitting_date DESC, s.speech_order
            """,
            (member_id,),
        ).fetchall()
    result = []
    for row in rows:
        item            = dict(row)
        item["content"] = _decompress(item.get("content"))
        result.append(item)
    return result


def build_explore_payload(db_id: str, *, databases: list[dict], db_info: dict) -> dict:
    return {
        "db_id":     db_id,
        "sittings":  get_sittings(db=db_id),
        "members":   get_members(db=db_id),
        "databases": databases,
        "db_info":   db_info,
        "errors":    [],
        "timings":   [],
    }


def build_sitting_payload(db_id: str, date: str, *, databases: list[dict], db_info: dict) -> dict:
    return {
        "db_id":     db_id,
        "date":      date,
        "debates":   get_sitting_debates(date=date, db=db_id),
        "databases": databases,
        "db_info":   db_info,
        "errors":    [],
        "timings":   [],
    }


def build_debate_payload(db_id: str, uuid: str, *, databases: list[dict], db_info: dict) -> dict:
    return {
        "db_id":     db_id,
        "debate":    get_debate(debate_uuid=uuid, db=db_id) or {},
        "speeches":  get_debate_speeches(debate_uuid=uuid, db=db_id),
        "databases": databases,
        "db_info":   db_info,
        "errors":    [],
        "timings":   [],
    }


def build_member_payload(db_id: str, member_id: int, *, databases: list[dict], db_info: dict) -> dict:
    return {
        "db_id":     db_id,
        "member":    get_member_by_id(member_id=member_id, db=db_id) or {},
        "speeches":  get_member_speeches(member_id=member_id, db=db_id),
        "databases": databases,
        "db_info":   db_info,
        "errors":    [],
        "timings":   [],
    }
