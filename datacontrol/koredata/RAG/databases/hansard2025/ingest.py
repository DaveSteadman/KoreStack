#!/usr/bin/env python3
# ====================================================================================================
# Hansard2025 / ingest.py — ingest 2025 UK Parliament debates
#
# Ingests all Commons sitting days for the full 2025 parliamentary year
# (2025-01-01 to 2025-12-31).  Once the checkpoint reaches 2025-12-31 the
# dataset is complete and subsequent runs do nothing.
#
# Database and descriptor live alongside this script:
#   datacontrol/koredata/RAG/databases/hansard2025/hansard2025.db
#   datacontrol/koredata/RAG/databases/hansard2025/hansard2025.json
#
# Usage (from KoreStack root, venv active):
#   python datacontrol/koredata/RAG/databases/hansard2025/ingest.py           # resume from checkpoint
#   python datacontrol/koredata/RAG/databases/hansard2025/ingest.py --reset   # wipe and restart
#
# Options:
#   --members N       MPs to fetch from Members API  (default: 650)
#   --from-date DATE  Override start date YYYY-MM-DD (ignores checkpoint)
#   --to-date DATE    Override end date YYYY-MM-DD  (default: 2025-12-31)
#   --max-debates N   Max debates per sitting day; 0 = unlimited  (default: 25)
#   --max-speeches N  Max speeches per debate  (default: 50)
#   --reset           Delete and recreate the database before ingesting
# ====================================================================================================
import sys
import argparse
from pathlib import Path
from datetime import date, timedelta

_HERE    = Path(__file__).resolve().parent                # .../databases/hansard2025/
sys.path.insert(0, str(_HERE))                         # hansard_access.py lives in this folder

import hansard_access as hansard  # noqa: E402

_DB_PATH    = _HERE / "hansard2025.db"
_JSON_PATH  = _HERE / "hansard2025.json"
_DB_ID      = "hansard2025"
_YEAR       = 2025
_YEAR_START = date(_YEAR, 1, 1)
_YEAR_END   = date(_YEAR, 12, 31)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=f"Ingest {_YEAR} UK Parliament Hansard debates (full year backfill)"
    )
    ap.add_argument("--members",      type=int, default=650,
                    help="MPs to fetch  (default 650)")
    ap.add_argument("--lords-members", type=int, default=800,
                    help="Lords to fetch from Members API  (default 800)")
    ap.add_argument("--from-date",    type=str, default=None, metavar="YYYY-MM-DD",
                    help="Override start date; ignores checkpoint")
    ap.add_argument("--to-date",      type=str, default=None, metavar="YYYY-MM-DD",
                    help=f"Override end date  (default: {_YEAR_END})")
    ap.add_argument("--max-debates",  type=int, default=25,
                    help="Max debates per sitting day; 0 = unlimited  (default 25)")
    ap.add_argument("--max-speeches", type=int, default=50,
                    help="Max speeches per debate  (default 50)")
    ap.add_argument("--reset",        action="store_true",
                    help="Delete and recreate the database before ingesting")
    args = ap.parse_args()

    range_end = date.fromisoformat(args.to_date) if args.to_date else _YEAR_END

    print("=" * 65)
    print(f"  HANSARD {_YEAR} INGEST")
    print(f"  DB:           {_DB_PATH}")
    print(f"  Members:      {args.members}")
    print(f"  Max debates:  {args.max_debates or 'unlimited'} per day")
    print(f"  Max speeches: {args.max_speeches} per debate")
    if args.reset:
        print("  Mode:         RESET (will delete existing DB)")
    print("=" * 65)

    if args.reset and _DB_PATH.exists():
        _DB_PATH.unlink()
        if _JSON_PATH.exists():
            _JSON_PATH.unlink()
        print(f"  Deleted existing DB: {_DB_PATH.name}")

    conn = hansard.get_conn(_DB_PATH)
    hansard.init_db(conn)
    print("  Tables ready")

    # ------------------------------------------------------------------
    # Determine date range, honouring the checkpoint unless overridden
    # ------------------------------------------------------------------
    if args.from_date:
        range_start = date.fromisoformat(args.from_date)
        lords_range_start = range_start
        print(f"  Date range:   {range_start} \u2192 {range_end} (from --from-date)")
    else:
        last_checked = hansard.get_meta(conn, "last_date_checked")
        if last_checked:
            range_start = date.fromisoformat(last_checked) + timedelta(days=1)
            print(f"  Checkpoint:   resuming from {range_start} (last checked: {last_checked})")
        else:
            range_start = _YEAR_START
            print(f"  No checkpoint; starting from {range_start} ({_YEAR} year start)")
        last_checked_lords = hansard.get_meta(conn, "last_date_checked_lords")
        if last_checked_lords:
            lords_range_start = date.fromisoformat(last_checked_lords) + timedelta(days=1)
            print(f"  Lords checkpoint: resuming from {lords_range_start} (last checked: {last_checked_lords})")
        else:
            lords_range_start = _YEAR_START
            print(f"  Lords: no checkpoint; starting from {lords_range_start}")

    num_days       = (range_end - range_start).days + 1
    lords_num_days = (range_end - lords_range_start).days + 1
    if num_days <= 0 and lords_num_days <= 0:
        print("  Already up to date — nothing to do.")
        conn.close()
        return

    if num_days > 0:
        print(f"  Scanning {num_days} calendar day(s) [Commons]: {range_start} → {range_end}")
    else:
        print("  Commons: already up to date")

    # ------------------------------------------------------------------
    # Phase 1a: Commons MPs
    # ------------------------------------------------------------------
    hansard.ingest_members(conn, limit=args.members, house="Commons")

    # ------------------------------------------------------------------
    # Phase 1b: Lords members
    # ------------------------------------------------------------------
    hansard.ingest_members(conn, limit=args.lords_members, house="Lords")

    name_lookup = hansard.build_name_lookup(conn)
    print(f"  Name lookup: {len(name_lookup)} entries")

    # ------------------------------------------------------------------
    # Phase 2: Commons sitting days
    # ------------------------------------------------------------------
    print("\n  Scanning for Commons sitting days...")
    total_speech_chunks = 0
    sitting_days_found  = 0
    last_date_ingested  = hansard.get_meta(conn, "last_date_ingested")

    max_deb   = args.max_debates if args.max_debates > 0 else 9999
    date_iter = [(range_start + timedelta(days=i)).isoformat() for i in range(num_days)]

    for check_date in date_iter:
        n = hansard.ingest_sitting_day(
            conn, check_date,
            max_debates=max_deb,
            max_speeches=args.max_speeches,
            name_lookup=name_lookup,
            house="Commons",
        )
        hansard.set_meta(conn, "last_date_checked", check_date)

        if n > 0:
            total_speech_chunks += n
            sitting_days_found  += 1
            last_date_ingested   = check_date
            hansard.set_meta(conn, "last_date_ingested", check_date)

    # ------------------------------------------------------------------
    # Phase 3: Lords sitting days
    # ------------------------------------------------------------------
    lords_sitting_days_found = 0
    last_lords_date_ingested = hansard.get_meta(conn, "last_date_ingested_lords")
    if lords_num_days > 0:
        print(f"\n  Scanning {lords_num_days} calendar day(s) for Lords: {lords_range_start} \u2192 {range_end}")
        lords_date_iter = [(lords_range_start + timedelta(days=i)).isoformat() for i in range(lords_num_days)]
        for check_date in lords_date_iter:
            n = hansard.ingest_sitting_day(
                conn, check_date,
                max_debates=max_deb,
                max_speeches=args.max_speeches,
                name_lookup=name_lookup,
                house="Lords",
            )
            hansard.set_meta(conn, "last_date_checked_lords", check_date)

            if n > 0:
                total_speech_chunks      += n
                lords_sitting_days_found += 1
                last_lords_date_ingested  = check_date
                hansard.set_meta(conn, "last_date_ingested_lords", check_date)
    else:
        print("  Lords already up to date.")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    sitting_rows = conn.execute(
        "SELECT sitting_date, house FROM h_sittings ORDER BY sitting_date DESC"
    ).fetchall()
    debate_count    = conn.execute("SELECT COUNT(*) FROM h_debates").fetchone()[0]
    commons_members = conn.execute("SELECT COUNT(*) FROM h_members WHERE house='Commons'").fetchone()[0]
    lords_members   = conn.execute("SELECT COUNT(*) FROM h_members WHERE house='Lords'").fetchone()[0]

    print("\n" + "=" * 65)
    print("  RESULTS")
    print(f"  Commons sitting days this run:   {sitting_days_found}")
    print(f"  Lords sitting days this run:     {lords_sitting_days_found}")
    for row in sitting_rows[:10]:
        print(f"    {row[0]}  ({row[1]})")
    if len(sitting_rows) > 10:
        print(f"    ... ({len(sitting_rows)} total in DB)")
    print(f"  Debates in DB:                   {debate_count}")
    print(f"  Commons members in h_members:    {commons_members}")
    print(f"  Lords members in h_members:      {lords_members}")
    print(f"  Speech chunks this run:          {total_speech_chunks}")
    print(f"  Total chunks in DB:              {total_chunks}")
    print("=" * 65)

    ingest_status = "complete" if range_end >= _YEAR_END else "ok"
    last_ingested = last_lords_date_ingested or last_date_ingested or ""
    hansard.write_descriptor(_JSON_PATH, _DB_ID, total_chunks, last_ingested, status=ingest_status)
    conn.close()
    if ingest_status == "complete":
        print("  2025 dataset complete.\n")
    else:
        print("  Done.\n")


if __name__ == "__main__":
    main()
