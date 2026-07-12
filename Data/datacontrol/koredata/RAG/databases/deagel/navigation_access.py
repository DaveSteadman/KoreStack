import sqlite3
from typing import Optional

from app.database import db_connection


NAVIGATION_TYPE   = "deagel"
EXPLORE_TEMPLATE  = "rag_explore_deagel.html"
CATEGORY_TEMPLATE = "rag_explore_deagel_category.html"
EXPLORE_CONTEXT   = {
    "categories": [],
    "countries":  [],
    "reports":    [],
    "news":       [],
    "databases":  [],
    "db_info":    {},
    "errors":     [],
    "timings":    [],
}
CATEGORY_CONTEXT  = {
    "category":  {},
    "groups":    [],
    "items":     [],
    "databases": [],
    "db_info":   {},
    "errors":    [],
    "timings":   [],
}


def _table_has_column(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(str(row["name"]).lower() == str(column_name).lower() for row in rows)


def has_navigation(db: str = "default") -> bool:
    with db_connection(db) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='d_categories'"
        ).fetchone()
    return row is not None


def get_categories(db: str = "default") -> list[dict]:
    with db_connection(db) as conn:
        rows = conn.execute(
            """
            WITH known_categories AS (
                SELECT 'aerospace_forces' AS category_id, 'Aerospace Forces' AS title, 'https://www.deagel.com/Aerospace%20Forces' AS category_url, '' AS item_count_text
                UNION ALL SELECT 'armies',         'Armies',           'https://www.deagel.com/Armies',            ''
                UNION ALL SELECT 'navies',         'Navies',           'https://www.deagel.com/Navies',            ''
                UNION ALL SELECT 'weapons',        'Weapons',          'https://www.deagel.com/Weapons',           ''
                UNION ALL SELECT 'components',     'Components',       'https://www.deagel.com/Components',        ''
                UNION ALL SELECT 'civil_aviation', 'Civil Aviation',   'https://www.deagel.com/Civil%20Aviation',  ''
            ),
            stats AS (
                SELECT i.category_id,
                       COUNT(DISTINCT i.item_id)      AS item_count,
                       COUNT(DISTINCT i.group_name)   AS group_count
                FROM d_items i
                WHERE COALESCE(i.category_id, '') <> ''
                GROUP BY i.category_id
            ),
            category_union AS (
                SELECT k.category_id, k.title, k.category_url, k.item_count_text
                FROM known_categories k
                UNION
                SELECT c.category_id, c.title, c.category_url, c.item_count_text
                FROM d_categories c
                UNION
                SELECT s.category_id,
                       REPLACE(REPLACE(UPPER(SUBSTR(s.category_id, 1, 1)) || SUBSTR(s.category_id, 2), '_', ' '), '  ', ' ') AS title,
                       'https://www.deagel.com/' || REPLACE(REPLACE(REPLACE(UPPER(SUBSTR(s.category_id, 1, 1)) || SUBSTR(s.category_id, 2), '_', ' '), '  ', ' '), ' ', '%20') AS category_url,
                       '' AS item_count_text
                FROM stats s
            )
            SELECT u.category_id,
                   MIN(u.title)           AS title,
                   MIN(u.category_url)    AS category_url,
                   MAX(u.item_count_text) AS item_count_text,
                   COALESCE(MAX(s.item_count), 0)  AS item_count,
                   COALESCE(MAX(s.group_count), 0) AS group_count
            FROM category_union u
            LEFT JOIN stats s ON s.category_id = u.category_id
            GROUP BY u.category_id
            ORDER BY title
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_category(category_id: str, db: str = "default") -> Optional[dict]:
    with db_connection(db) as conn:
        row = conn.execute(
            "SELECT category_id, title, category_url, item_count_text, updated_at FROM d_categories WHERE category_id = ?",
            (category_id,),
        ).fetchone()
    return dict(row) if row else None


def get_category_items(category_id: str, db: str = "default") -> list[dict]:
    with db_connection(db) as conn:
        chunk_id_expr = "i.chunk_id" if _table_has_column(conn, "d_items", "chunk_id") else "NULL AS chunk_id"
        rows = conn.execute(
            """
            SELECT i.item_id,
                   i.page_code,
                   COALESCE(i.anchor_id, ci.item_anchor)   AS anchor_id,
                   {chunk_id_expr},
                   COALESCE(i.title, ci.item_title)        AS title,
                   ci.item_url                             AS item_url,
                   ci.group_name                           AS group_name,
                   ci.sort_order                           AS item_sort_order,
                   i.status,
                   i.origin_country,
                   i.contractor,
                   i.initial_operational_capability_text,
                   i.first_flight_text,
                   i.total_production_text,
                   i.summary
            FROM d_category_items ci
            LEFT JOIN d_items i
                   ON i.category_id = ci.category_id
                  AND i.item_url    = ci.item_url
            WHERE ci.category_id = ?
            ORDER BY ci.sort_order, ci.group_name, COALESCE(i.title, ci.item_title)
            """.format(chunk_id_expr=chunk_id_expr),
            (category_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_category_groups(category_id: str, db: str = "default") -> list[dict]:
    with db_connection(db) as conn:
        rows = conn.execute(
            """
            WITH grouped AS (
                SELECT ci.group_name                AS group_name,
                       MIN(ci.sort_order)           AS sort_order,
                       COUNT(*)                     AS item_count
                FROM d_category_items ci
                WHERE ci.category_id = ?
                  AND COALESCE(ci.group_name, '') <> ''
                GROUP BY ci.group_name
            )
            SELECT group_name, sort_order, item_count
            FROM grouped
            ORDER BY sort_order, group_name
            """,
            (category_id,),
        ).fetchall()
        if rows:
            return [dict(r) for r in rows]

        rows = conn.execute(
            """
            SELECT i.group_name AS group_name,
                   NULL         AS sort_order,
                   COUNT(*)     AS item_count
            FROM d_items i
            WHERE i.category_id = ?
              AND COALESCE(i.group_name, '') <> ''
            GROUP BY i.group_name
            ORDER BY i.group_name
            """,
            (category_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_countries(db: str = "default") -> list[dict]:
    with db_connection(db) as conn:
        rows = conn.execute(
            """
            WITH operator_countries AS (
                SELECT country_name AS title,
                       COUNT(*)     AS item_count
                FROM d_item_operators
                WHERE COALESCE(country_name, '') <> ''
                GROUP BY country_name
            ),
            country_union AS (
                SELECT c.country_id,
                       c.title,
                       c.country_url,
                       c.year_text,
                       c.ranking_order,
                       c.ranking_wealth,
                       c.ranking_strength,
                       c.ranking_population,
                       NULL AS item_count
                FROM d_countries c
                UNION
                SELECT LOWER(REPLACE(REPLACE(REPLACE(REPLACE(title, ' ', '_'), '.', ''), '-', '_'), '&', 'and')) AS country_id,
                       title,
                       'https://www.deagel.com/Country/' || REPLACE(title, ' ', '%20') AS country_url,
                       ''   AS year_text,
                       NULL AS ranking_order,
                       ''   AS ranking_wealth,
                       ''   AS ranking_strength,
                       ''   AS ranking_population,
                       item_count
                FROM operator_countries
            )
            SELECT country_id,
                   title,
                   MIN(country_url)          AS country_url,
                   MAX(year_text)            AS year_text,
                   MIN(ranking_order)        AS ranking_order,
                   MAX(ranking_wealth)       AS ranking_wealth,
                   MAX(ranking_strength)     AS ranking_strength,
                   MAX(ranking_population)   AS ranking_population,
                   MAX(item_count)           AS item_count
            FROM country_union
            GROUP BY country_id, title
            ORDER BY CASE WHEN ranking_order IS NULL THEN 1 ELSE 0 END, ranking_order, item_count DESC, title
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_item_by_page_anchor(page_code: str, anchor_id: str, db: str = "default") -> Optional[dict]:
    with db_connection(db) as conn:
        chunk_id_expr = "i.chunk_id" if _table_has_column(conn, "d_items", "chunk_id") else "NULL AS chunk_id"
        row = conn.execute(
            """
            SELECT i.item_id, i.page_code, i.anchor_id, {chunk_id_expr}, i.title, i.item_url
            FROM d_items i
            WHERE i.page_code = ? AND COALESCE(i.anchor_id, '') = COALESCE(?, '')
            LIMIT 1
            """.format(chunk_id_expr=chunk_id_expr),
            (page_code, anchor_id),
        ).fetchone()
    return dict(row) if row else None


def get_chunk_id_by_source(source: str, db: str = "default") -> Optional[int]:
    with db_connection(db) as conn:
        row = conn.execute(
            "SELECT id FROM chunks WHERE source = ? LIMIT 1",
            (source,),
        ).fetchone()
    return int(row["id"]) if row else None


def get_reports(db: str = "default", limit: int = 24) -> list[dict]:
    with db_connection(db) as conn:
        rows = conn.execute(
            """
            SELECT report_key, title, report_group, period_text, orders_text, report_url, sort_order
            FROM d_reports
            ORDER BY sort_order, title
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_news(db: str = "default", limit: int = 12) -> list[dict]:
    with db_connection(db) as conn:
        rows = conn.execute(
            """
            SELECT news_id, title, released_on_text, published_at, news_url
            FROM d_news
            ORDER BY COALESCE(published_at, ''), COALESCE(released_on_text, '') DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def build_explore_payload(db_id: str, *, databases: list[dict], db_info: dict) -> dict:
    return {
        "db_id":       db_id,
        "categories":  get_categories(db=db_id),
        "countries":   get_countries(db=db_id),
        "reports":     get_reports(db=db_id),
        "news":        get_news(db=db_id),
        "databases":   databases,
        "db_info":     db_info,
        "errors":      [],
        "timings":     [],
    }


def build_category_payload(db_id: str, category_id: str, *, databases: list[dict], db_info: dict) -> dict:
    return {
        "db_id":       db_id,
        "category_id": category_id,
        "category":    get_category(db=db_id, category_id=category_id) or {},
        "groups":      get_category_groups(category_id=category_id, db=db_id),
        "items":       get_category_items(category_id=category_id, db=db_id),
        "databases":   databases,
        "db_info":     db_info,
        "errors":      [],
        "timings":     [],
    }


def resolve_item_chunk_id(db_id: str, page_code: str, anchor_id: str) -> Optional[int]:
    item = get_item_by_page_anchor(page_code=page_code, anchor_id=anchor_id, db=db_id)
    if item is None:
        return None
    chunk_id = item.get("chunk_id")
    if chunk_id:
        return int(chunk_id)
    item_url = item.get("item_url")
    if not item_url:
        return None
    return get_chunk_id_by_source(str(item_url), db=db_id)
