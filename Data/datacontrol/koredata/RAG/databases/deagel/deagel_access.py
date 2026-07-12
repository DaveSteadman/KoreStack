# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Deagel access helpers for datacontrol/koredata/RAG/databases/deagel.
# Provides schema init, parsing, upserts, chunk writes, and descriptor writing.
# ====================================================================================================
import hashlib
import json
import re
import sqlite3
import time
import zlib
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from bs4 import Tag

_DEAGEL_BASE = "https://www.deagel.com"
_HEADERS     = {
    "User-Agent": "KoreStack/1.0 (deagel ingest; internal use)",
    "Accept":     "text/html,application/xhtml+xml",
}

_CATEGORY_PATHS: list[tuple[str, str]] = [
    ("aerospace_forces", "/Aerospace%20Forces"),
    ("armies",           "/Armies"),
    ("navies",           "/Navies"),
    ("weapons",          "/Weapons"),
    ("components",       "/Components"),
    ("civil_aviation",   "/Civil%20Aviation"),
]

_COUNTRY_INDEX_PATH = "/Country"
_REPORTS_PATH       = "/Reports"

_RETRY_STATUSES = {429, 503}
_MAX_RETRIES    = 3

_ITEM_SECTION_RE = re.compile(
    r'<a id="(?P<anchor>\d+)"></a>.*?<h1[^>]*>(?P<title>.*?)</h1>(?P<body>.*?)(?=(?:<a id="\d+"></a>)|(?:<h4 class="pt-3">Photo Gallery)|(?:<h2[^>]*>Notes)|(?:</body>))',
    flags = re.IGNORECASE | re.DOTALL,
)

_CHUNK_COLS = ("id", "title", "source", "tags", "content", "word_count")


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _compress(text: str) -> bytes:
    return zlib.compress(text.encode("utf-8"), level=6)


def _decompress(blob: bytes | None) -> str:
    if not blob:
        return ""
    try:
        return zlib.decompress(blob).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _word_count(text: str) -> int:
    return len((text or "").split())


def _clean_text(value: str | None) -> str:
    text = str(value or "")
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_multiline_text(value: str | None) -> str:
    text  = str(value or "")
    lines = [_clean_text(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _slugify(value: str) -> str:
    text = _clean_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _absolute_url(href: str) -> str:
    return urljoin(_DEAGEL_BASE + "/", href)


def _page_code_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    tail = path.split("/")[-1] if path else ""
    return tail or _slugify(url)


def _hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def _get(url: str) -> str:
    delay = 3.0
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = requests.get(url, headers=_HEADERS, timeout=30)
            if response.status_code == 200:
                return response.text
            if response.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                print(f"    HTTP {response.status_code} - retrying in {delay:.0f}s: {url}")
                time.sleep(delay)
                delay *= 2
                continue
            raise RuntimeError(f"HTTP {response.status_code} for {url}")
        except Exception as exc:
            if attempt < _MAX_RETRIES:
                print(f"    Error ({exc}) - retrying in {delay:.0f}s: {url}")
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError(f"Failed to fetch {url}")


def _sleep(secs: float = 0.25) -> None:
    time.sleep(secs)


def get_conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS _meta (
            key     TEXT PRIMARY KEY,
            value   TEXT
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT,
            source      TEXT,
            tags        TEXT,
            content     BLOB,
            word_count  INTEGER,
            created_at  TEXT DEFAULT (datetime('now','utc'))
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            title, source, tags, content,
            tokenize='unicode61 remove_diacritics 1',
            content=''
        );

        CREATE TABLE IF NOT EXISTS d_categories (
            category_id       TEXT PRIMARY KEY,
            title             TEXT NOT NULL,
            category_url      TEXT NOT NULL,
            item_count_text   TEXT,
            updated_at        TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS d_category_items (
            category_id       TEXT NOT NULL,
            group_name        TEXT NOT NULL,
            item_title        TEXT NOT NULL,
            item_url          TEXT NOT NULL,
            item_anchor       TEXT,
            sort_order        INTEGER NOT NULL,
            PRIMARY KEY (category_id, item_url)
        );
        CREATE INDEX IF NOT EXISTS idx_d_category_items_url ON d_category_items(item_url);

        CREATE TABLE IF NOT EXISTS d_items (
            item_id                               TEXT PRIMARY KEY,
            page_url                              TEXT NOT NULL,
            page_code                             TEXT NOT NULL,
            anchor_id                             TEXT,
            chunk_id                              INTEGER,
            title                                 TEXT NOT NULL,
            category_id                           TEXT,
            group_name                            TEXT,
            item_url                              TEXT NOT NULL UNIQUE,
            status                                TEXT,
            origin_country                        TEXT,
            contractor                            TEXT,
            initial_operational_capability_text   TEXT,
            first_flight_text                     TEXT,
            total_production_text                 TEXT,
            summary                               TEXT,
            source_hash                           TEXT,
            updated_at                            TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_d_items_category ON d_items(category_id);

        CREATE TABLE IF NOT EXISTS d_item_facts (
            item_id          TEXT NOT NULL,
            section_name     TEXT NOT NULL,
            fact_key         TEXT NOT NULL,
            fact_value       TEXT,
            notes            TEXT,
            sort_order       INTEGER NOT NULL,
            PRIMARY KEY (item_id, section_name, fact_key, sort_order)
        );

        CREATE TABLE IF NOT EXISTS d_item_operators (
            item_id            TEXT NOT NULL,
            country_name       TEXT NOT NULL,
            operator_status    TEXT,
            state_text         TEXT,
            notes              TEXT,
            sort_order         INTEGER NOT NULL,
            PRIMARY KEY (item_id, country_name, sort_order)
        );

        CREATE TABLE IF NOT EXISTS d_countries (
            country_id         TEXT PRIMARY KEY,
            title              TEXT NOT NULL,
            country_url        TEXT NOT NULL UNIQUE,
            year_text          TEXT,
            ranking_order      INTEGER,
            ranking_wealth     TEXT,
            ranking_strength   TEXT,
            ranking_population TEXT,
            source_hash        TEXT,
            updated_at         TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS d_country_metrics (
            country_id         TEXT NOT NULL,
            metric_name        TEXT NOT NULL,
            country_value      TEXT,
            world_value        TEXT,
            sort_order         INTEGER NOT NULL,
            PRIMARY KEY (country_id, metric_name)
        );

        CREATE TABLE IF NOT EXISTS d_country_inventory_groups (
            country_id         TEXT NOT NULL,
            branch_code        TEXT NOT NULL,
            branch_label       TEXT NOT NULL,
            group_name         TEXT NOT NULL,
            available_text     TEXT,
            ordered_text       TEXT,
            sort_order         INTEGER NOT NULL,
            PRIMARY KEY (country_id, branch_code, group_name)
        );

        CREATE TABLE IF NOT EXISTS d_country_inventory (
            country_id         TEXT NOT NULL,
            branch_code        TEXT NOT NULL,
            branch_label       TEXT NOT NULL,
            group_name         TEXT,
            item_name          TEXT NOT NULL,
            item_url           TEXT,
            ioc_text           TEXT,
            available_text     TEXT,
            ordered_text       TEXT,
            sort_order         INTEGER NOT NULL,
            PRIMARY KEY (country_id, branch_code, group_name, item_name)
        );

        CREATE TABLE IF NOT EXISTS d_news (
            news_id            TEXT PRIMARY KEY,
            title              TEXT NOT NULL,
            og_title           TEXT,
            released_on_text   TEXT,
            published_at       TEXT,
            news_url           TEXT NOT NULL UNIQUE,
            body               TEXT,
            source_hash        TEXT,
            updated_at         TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS d_reports (
            report_key         TEXT PRIMARY KEY,
            title              TEXT NOT NULL,
            report_group       TEXT,
            period_text        TEXT,
            orders_text        TEXT,
            report_url         TEXT,
            sort_order         INTEGER NOT NULL
        );
        """
    )
    _ensure_column(conn, "d_items", "chunk_id", "INTEGER")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_sql: str) -> None:
    cols = {
        str(row["name"]).lower()
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if column_name.lower() not in cols:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def get_meta(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM _meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


def count_chunks(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])


def _fts_delete(conn: sqlite3.Connection, chunk_id: int, title: str, source: str, tags: str, content: str) -> None:
    conn.execute(
        "INSERT INTO chunks_fts(chunks_fts, rowid, title, source, tags, content) VALUES ('delete', ?, ?, ?, ?, ?)",
        (chunk_id, title, source, tags, content),
    )


def _fts_insert(conn: sqlite3.Connection, chunk_id: int, title: str, source: str, tags: str, content: str) -> None:
    conn.execute(
        "INSERT INTO chunks_fts(rowid, title, source, tags, content) VALUES (?, ?, ?, ?, ?)",
        (chunk_id, title, source, tags, content),
    )


def upsert_chunk(conn: sqlite3.Connection, *, title: str, source: str, tags: str, content: str) -> int:
    existing = conn.execute(
        "SELECT id, title, source, tags, content FROM chunks WHERE source = ? LIMIT 1",
        (source,),
    ).fetchone()
    compressed = _compress(content)
    word_count = _word_count(content)
    if existing is not None:
        _fts_delete(
            conn,
            int(existing["id"]),
            existing["title"]  or "",
            existing["source"] or "",
            existing["tags"]   or "",
            _decompress(existing["content"]),
        )
        chunk_id = int(existing["id"])
        conn.execute(
            "UPDATE chunks SET title = ?, source = ?, tags = ?, content = ?, word_count = ? WHERE id = ?",
            (title, source, tags, compressed, word_count, chunk_id),
        )
        _fts_insert(conn, chunk_id, title or "", source or "", tags or "", content or "")
        return chunk_id

    cur = conn.execute(
        "INSERT INTO chunks (title, source, tags, content, word_count) VALUES (?, ?, ?, ?, ?)",
        (title, source, tags, compressed, word_count),
    )
    chunk_id = int(cur.lastrowid)
    _fts_insert(conn, chunk_id, title or "", source or "", tags or "", content or "")
    return chunk_id


def _parse_info_lines(info_text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in info_text.splitlines():
        line = _clean_text(raw_line)
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[_clean_text(key).lower()] = _clean_text(value)
    return result


def _parse_generic_fact_rows(table: Tag, section_name: str) -> list[dict]:
    facts: list[dict] = []
    order             = 0
    subgroup          = ""
    tbody             = table.find("tbody") or table
    for tr in tbody.find_all("tr", recursive=False):
        cells = [_clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"], recursive=False)]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue

        first_th = tr.find("th", recursive=False)
        if len(cells) == 1 and first_th is not None and first_th.get("colspan"):
            subgroup = cells[0]
            continue

        order += 1
        if len(cells) >= 3:
            facts.append({
                "section_name": section_name if not subgroup else f"{section_name} / {subgroup}",
                "fact_key":     cells[0],
                "fact_value":   cells[1],
                "notes":        cells[2],
                "sort_order":   order,
            })
            continue

        if len(cells) == 2:
            facts.append({
                "section_name": section_name if not subgroup else f"{section_name} / {subgroup}",
                "fact_key":     cells[0],
                "fact_value":   cells[1],
                "notes":        "",
                "sort_order":   order,
            })
            continue

        if subgroup:
            facts.append({
                "section_name": section_name,
                "fact_key":     subgroup,
                "fact_value":   cells[0],
                "notes":        "",
                "sort_order":   order,
            })
    return facts


def parse_category_page(html: str, page_url: str, category_id: str) -> dict:
    soup            = BeautifulSoup(html, "html.parser")
    title           = _clean_text((soup.find("h1") or soup.find("title")).get_text(" ", strip=True) if (soup.find("h1") or soup.find("title")) else category_id)
    item_count_text = ""
    match           = re.search(r"Items\s*:\s*([0-9,]+)", html, flags=re.IGNORECASE)
    if match:
        item_count_text = match.group(1)

    groups: list[dict] = []
    order              = 0
    for guide in soup.select("div.guide"):
        current_group = ""
        for child in guide.children:
            if not isinstance(child, Tag):
                continue
            if child.name == "div":
                current_group = _clean_text(child.get_text(" ", strip=True))
                if current_group:
                    groups.append({"group_name": current_group, "items": []})
                continue
            if child.name != "a" or not current_group:
                continue
            href       = _clean_text(child.get("href"))
            item_title = _clean_text(child.get_text(" ", strip=True))
            if not href or not item_title:
                continue
            order += 1
            item_url    = _absolute_url(href)
            item_anchor = urlparse(item_url).fragment or None
            groups[-1]["items"].append({
                "item_title":  item_title,
                "item_url":    item_url,
                "item_anchor": item_anchor,
                "sort_order":  order,
            })

    return {
        "category_id":     category_id,
        "title":           title,
        "category_url":    page_url,
        "item_count_text": item_count_text,
        "groups":          groups,
    }


def parse_item_page(html: str, page_url: str) -> list[dict]:
    results: list[dict] = []
    page_code           = _page_code_from_url(page_url)
    matches             = list(_ITEM_SECTION_RE.finditer(html))
    if not matches:
        return results

    for match in matches:
        anchor_id    = _clean_text(match.group("anchor"))
        section_title = BeautifulSoup(match.group("title"), "html.parser").get_text(" ", strip=True)
        body_html    = match.group("body")
        fragment     = BeautifulSoup(body_html, "html.parser")
        info_p       = fragment.find("p", class_=lambda value: value and "fst-italic" in value)
        info_lines   = _parse_info_lines(info_p.get_text("\n", strip=True) if info_p else "")

        pre_sections_html = body_html
        if info_p is not None:
            info_html = str(info_p)
            if info_html in pre_sections_html:
                pre_sections_html = pre_sections_html.split(info_html, 1)[1]
        if "<h5" in pre_sections_html:
            pre_sections_html = pre_sections_html.split("<h5", 1)[0]
        summary_text = _clean_multiline_text(BeautifulSoup(pre_sections_html, "html.parser").get_text("\n", strip=True))

        operators: list[dict] = []
        facts:     list[dict] = []
        for heading in fragment.find_all("h5"):
            section_name = _clean_text(heading.get_text(" ", strip=True))
            table        = heading.find_next("table")
            if not section_name or table is None:
                continue

            if section_name.lower() == "operators":
                tbody = table.find("tbody") or table
                order = 0
                for tr in tbody.find_all("tr", recursive=False):
                    cells = [_clean_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["td", "th"], recursive=False)]
                    cells = [cell for cell in cells if cell]
                    if len(cells) < 3 or cells[0].lower() == "country":
                        continue
                    order += 1
                    operators.append({
                        "country_name":    cells[0],
                        "operator_status": cells[1] if len(cells) > 1 else "",
                        "state_text":      cells[2] if len(cells) > 2 else "",
                        "notes":           cells[3] if len(cells) > 3 else "",
                        "sort_order":      order,
                    })
                continue

            facts.extend(_parse_generic_fact_rows(table, section_name))

        results.append({
            "item_id":                              f"{page_code}#{anchor_id}",
            "page_url":                             page_url,
            "page_code":                            page_code,
            "anchor_id":                            anchor_id,
            "title":                                _clean_text(section_title),
            "item_url":                             f"{page_url}#{anchor_id}",
            "status":                               info_lines.get("status", ""),
            "origin_country":                       info_lines.get("origin", ""),
            "contractor":                           info_lines.get("contractor", ""),
            "initial_operational_capability_text":  info_lines.get("initial operational capability (ioc)", ""),
            "first_flight_text":                    info_lines.get("first flight", ""),
            "total_production_text":                info_lines.get("total production", ""),
            "group_name":                           info_lines.get("group", ""),
            "summary":                              summary_text,
            "source_hash":                          _hash_text(body_html),
            "operators":                            operators,
            "facts":                                facts,
        })
    return results


def parse_country_index(html: str, page_url: str) -> list[dict]:
    soup    = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    order   = 0
    for table in soup.find_all("table"):
        tbody = table.find("tbody")
        if tbody is None:
            continue
        for tr in tbody.find_all("tr", recursive=False):
            cells = tr.find_all("td", recursive=False)
            if len(cells) < 4:
                continue
            link = tr.find("a", href=True)
            if link is None:
                continue
            country_name = _clean_text(link.get_text(" ", strip=True))
            country_url  = _absolute_url(link.get("href"))
            if "/Country/" not in country_url and "/country/" not in country_url:
                continue
            order += 1
            results.append({
                "country_id":         _slugify(country_name),
                "title":              country_name,
                "country_url":        country_url,
                "ranking_order":      order,
                "ranking_wealth":     _clean_text(cells[1].get_text(" ", strip=True)),
                "ranking_strength":   _clean_text(cells[2].get_text(" ", strip=True)),
                "ranking_population": _clean_text(cells[3].get_text(" ", strip=True)),
            })
    return results


def parse_country_page(html: str, page_url: str) -> dict:
    heading_text = ""
    year_text    = ""
    title        = ""

    heading_match = re.search(
        r"<h2[^>]*>(?P<body>.*?)</h2>",
        html,
        flags = re.IGNORECASE | re.DOTALL,
    )
    if heading_match:
        heading_text = _clean_text(BeautifulSoup(heading_match.group("body"), "html.parser").get_text(" ", strip=True))
        year_match   = re.search(r"\b(\d{4})\b$", heading_text)
        year_text    = year_match.group(1) if year_match else ""
        title        = heading_text[:-5].strip() if year_text and heading_text.endswith(year_text) else heading_text

    country_id   = _slugify(title or page_url)

    metrics: list[dict] = []
    metrics_match = re.search(
        r"World Figures</th>.*?<tbody>(?P<body>.*?)</tbody>",
        html,
        flags = re.IGNORECASE | re.DOTALL,
    )
    if metrics_match:
        order = 0
        for tr_html in re.findall(r"<tr[^>]*>(.*?)</tr>", metrics_match.group("body"), flags=re.IGNORECASE | re.DOTALL):
            tr_soup = BeautifulSoup(f"<tr>{tr_html}</tr>", "html.parser")
            cells   = tr_soup.find_all("td")
            if len(cells) < 3:
                continue
            order += 1
            metrics.append({
                "metric_name":   _clean_text(cells[0].get_text(" ", strip=True)),
                "country_value": _clean_text(cells[1].get_text(" ", strip=True)),
                "world_value":   _clean_text(cells[2].get_text(" ", strip=True)),
                "sort_order":    order,
            })
    branch_labels = {
        "air":    "AF",
        "army":   "Army",
        "navy":   "Navy",
        "weapon": "OM",
    }
    inventory_groups: list[dict] = []
    inventory_items:  list[dict] = []
    for branch_code, branch_label in branch_labels.items():
        pane_match = re.search(
            rf'<div class="tab-pane[^"]*" id="{branch_code}">(?P<body>.*?)(?=<div class="tab-pane|\Z)',
            html,
            flags = re.IGNORECASE | re.DOTALL,
        )
        if not pane_match:
            continue
        current_group = ""
        sort_order    = 0
        for class_name, row_html in re.findall(
            r'<tr(?: class="([^"]+)")?>(.*?)</tr>',
            pane_match.group("body"),
            flags = re.IGNORECASE | re.DOTALL,
        ):
            row_soup = BeautifulSoup(f"<tr>{row_html}</tr>", "html.parser")
            cells    = [_clean_text(cell.get_text(" ", strip=True)) for cell in row_soup.find_all("td")]
            if not cells:
                continue
            classes = set(_clean_text(class_name).split())
            if "sum" in classes:
                group_name = _clean_text(cells[0]) if cells else ""
                if not group_name:
                    continue
                current_group = group_name
                sort_order   += 1
                inventory_groups.append({
                    "branch_code":    branch_code,
                    "branch_label":   branch_label,
                    "group_name":     group_name,
                    "available_text": cells[1] if len(cells) > 1 else "",
                    "ordered_text":   cells[2] if len(cells) > 2 else "",
                    "sort_order":     sort_order,
                })
                continue

            item_link = row_soup.find("a", href=True)
            sort_order += 1
            inventory_items.append({
                "branch_code":    branch_code,
                "branch_label":   branch_label,
                "group_name":     current_group,
                "item_name":      _clean_text(cells[1] if len(cells) > 1 else (item_link.get_text(" ", strip=True) if item_link else "")),
                "item_url":       _absolute_url(item_link.get("href")) if item_link else "",
                "ioc_text":       _clean_text(cells[0] if len(cells) > 0 else ""),
                "available_text": _clean_text(cells[2] if len(cells) > 2 else ""),
                "ordered_text":   _clean_text(cells[3] if len(cells) > 3 else ""),
                "sort_order":     sort_order,
            })

    return {
        "country_id":        country_id,
        "title":             title,
        "country_url":       page_url,
        "year_text":         year_text,
        "metrics":           metrics,
        "inventory_groups":  inventory_groups,
        "inventory_items":   inventory_items,
        "source_hash":       _hash_text(html),
    }


def parse_reports_page(html: str, page_url: str) -> list[dict]:
    soup    = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    order   = 0
    for table in soup.find_all("table"):
        tbody = table.find("tbody")
        if tbody is None:
            continue
        for tr in tbody.find_all("tr", recursive=False):
            if "separator" in set(tr.get("class", [])):
                continue
            cells = tr.find_all("td", recursive=False)
            if len(cells) < 3:
                continue
            link        = tr.find("a", href=True)
            title       = _clean_text(link.get_text(" ", strip=True) if link else cells[0].get_text(" ", strip=True))
            report_url  = _absolute_url(link.get("href")) if link else page_url
            report_path = urlparse(report_url).path
            if not title:
                continue
            order += 1
            if "/Reports/Country/" in report_path:
                report_group = "country"
            elif "/Reports/Guide/" in report_path:
                report_group = "guide"
            elif "/Reports/" in report_path:
                report_group = "report"
            else:
                report_group = "other"
            results.append({
                "report_key":   _slugify(report_path or f"{title}_{order}"),
                "title":        title,
                "report_group": report_group,
                "period_text":  _clean_text(cells[1].get_text(" ", strip=True)),
                "orders_text":  _clean_text(cells[2].get_text(" ", strip=True)),
                "report_url":   report_url,
                "sort_order":   order,
            })
        if results:
            break
    return results


def discover_home_news_links(html: str) -> list[str]:
    seen: OrderedDict[str, None] = OrderedDict()
    for href in re.findall(r'href="([^"]*news/n\d+)"', html, flags=re.IGNORECASE):
        full = _absolute_url(href)
        seen.setdefault(full, None)
    return list(seen.keys())


def parse_news_article(html: str, page_url: str) -> dict:
    soup          = BeautifulSoup(html, "html.parser")
    news_id       = _page_code_from_url(page_url)
    og_title      = ""
    og_title_tag  = soup.find("meta", attrs={"property": "og:title"})
    if og_title_tag is not None:
        og_title = _clean_text(og_title_tag.get("content"))

    published_at  = ""
    meta_pub      = soup.find("meta", attrs={"property": "news:published_time"})
    if meta_pub is not None:
        published_at = _clean_text(meta_pub.get("content"))

    title            = og_title or news_id
    released_on_text = ""
    header_match     = re.search(
        r'<div class="container px-4 py-2 bg-white shadow">\s*<h5[^>]*>(?P<title>.*?)</h5>\s*<em>(?P<released>.*?)</em>',
        html,
        flags = re.IGNORECASE | re.DOTALL,
    )
    if header_match:
        title            = _clean_text(BeautifulSoup(header_match.group("title"), "html.parser").get_text(" ", strip=True)) or title
        released_on_text = _clean_text(BeautifulSoup(header_match.group("released"), "html.parser").get_text(" ", strip=True))

    paragraphs: list[str] = []
    article_segment       = html
    marker                = '<p class="border-bottom">&nbsp;</p>'
    if marker in html:
        article_segment = html.split(marker, 1)[1]
    for para_html in re.findall(r"<p[^>]*>(.*?)</p>", article_segment, flags=re.IGNORECASE | re.DOTALL):
        text = _clean_text(BeautifulSoup(para_html, "html.parser").get_text(" ", strip=True))
        if not text or text == "\xa0":
            continue
        if text.startswith("Copyright") or text == "Cookies & Privacy":
            continue
        paragraphs.append(text)
    body = "\n\n".join(paragraphs)

    return {
        "news_id":          news_id,
        "title":            title,
        "og_title":         og_title,
        "released_on_text": released_on_text,
        "published_at":     published_at,
        "news_url":         page_url,
        "body":             body,
        "source_hash":      _hash_text(html),
    }


def _build_item_chunk(item: dict) -> str:
    lines = [
        f"Item: {item['title']}",
        f"Category: {item.get('category_id') or ''}",
        f"Group: {item.get('group_name') or ''}",
        f"Status: {item.get('status') or ''}",
        f"Origin: {item.get('origin_country') or ''}",
        f"Contractor: {item.get('contractor') or ''}",
        f"IOC: {item.get('initial_operational_capability_text') or ''}",
        f"First flight: {item.get('first_flight_text') or ''}",
        f"Total production: {item.get('total_production_text') or ''}",
        "",
        item.get("summary") or "",
    ]
    return _clean_multiline_text("\n".join(lines))


def _build_country_chunk(country: dict) -> str:
    lines = [
        f"Country: {country['title']}",
        f"Year: {country.get('year_text') or ''}",
    ]
    for metric in country.get("metrics", []):
        lines.append(f"{metric['metric_name']}: {metric['country_value']} (world: {metric['world_value']})")
    return _clean_multiline_text("\n".join(lines))


def _build_inventory_chunk(country_title: str, branch_label: str, group: dict, items: list[dict]) -> str:
    lines = [
        f"Country: {country_title}",
        f"Branch: {branch_label}",
        f"Group: {group['group_name']}",
        f"Available total: {group.get('available_text') or ''}",
        f"Ordered total: {group.get('ordered_text') or ''}",
        "",
    ]
    for item in items:
        lines.append(
            f"{item['item_name']} | IOC {item.get('ioc_text') or '-'} | available {item.get('available_text') or '-'} | ordered {item.get('ordered_text') or '-'}"
        )
    return _clean_multiline_text("\n".join(lines))


def _build_news_chunk(news: dict) -> str:
    lines = [
        f"Title: {news['title']}",
        f"Released: {news.get('released_on_text') or ''}",
        f"Published at: {news.get('published_at') or ''}",
        "",
        news.get("body") or "",
    ]
    return _clean_multiline_text("\n".join(lines))


def _build_report_chunk(report: dict) -> str:
    return _clean_multiline_text(
        "\n".join([
            f"Report: {report['title']}",
            f"Group: {report.get('report_group') or ''}",
            f"Period: {report.get('period_text') or ''}",
            f"Orders: {report.get('orders_text') or ''}",
        ])
    )


def _tag_string(*parts: Optional[str]) -> str:
    return " ".join([_clean_text(part) for part in parts if _clean_text(part)])


def ingest_categories(conn: sqlite3.Connection, *, category_limit: int | None = None, item_limit: int | None = None) -> dict:
    print("\n  Phase 1: categories and item pages")
    categories_processed = 0
    items_processed      = 0
    item_pages_failed    = 0

    selected_categories = _CATEGORY_PATHS[:category_limit] if category_limit else _CATEGORY_PATHS
    for category_id, path in selected_categories:
        category_url = _absolute_url(path)
        print(f"    Category: {category_id} -> {category_url}")
        html   = _get(category_url)
        parsed = parse_category_page(html, category_url, category_id)
        conn.execute(
            """
            INSERT INTO d_categories (category_id, title, category_url, item_count_text, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(category_id) DO UPDATE SET
                title           = excluded.title,
                category_url    = excluded.category_url,
                item_count_text = excluded.item_count_text,
                updated_at      = excluded.updated_at
            """,
            (
                parsed["category_id"],
                parsed["title"],
                parsed["category_url"],
                parsed["item_count_text"],
                _now(),
            ),
        )
        conn.execute("DELETE FROM d_category_items WHERE category_id = ?", (category_id,))

        page_lookup: OrderedDict[str, dict[str, dict]] = OrderedDict()
        for group in parsed["groups"]:
            for item in group["items"]:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO d_category_items (
                        category_id, group_name, item_title, item_url, item_anchor, sort_order
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        category_id,
                        group["group_name"],
                        item["item_title"],
                        item["item_url"],
                        item["item_anchor"],
                        item["sort_order"],
                    ),
                )
                page_url = item["item_url"].split("#", 1)[0]
                page_lookup.setdefault(page_url, {})
                page_lookup[page_url][item["item_url"]] = {
                    "category_id": category_id,
                    "group_name":  group["group_name"],
                }

        categories_processed += 1
        conn.commit()

        for page_url, listing_map in page_lookup.items():
            if item_limit and items_processed >= item_limit:
                break
            print(f"      Item page: {page_url}")
            try:
                page_html = _get(page_url)
            except Exception as exc:
                print(f"      Skipping item page after fetch failure: {page_url} ({exc})")
                item_pages_failed += 1
                conn.commit()
                _sleep()
                continue
            sections  = parse_item_page(page_html, page_url)
            for section in sections:
                listing = listing_map.get(section["item_url"]) or next(iter(listing_map.values()))
                section["category_id"] = listing.get("category_id") or section.get("category_id") or category_id
                if not section.get("group_name"):
                    section["group_name"] = listing.get("group_name") or ""

                chunk_id   = None
                chunk_text = _build_item_chunk(section)
                if chunk_text:
                    chunk_id = upsert_chunk(
                        conn,
                        title   = section["title"],
                        source  = section["item_url"],
                        tags    = _tag_string(
                            "source:deagel",
                            "type:item",
                            f"category:{section.get('category_id')}",
                            f"group:{_slugify(section.get('group_name') or '')}",
                            f"origin:{_slugify(section.get('origin_country') or '')}",
                            f"status:{_slugify(section.get('status') or '')}",
                        ),
                        content = chunk_text,
                    )

                conn.execute(
                    """
                    INSERT INTO d_items (
                        item_id, page_url, page_code, anchor_id, chunk_id, title, category_id, group_name, item_url,
                        status, origin_country, contractor, initial_operational_capability_text,
                        first_flight_text, total_production_text, summary, source_hash, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(item_id) DO UPDATE SET
                        page_url                             = excluded.page_url,
                        page_code                            = excluded.page_code,
                        anchor_id                            = excluded.anchor_id,
                        chunk_id                             = excluded.chunk_id,
                        title                                = excluded.title,
                        category_id                          = excluded.category_id,
                        group_name                           = excluded.group_name,
                        item_url                             = excluded.item_url,
                        status                               = excluded.status,
                        origin_country                       = excluded.origin_country,
                        contractor                           = excluded.contractor,
                        initial_operational_capability_text  = excluded.initial_operational_capability_text,
                        first_flight_text                    = excluded.first_flight_text,
                        total_production_text                = excluded.total_production_text,
                        summary                              = excluded.summary,
                        source_hash                          = excluded.source_hash,
                        updated_at                           = excluded.updated_at
                    """,
                    (
                        section["item_id"],
                        section["page_url"],
                        section["page_code"],
                        section["anchor_id"],
                        chunk_id,
                        section["title"],
                        section.get("category_id"),
                        section.get("group_name"),
                        section["item_url"],
                        section.get("status"),
                        section.get("origin_country"),
                        section.get("contractor"),
                        section.get("initial_operational_capability_text"),
                        section.get("first_flight_text"),
                        section.get("total_production_text"),
                        section.get("summary"),
                        section.get("source_hash"),
                        _now(),
                    ),
                )
                conn.execute("DELETE FROM d_item_facts WHERE item_id = ?", (section["item_id"],))
                conn.execute("DELETE FROM d_item_operators WHERE item_id = ?", (section["item_id"],))

                for fact in section.get("facts", []):
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO d_item_facts (item_id, section_name, fact_key, fact_value, notes, sort_order)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            section["item_id"],
                            fact["section_name"],
                            fact["fact_key"],
                            fact.get("fact_value"),
                            fact.get("notes"),
                            fact["sort_order"],
                        ),
                    )

                for operator in section.get("operators", []):
                    conn.execute(
                        """
                        INSERT INTO d_item_operators (item_id, country_name, operator_status, state_text, notes, sort_order)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            section["item_id"],
                            operator["country_name"],
                            operator.get("operator_status"),
                            operator.get("state_text"),
                            operator.get("notes"),
                            operator["sort_order"],
                        ),
                    )
            items_processed += 1
            conn.commit()
            _sleep()
        if item_limit and items_processed >= item_limit:
            break

    set_meta(conn, "last_categories_ingest_at", _now())
    return {
        "categories_processed": categories_processed,
        "items_processed":      items_processed,
        "item_pages_failed":    item_pages_failed,
    }


def ingest_countries(conn: sqlite3.Connection, *, country_limit: int | None = None) -> dict:
    print("\n  Phase 2: countries")
    index_url = _absolute_url(_COUNTRY_INDEX_PATH)
    html      = _get(index_url)
    countries = parse_country_index(html, index_url)
    if country_limit:
        countries = countries[:country_limit]

    countries_processed = 0
    for country in countries:
        print(f"    Country: {country['title']}")
        country_html   = _get(country["country_url"])
        country_detail = parse_country_page(country_html, country["country_url"])
        country_detail["ranking_order"]      = country.get("ranking_order")
        country_detail["ranking_wealth"]     = country.get("ranking_wealth")
        country_detail["ranking_strength"]   = country.get("ranking_strength")
        country_detail["ranking_population"] = country.get("ranking_population")

        conn.execute(
            """
            INSERT INTO d_countries (
                country_id, title, country_url, year_text, ranking_order, ranking_wealth,
                ranking_strength, ranking_population, source_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(country_id) DO UPDATE SET
                title              = excluded.title,
                country_url        = excluded.country_url,
                year_text          = excluded.year_text,
                ranking_order      = excluded.ranking_order,
                ranking_wealth     = excluded.ranking_wealth,
                ranking_strength   = excluded.ranking_strength,
                ranking_population = excluded.ranking_population,
                source_hash        = excluded.source_hash,
                updated_at         = excluded.updated_at
            """,
            (
                country_detail["country_id"],
                country_detail["title"],
                country_detail["country_url"],
                country_detail["year_text"],
                country_detail.get("ranking_order"),
                country_detail.get("ranking_wealth"),
                country_detail.get("ranking_strength"),
                country_detail.get("ranking_population"),
                country_detail.get("source_hash"),
                _now(),
            ),
        )

        conn.execute("DELETE FROM d_country_metrics WHERE country_id = ?", (country_detail["country_id"],))
        conn.execute("DELETE FROM d_country_inventory_groups WHERE country_id = ?", (country_detail["country_id"],))
        conn.execute("DELETE FROM d_country_inventory WHERE country_id = ?", (country_detail["country_id"],))

        for metric in country_detail.get("metrics", []):
            conn.execute(
                """
                INSERT INTO d_country_metrics (country_id, metric_name, country_value, world_value, sort_order)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    country_detail["country_id"],
                    metric["metric_name"],
                    metric.get("country_value"),
                    metric.get("world_value"),
                    metric["sort_order"],
                ),
            )

        for group in country_detail.get("inventory_groups", []):
            conn.execute(
                """
                INSERT INTO d_country_inventory_groups (
                    country_id, branch_code, branch_label, group_name, available_text, ordered_text, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    country_detail["country_id"],
                    group["branch_code"],
                    group["branch_label"],
                    group["group_name"],
                    group.get("available_text"),
                    group.get("ordered_text"),
                    group["sort_order"],
                ),
            )

        for item in country_detail.get("inventory_items", []):
            if not item.get("item_name"):
                continue
            conn.execute(
                """
                INSERT INTO d_country_inventory (
                    country_id, branch_code, branch_label, group_name, item_name, item_url,
                    ioc_text, available_text, ordered_text, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    country_detail["country_id"],
                    item["branch_code"],
                    item["branch_label"],
                    item.get("group_name"),
                    item["item_name"],
                    item.get("item_url"),
                    item.get("ioc_text"),
                    item.get("available_text"),
                    item.get("ordered_text"),
                    item["sort_order"],
                ),
            )

        chunk_text = _build_country_chunk(country_detail)
        if chunk_text:
            upsert_chunk(
                conn,
                title   = country_detail["title"],
                source  = country_detail["country_url"],
                tags    = _tag_string(
                    "source:deagel",
                    "type:country",
                    f"country:{country_detail['country_id']}",
                ),
                content = chunk_text,
            )

        inventory_groups = country_detail.get("inventory_groups", [])
        inventory_items  = country_detail.get("inventory_items", [])
        for group in inventory_groups:
            grouped_items = [
                item for item in inventory_items
                if item.get("branch_code") == group["branch_code"] and item.get("group_name") == group["group_name"]
            ]
            inventory_chunk = _build_inventory_chunk(
                country_detail["title"],
                group["branch_label"],
                group,
                grouped_items,
            )
            if not inventory_chunk:
                continue
            upsert_chunk(
                conn,
                title   = f"{country_detail['title']} - {group['branch_label']} - {group['group_name']}",
                source  = f"{country_detail['country_url']}#{group['branch_code']}:{_slugify(group['group_name'])}",
                tags    = _tag_string(
                    "source:deagel",
                    "type:inventory",
                    f"country:{country_detail['country_id']}",
                    f"branch:{group['branch_code']}",
                    f"group:{_slugify(group['group_name'])}",
                ),
                content = inventory_chunk,
            )

        countries_processed += 1
        conn.commit()
        _sleep()

    set_meta(conn, "last_countries_ingest_at", _now())
    return {"countries_processed": countries_processed}


def ingest_reports(conn: sqlite3.Connection) -> int:
    print("\n  Phase 3: reports")
    reports_url = _absolute_url(_REPORTS_PATH)
    reports     = parse_reports_page(_get(reports_url), reports_url)
    conn.execute("DELETE FROM d_reports")
    for report in reports:
        conn.execute(
            """
            INSERT INTO d_reports (report_key, title, report_group, period_text, orders_text, report_url, sort_order)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report["report_key"],
                report["title"],
                report.get("report_group"),
                report.get("period_text"),
                report.get("orders_text"),
                report.get("report_url"),
                report["sort_order"],
            ),
        )
        chunk_text = _build_report_chunk(report)
        if chunk_text:
            upsert_chunk(
                conn,
                title   = report["title"],
                source  = report["report_url"],
                tags    = _tag_string(
                    "source:deagel",
                    "type:report",
                    f"report_group:{report.get('report_group')}",
                ),
                content = chunk_text,
            )
    conn.commit()
    set_meta(conn, "last_reports_ingest_at", _now())
    print(f"    Reports: {len(reports)}")
    return len(reports)


def ingest_latest_news(conn: sqlite3.Connection, *, news_limit: int = 12) -> int:
    print("\n  Phase 4: latest news")
    home_html  = _get(_DEAGEL_BASE + "/")
    news_links = discover_home_news_links(home_html)[:max(int(news_limit or 0), 0)]
    count      = 0
    for news_url in news_links:
        print(f"    News: {news_url}")
        article = parse_news_article(_get(news_url), news_url)
        conn.execute(
            """
            INSERT INTO d_news (
                news_id, title, og_title, released_on_text, published_at, news_url, body, source_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(news_id) DO UPDATE SET
                title            = excluded.title,
                og_title         = excluded.og_title,
                released_on_text = excluded.released_on_text,
                published_at     = excluded.published_at,
                news_url         = excluded.news_url,
                body             = excluded.body,
                source_hash      = excluded.source_hash,
                updated_at       = excluded.updated_at
            """,
            (
                article["news_id"],
                article["title"],
                article.get("og_title"),
                article.get("released_on_text"),
                article.get("published_at"),
                article["news_url"],
                article.get("body"),
                article.get("source_hash"),
                _now(),
            ),
        )
        chunk_text = _build_news_chunk(article)
        if chunk_text:
            upsert_chunk(
                conn,
                title   = article["title"],
                source  = article["news_url"],
                tags    = _tag_string(
                    "source:deagel",
                    "type:news",
                    f"news_id:{article['news_id']}",
                ),
                content = chunk_text,
            )
        count += 1
        conn.commit()
        _sleep()
    set_meta(conn, "last_news_ingest_at", _now())
    return count


def write_descriptor(
    json_path: Path,
    db_id: str,
    *,
    total_chunks: int,
    status: str,
    extra_sync: Optional[dict] = None,
) -> None:
    try:
        descriptor = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        descriptor = {}

    descriptor.update({
        "id":           db_id,
        "display_name": descriptor.get("display_name") or "Deagel - Military Equipment and Aviation",
        "description":  descriptor.get("description")  or "<News Has Low Credibility> Structured ingest of deagel.com categories, equipment items, country inventories, reports, and latest news.",
        "source_url":   descriptor.get("source_url")   or "https://www.deagel.com/",
        "licence":      descriptor.get("licence")      or "Deagel website copyright; internal structured ingest",
        "managed_by":   "ingestor",
        "ingestor":     db_id,
        "schedule":     descriptor.get("schedule") or "manual",
        "navigation":   descriptor.get("navigation") or {
            "type": "deagel",
            "tables": ["d_categories", "d_category_items", "d_items", "d_countries", "d_news", "d_reports"],
        },
    })

    descriptor["sync"] = {
        **(descriptor.get("sync") or {}),
        "last_run":                  _now(),
        "status":                    status,
        "total_chunks":              int(total_chunks),
        "last_ingest_completed_at":  _now() if status == "complete" else (descriptor.get("sync") or {}).get("last_ingest_completed_at"),
    }
    if extra_sync:
        descriptor["sync"].update(extra_sync)

    json_path.write_text(
        json.dumps(descriptor, indent=2, ensure_ascii=False) + "\n",
        encoding = "utf-8",
    )


def write_failed_descriptor(json_path: Path, db_id: str, exc: Exception) -> None:
    try:
        descriptor = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        descriptor = {}

    sync = descriptor.get("sync") or {}
    sync = {
        **sync,
        "last_run":    _now(),
        "status":      "failed",
        "last_error":  _clean_text(str(exc)),
    }
    descriptor["sync"] = sync

    json_path.write_text(
        json.dumps(descriptor, indent=2, ensure_ascii=False) + "\n",
        encoding = "utf-8",
    )
