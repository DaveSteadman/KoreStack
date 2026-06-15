import argparse, csv, sqlite3
from pathlib import Path

parser = argparse.ArgumentParser(description="Import KoreGraph triples from export.csv")
parser.add_argument("--csv", default="export.csv")
parser.add_argument("--db", default="graph.db")
parser.add_argument("--nodes-only", action="store_true")
args = parser.parse_args()

base = Path(__file__).resolve().parent
graph_root = base.parent if base.name.lower() == "processing" else base
csv_path = Path(args.csv) if Path(args.csv).is_absolute() else base / args.csv
db_path = Path(args.db) if Path(args.db).is_absolute() else graph_root / args.db

with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
    rows = [r for r in csv.DictReader(f) if (r.get("start") or "").strip() and (r.get("connection") or "").strip() and (r.get("end") or "").strip()]

conn = sqlite3.connect(db_path)
conn.executescript("""
CREATE TABLE IF NOT EXISTS vocab (id INTEGER PRIMARY KEY AUTOINCREMENT, concept_id INTEGER NOT NULL, term TEXT NOT NULL UNIQUE);
CREATE INDEX IF NOT EXISTS idx_vocab_concept ON vocab(concept_id);
CREATE TABLE IF NOT EXISTS relations (
  subject_concept_id INTEGER NOT NULL,
  predicate_concept_id INTEGER NOT NULL,
  object_concept_id INTEGER NOT NULL,
  state INTEGER NOT NULL DEFAULT 0,
  score INTEGER NOT NULL DEFAULT 0,
  UNIQUE (subject_concept_id, predicate_concept_id, object_concept_id)
);
""")

def cid(term: str) -> int:
    row = conn.execute("SELECT concept_id FROM vocab WHERE term=?", (term,)).fetchone()
    if row: return row[0]
    concept_id = conn.execute("SELECT COALESCE(MAX(concept_id), 0) + 1 FROM vocab").fetchone()[0]
    conn.execute("INSERT INTO vocab (concept_id, term) VALUES (?, ?)", (concept_id, term))
    return concept_id

inserted = updated = 0
for row in rows:
    s, p, o = cid(row["start"].strip()), cid(row["connection"].strip()), cid(row["end"].strip())
    if args.nodes_only: continue
    state = max(0, min(3, int((row.get("state") or "0").strip() or "0")))
    raw_score = (row.get("strength") or "0").strip() or "0"
    score = max(0, min(255, round(float(raw_score) * 100) if "." in raw_score else int(raw_score)))
    existed = conn.execute("SELECT 1 FROM relations WHERE subject_concept_id=? AND predicate_concept_id=? AND object_concept_id=?", (s, p, o)).fetchone()
    conn.execute("INSERT INTO relations (subject_concept_id, predicate_concept_id, object_concept_id, state, score) VALUES (?, ?, ?, ?, ?) ON CONFLICT(subject_concept_id, predicate_concept_id, object_concept_id) DO UPDATE SET state=excluded.state, score=excluded.score", (s, p, o, state, score))
    inserted += 0 if existed else 1
    updated += 1 if existed else 0

conn.commit()
print(f"rows={len(rows)} vocab={conn.execute('SELECT COUNT(*) FROM vocab').fetchone()[0]} inserted={inserted} updated={updated}")
conn.close()
