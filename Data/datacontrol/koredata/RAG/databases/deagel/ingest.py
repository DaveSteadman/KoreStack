#!/usr/bin/env python3
# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Ingest helpers for datacontrol/koredata/RAG/databases/deagel.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================
import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import deagel_access as deagel  # noqa: E402

_DB_PATH   = _HERE / "deagel.db"
_JSON_PATH = _HERE / "deagel.json"
_DB_ID     = "deagel"


def _write_progress(*, conn, status: str, extra_sync: dict | None = None) -> None:
    deagel.write_descriptor(
        _JSON_PATH,
        _DB_ID,
        total_chunks = deagel.count_chunks(conn),
        status       = status,
        extra_sync   = extra_sync,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest structured Deagel military and aviation data")
    ap.add_argument("--reset",           action="store_true", help="Delete and recreate the database before ingesting")
    ap.add_argument("--bootstrap-only",  action="store_true", help="Create schema and descriptor only; do not fetch remote content")
    ap.add_argument("--categories-limit", type=int, default=0, help="Limit category pages processed; 0 = all")
    ap.add_argument("--items-limit",      type=int, default=0, help="Limit item pages processed across all categories; 0 = all")
    ap.add_argument("--countries-limit",  type=int, default=0, help="Limit country pages processed; 0 = all")
    ap.add_argument("--news-limit",       type=int, default=12, help="Limit latest news articles fetched from the homepage")
    args = ap.parse_args()

    print("=" * 72)
    print("  DEAGEL INGEST")
    print(f"  DB:              {_DB_PATH}")
    print(f"  Categories:      {args.categories_limit or 'all'}")
    print(f"  Items:           {args.items_limit or 'all'}")
    print(f"  Countries:       {args.countries_limit or 'all'}")
    print(f"  Latest news:     {args.news_limit}")
    print(f"  Bootstrap only:  {'yes' if args.bootstrap_only else 'no'}")
    if args.reset:
        print("  Mode:            RESET")
    print("=" * 72)

    if args.reset and _DB_PATH.exists():
        _DB_PATH.unlink()
        print(f"  Deleted existing DB: {_DB_PATH.name}")

    conn = deagel.get_conn(_DB_PATH)
    deagel.init_db(conn)
    print("  Tables ready")
    _write_progress(conn=conn, status="running")

    try:
        if args.bootstrap_only:
            _write_progress(conn=conn, status="idle")
            print("  Bootstrap complete.")
            return

        category_stats = deagel.ingest_categories(
            conn,
            category_limit = args.categories_limit or None,
            item_limit     = args.items_limit or None,
        )
        _write_progress(
            conn       = conn,
            status     = "running",
            extra_sync = category_stats,
        )
        country_stats = deagel.ingest_countries(
            conn,
            country_limit = args.countries_limit or None,
        )
        _write_progress(
            conn       = conn,
            status     = "running",
            extra_sync = {**category_stats, **country_stats},
        )
        report_count = deagel.ingest_reports(conn)
        _write_progress(
            conn       = conn,
            status     = "running",
            extra_sync = {
                **category_stats,
                **country_stats,
                "reports_processed": report_count,
            },
        )
        news_count   = deagel.ingest_latest_news(conn, news_limit=args.news_limit)

        total_chunks = deagel.count_chunks(conn)
        _write_progress(
            conn       = conn,
            status     = "complete",
            extra_sync = {
                **category_stats,
                **country_stats,
                "reports_processed": report_count,
                "news_processed":    news_count,
            },
        )

        print("\n" + "=" * 72)
        print("  RESULTS")
        print(f"  Categories processed:  {category_stats['categories_processed']}")
        print(f"  Item pages processed:  {category_stats['items_processed']}")
        print(f"  Item pages failed:     {category_stats.get('item_pages_failed', 0)}")
        print(f"  Countries processed:   {country_stats['countries_processed']}")
        print(f"  Reports processed:     {report_count}")
        print(f"  News processed:        {news_count}")
        print(f"  Total chunks in DB:    {total_chunks}")
        print("=" * 72)
        print("  Done.")
    except Exception as exc:
        deagel.write_failed_descriptor(_JSON_PATH, _DB_ID, exc)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
