# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Test coverage for ingest json.
# Exercises the expected behaviour and regression boundaries for this area.
# ====================================================================================================

import os, sys, tempfile

tmp_dir = tempfile.mkdtemp()
print(f"TEMP DIR: {tmp_dir}")
os.environ['KORE_DATA_DIR'] = tmp_dir

sys.path.insert(0, r'C:\Util\GithubRepos\KoreStack\KoreData\KoreFeed')
sys.path.insert(0, r'C:\Util\GithubRepos\KoreStack\KoreData')
sys.path.insert(0, r'C:\Util\GithubRepos\KoreStack\KoreData\CommonCode')

# Verify config is using our temp dir
from app.config import cfg
print(f"cfg data_dir = {cfg['data_dir']!r}")

import html as _html
import httpx
from app.ingest import _parse_json_items, _parse_date_flexible, ingest_json_listing_feed
from app.database import init_db, get_entries

# Test 1: verify _parse_json_items extracts dates correctly
print("\n=== Test _parse_json_items ===")
r = httpx.get("https://des.mod.uk/api/news?page=1&pageSize=3", timeout=15,
              headers={"User-Agent": "test", "Accept": "application/json"})
data = r.json()
items = _parse_json_items(data, "https://des.mod.uk/api/news?page=1&pageSize=3")
print(f"Items: {len(items)}")
for url, title, date_str in items[:3]:
    print(f"  date_str={date_str!r}  title={title[:40]!r}")
    print(f"  -> _parse_date_flexible: {_parse_date_flexible(date_str)!r}")

# Test 2: run ingest_json_listing_feed directly
print("\n=== Test ingest_json_listing_feed ===")
init_db("des_direct")
feed = {
    'id': 'test-json-001',
    'domain': 'des_direct',
    'name': 'DE&S Direct',
    'url': 'https://des.mod.uk/api/news?page=1&pageSize=5',
    'type': 'json_listing',
    'update_rate': 60,
}
ingest_json_listing_feed(feed)
entries = get_entries("des_direct", limit=10)
print(f"Entries: {len(entries)}")
for e in entries[:5]:
    print(f"  headline={e.get('headline','')[:60]!r}")
    print(f"  published={e.get('published','')!r}")
