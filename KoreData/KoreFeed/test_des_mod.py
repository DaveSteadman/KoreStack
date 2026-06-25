# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Test coverage for des mod.
# Exercises the expected behaviour and regression boundaries for this area.
# ====================================================================================================

import sys
sys.path.insert(0, r'C:\Util\GithubRepos\KoreStack\KoreData\KoreFeed')
sys.path.insert(0, r'C:\Util\GithubRepos\KoreStack\KoreData')
sys.path.insert(0, r'C:\Util\GithubRepos\KoreStack\KoreData\CommonCode')

from app.ingest import _try_json_listing, _parse_date_flexible, ingest_web_feed
from app.database import get_entries

# Test auto-detection
print("=== _try_json_listing test ===")
api_url, items = _try_json_listing("https://des.mod.uk/news/?page=1&pageSize=25")
print(f"api_url = {api_url!r}")
print(f"items count = {len(items)}")
if items:
    url, title, date_str = items[0]
    print(f"First item: date={date_str!r} -> parsed={_parse_date_flexible(date_str)!r}")
    print(f"  title={title[:60]!r}")
    print(f"  url={url!r}")

# Read back whatever is in the DE&S domain after any previous test runs
# (we know cfg ignores env override, so check what's in the actual DB)
print("\n=== Current DB entries for des_mod domain ===")
# find the domain used by ingest_web_feed for des.mod.uk
import sqlite3, glob, os
data_dir = r'C:\Util\GithubRepos\KoreStack\datacontrol\koredata\Feeds'
dbs = glob.glob(os.path.join(data_dir, '*.db'))
print(f"DB files: {[os.path.basename(d) for d in dbs]}")
for db_path in dbs:
    if 'des' in db_path.lower() or 'mod' in db_path.lower():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT headline, published FROM entries ORDER BY published DESC LIMIT 5").fetchall()
        print(f"\n{os.path.basename(db_path)}: {len(rows)} recent entries")
        for r in rows:
            print(f"  headline={r['headline'][:60]!r}  published={r['published']!r}")
        conn.close()
