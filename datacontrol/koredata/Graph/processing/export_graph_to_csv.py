import argparse
import csv
import sqlite3
from pathlib import Path


parser = argparse.ArgumentParser(description="Export KoreGraph triples to export.csv")
parser.add_argument("--csv",   default="export.csv")
parser.add_argument("--db",    default="graph.db")
parser.add_argument("--state", type=int, default=None)
args = parser.parse_args()

base       = Path(__file__).resolve().parent
graph_root = base.parent if base.name.lower() == "processing" else base
csv_path   = Path(args.csv) if Path(args.csv).is_absolute() else base / args.csv
db_path    = Path(args.db)  if Path(args.db).is_absolute()  else graph_root / args.db

csv_path.parent.mkdir(parents=True, exist_ok=True)

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

sql = """
SELECT vs.term AS start,
       vp.term AS connection,
       vo.term AS end,
       r.score,
       r.state
FROM relations r
JOIN vocab vs
  ON vs.id = (
      SELECT MIN(id)
      FROM vocab
      WHERE concept_id = r.subject_concept_id
  )
JOIN vocab vp
  ON vp.id = (
      SELECT MIN(id)
      FROM vocab
      WHERE concept_id = r.predicate_concept_id
  )
JOIN vocab vo
  ON vo.id = (
      SELECT MIN(id)
      FROM vocab
      WHERE concept_id = r.object_concept_id
  )
"""

params: list[object] = []
if args.state is not None:
    sql += "\nWHERE r.state=?"
    params.append(args.state)

sql += """
ORDER BY vs.term,
         vp.term,
         vo.term
"""

rows = conn.execute(sql, params).fetchall()

with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.writer(f, quoting=csv.QUOTE_ALL)
    writer.writerow(["start", "connection", "end", "strength", "state"])
    for row in rows:
        writer.writerow([
            row["start"],
            row["connection"],
            row["end"],
            f"{row['score'] / 100:.1f}",
            str(row["state"]),
        ])

vocab_count = conn.execute("SELECT COUNT(*) FROM vocab").fetchone()[0]
conn.close()

print(f"rows={len(rows)} vocab={vocab_count} csv={csv_path}")
