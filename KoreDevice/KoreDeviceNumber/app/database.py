import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.config import cfg

DATA_DIR = Path(cfg["data_dir"])
DB_PATH  = DATA_DIR / "device_numbers.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_timestamp(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return _utc_now()
    raw = raw.replace(" ", "T")
    if raw.endswith("Z"):
        return raw
    try:
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except ValueError:
        return _utc_now()


@contextmanager
def db_connection():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                name          TEXT PRIMARY KEY,
                display_name  TEXT,
                unit          TEXT,
                normal_min    REAL,
                normal_max    REAL,
                sample_count  INTEGER NOT NULL DEFAULT 0,
                last_value    REAL,
                last_seen     TEXT,
                min_value     REAL,
                max_value     REAL,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS samples (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_name  TEXT NOT NULL,
                observed_at  TEXT NOT NULL,
                value        REAL NOT NULL,
                source       TEXT,
                note         TEXT,
                FOREIGN KEY(signal_name) REFERENCES signals(name)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_signal_time ON samples(signal_name, observed_at DESC)")


def _signal_notice(row: sqlite3.Row | dict, trend: str | None) -> str | None:
    last_value = row["last_value"]
    if last_value is None:
        return None
    if row["normal_min"] is not None and last_value < row["normal_min"]:
        return "below normal range"
    if row["normal_max"] is not None and last_value > row["normal_max"]:
        return "above normal range"
    if trend == "rising":
        return "rising within range"
    if trend == "falling":
        return "falling within range"
    return "within normal range"


def _signal_trend(conn: sqlite3.Connection, name: str) -> str | None:
    rows = conn.execute(
        "SELECT value FROM samples WHERE signal_name = ? ORDER BY observed_at DESC, id DESC LIMIT 3",
        (name,),
    ).fetchall()
    if len(rows) < 3:
        return None
    values = [float(row["value"]) for row in reversed(rows)]
    if values[0] < values[1] < values[2]:
        return "rising"
    if values[0] > values[1] > values[2]:
        return "falling"
    if values[0] == values[1] == values[2]:
        return "stable"
    return "mixed"


def record_sample(
    name:         str,
    value:        float,
    observed_at:  str | None = None,
    display_name: str | None = None,
    unit:         str | None = None,
    source:       str | None = None,
    note:         str | None = None,
    normal_min:   float | None = None,
    normal_max:   float | None = None,
) -> dict:
    signal_name = name.strip()
    if not signal_name:
        raise ValueError("signal name is required")

    observed = _normalize_timestamp(observed_at)
    now      = _utc_now()

    with db_connection() as conn:
        existing = conn.execute("SELECT * FROM signals WHERE name = ?", (signal_name,)).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO signals (
                    name, display_name, unit, normal_min, normal_max,
                    sample_count, last_value, last_seen, min_value, max_value, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (signal_name, display_name, unit, normal_min, normal_max, now, now),
            )
            existing = conn.execute("SELECT * FROM signals WHERE name = ?", (signal_name,)).fetchone()

        next_display_name = display_name if display_name not in (None, "") else existing["display_name"]
        next_unit         = unit         if unit         not in (None, "") else existing["unit"]
        next_normal_min   = normal_min   if normal_min   is not None        else existing["normal_min"]
        next_normal_max   = normal_max   if normal_max   is not None        else existing["normal_max"]
        next_min_value    = value if existing["min_value"] is None else min(float(existing["min_value"]), value)
        next_max_value    = value if existing["max_value"] is None else max(float(existing["max_value"]), value)
        next_count        = int(existing["sample_count"] or 0) + 1

        conn.execute(
            "INSERT INTO samples (signal_name, observed_at, value, source, note) VALUES (?, ?, ?, ?, ?)",
            (signal_name, observed, value, source, note),
        )
        conn.execute(
            """
            UPDATE signals
               SET display_name = ?,
                   unit         = ?,
                   normal_min   = ?,
                   normal_max   = ?,
                   sample_count = ?,
                   last_value   = ?,
                   last_seen    = ?,
                   min_value    = ?,
                   max_value    = ?,
                   updated_at   = ?
             WHERE name = ?
            """,
            (
                next_display_name,
                next_unit,
                next_normal_min,
                next_normal_max,
                next_count,
                value,
                observed,
                next_min_value,
                next_max_value,
                now,
                signal_name,
            ),
        )
        return get_signal(signal_name, sample_limit=20, conn=conn)


def list_signals(limit: int = 200) -> list[dict]:
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM signals ORDER BY COALESCE(last_seen, created_at) DESC, name ASC LIMIT ?",
            (limit,),
        ).fetchall()
        items: list[dict] = []
        for row in rows:
            trend  = _signal_trend(conn, row["name"])
            notice = _signal_notice(row, trend)
            item   = dict(row)
            item["trend"]  = trend
            item["notice"] = notice
            items.append(item)
        return items


def get_signal(name: str, sample_limit: int = 100, conn: sqlite3.Connection | None = None) -> dict | None:
    owns_conn = conn is None
    manager   = None
    if conn is None:
        manager = db_connection()
        conn    = manager.__enter__()
    try:
        row = conn.execute("SELECT * FROM signals WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        samples = conn.execute(
            """
            SELECT id, signal_name, observed_at, value, source, note
              FROM samples
             WHERE signal_name = ?
             ORDER BY observed_at DESC, id DESC
             LIMIT ?
            """,
            (name, sample_limit),
        ).fetchall()
        trend  = _signal_trend(conn, name)
        notice = _signal_notice(row, trend)
        return {
            **dict(row),
            "trend":   trend,
            "notice":  notice,
            "samples": [dict(sample) for sample in samples],
        }
    finally:
        if owns_conn and manager is not None:
            manager.__exit__(None, None, None)


def get_status() -> dict:
    with db_connection() as conn:
        signal_row = conn.execute("SELECT COUNT(*) AS count FROM signals").fetchone()
        sample_row = conn.execute("SELECT COUNT(*) AS count, MAX(observed_at) AS last_sample_at FROM samples").fetchone()
        return {
            "ok":             True,
            "service":        "KoreDeviceNumber",
            "total_signals":  int(signal_row["count"] if signal_row else 0),
            "total_samples":  int(sample_row["count"] if sample_row else 0),
            "last_sample_at": sample_row["last_sample_at"] if sample_row else None,
        }
