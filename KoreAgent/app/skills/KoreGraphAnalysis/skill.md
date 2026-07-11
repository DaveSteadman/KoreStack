# KoreGraphAnalysis Skill

## Purpose
Extract factual knowledge graph connections from library book text and populate KoreGraph.
Use this whenever the task involves reading a book or document from KoreLibrary and generating
structured knowledge graph edges from its content.

## Trigger keywords: book connections, library graph, book to graph, extract connections from book

## Interface
- Module: (none — workflow skill)
- Functions: (none)

---

## PRIMARY METHOD: One-shot Python script (use this for any book with more than 5 chunks)

Books in KoreLibrary typically have 30–60 chunks. Processing them chunk-by-chunk
with individual tool calls would require 60–120 rounds — far more than the round budget.
**Use `run_python_snippet` to process the entire book in a single call.**

### Step 1 — Find the book
Call `koredata_find_library_book(title)`. Note the `book_id`.

### Step 2 — Run the extraction script
Call `run_python_snippet` with this script, substituting `BOOK_ID`:

```python
import sys, subprocess
try:
    import requests
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "requests", "-q"], check=True)
    import requests
import re

import json as _j
import pathlib as _pl

def _load_cfg():
    p = _pl.Path.cwd()
    for _ in range(8):
        if (p / 'config' / 'korestack_config.json').exists():
            break
        p = p.parent
    else:
        return {}
    result = {}
    try:
        d = _j.loads((p / 'config' / 'korestack_config.json').read_text(encoding='utf-8'))
    except Exception:
        d = {}
    for k, v in d.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = {**result[k], **v}
        else:
            result[k] = v
    return result

_cfg        = _load_cfg()
_host       = _cfg.get('network', {}).get('host', '127.0.0.1')
_svcs       = _cfg.get('services', {})
_lib_port   = _svcs.get('korelibrary', {}).get('port', 9605)
_graph_port = _svcs.get('koregraph', {}).get('port', 9608)
LIBRARY     = f'http://{_host}:{_lib_port}'
GRAPH       = f'http://{_host}:{_graph_port}'
print(f'Config: library={LIBRARY}  graph={GRAPH}')
BOOK_ID = "REPLACE_WITH_BOOK_ID"   # <-- substitute actual book_id here
CHUNK   = 16000

# ── extraction helpers ────────────────────────────────────────────────────

_STOP = {
    'man', 'men', 'time', 'times', 'way', 'ways', 'fact', 'thing', 'things',
    'world', 'work', 'works', 'first', 'last', 'great', 'part', 'parts',
    'same', 'such', 'this', 'that', 'these', 'those', 'which', 'what',
    'one', 'two', 'three', 'four', 'five', 'many', 'more', 'most',
    'place', 'places', 'name', 'names', 'view', 'views', 'form', 'forms',
}

def _sent(text):
    return re.split(r'(?<=[.!?])\s+', text)

def _extract(sent):
    s = sent.strip()
    if len(s) < 20 or s.startswith('#'):
        return []
    out = []

    # "Name discovered/invented/proposed/developed/wrote/studied/founded/... [the/a] obj"
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})'
        r'\s+(discovered|invented|proposed|developed|founded|established|'
        r'proved|disproved|wrote|described|calculated|measured|introduced|'
        r'studied|applied|created|derived|formulated|demonstrated|showed)'
        r'\s+(?:(?:the|a|an|his|her|its|that|how)\s+)?'
        r'([A-Za-z][a-z]{2,}(?:\s+(?:of\s+)?[a-z]{2,}){0,3})',
        s
    ):
        subj, verb, obj = m.group(1), m.group(2), m.group(3).strip()
        if obj.split()[0].lower() not in _STOP and len(obj) >= 4:
            out.append({"start": subj, "connection": verb, "end": obj})

    # "Name was [a/an] profession/nationality"
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})'
        r'\s+was\s+(?:a|an)\s+'
        r'(Greek|Roman|Egyptian|Arab|Persian|Babylonian|Chinese|Indian|'
        r'mathematician|philosopher|astronomer|physicist|chemist|biologist|'
        r'physician|geographer|geometer|naturalist|historian|engineer|'
        r'theologian|logician|scholar|scientist)',
        s
    ):
        out.append({"start": m.group(1), "connection": "is_a", "end": m.group(2)})

    # "Name lived/worked/taught in/at Place"
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})'
        r'\s+(?:lived|worked|resided|taught|studied)\s+(?:in|at)\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,1})',
        s
    ):
        out.append({"start": m.group(1), "connection": "lived_in", "end": m.group(2)})

    # "Name influenced/succeeded/preceded/taught Name"
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})'
        r'\s+(influenced|inspired|succeeded|preceded)\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})',
        s
    ):
        out.append({"start": m.group(1), "connection": m.group(2), "end": m.group(3)})

    return out

# ── main loop ─────────────────────────────────────────────────────────────

all_conns = []
offset    = 0
chunk_num = 0

while True:
    resp = requests.get(
        f"{LIBRARY}/books/{BOOK_ID}/chunk",
        params={"offset": offset, "length": CHUNK},
        timeout=30
    )
    if not resp.ok:
        print(f"ERROR fetching chunk {chunk_num}: {resp.status_code}")
        break

    data  = resp.json()
    text  = data.get("chunk", "")
    found = []
    for sent in _sent(text):
        found.extend(_extract(sent))
    all_conns.extend(found)
    print(f"Chunk {chunk_num:3d}  offset={offset:>7d}  +{len(found):3d} conns  total={len(all_conns)}")

    if not data.get("has_more"):
        break
    offset    = data["next_offset"]
    chunk_num += 1

# ── deduplicate ───────────────────────────────────────────────────────────

seen   = set()
unique = []
for c in all_conns:
    key = (c["start"].lower(), c["connection"], c["end"].lower())
    if key not in seen:
        seen.add(key)
        unique.append(c)

print(f"\nUnique connections to submit: {len(unique)}")

# ── submit via batch endpoint ─────────────────────────────────────────────

BATCH_URL  = f"{GRAPH}/api/connections/by-name/batch"
SINGLE_URL = f"{GRAPH}/api/connections/by-name"
BATCH_SIZE = 100
submitted  = 0
errors     = 0

for i in range(0, len(unique), BATCH_SIZE):
    batch = unique[i : i + BATCH_SIZE]
    r = requests.post(BATCH_URL, json=batch, timeout=60)
    if r.ok:
        result     = r.json()
        submitted += result.get("accepted", len(batch))
        errors    += len(result.get("errors", []))
        print(f"Batch {i//BATCH_SIZE+1:3d}: submitted={submitted}  errors={errors}")
    else:
        # fallback: individual calls
        for c in batch:
            r2 = requests.post(SINGLE_URL, json=c, timeout=10)
            if r2.ok:
                submitted += 1
            else:
                errors += 1
        print(f"Batch {i//BATCH_SIZE+1:3d} used fallback: submitted={submitted}  errors={errors}")

print(f"\nDone. Total submitted={submitted}  errors={errors}")
```

**This script:**
- Fetches every chunk from the KoreLibrary HTTP API directly (no MCP round per chunk)
- Extracts connections using four regex patterns calibrated for historical/scientific prose
- Submits all results to the KoreGraph batch endpoint in one series of HTTP calls
- Prints progress at every step so you can see output

---

## SECONDARY METHOD: Manual round-by-round (only for books with ≤ 5 chunks)

### 1. Find the book
Call `koredata_find_library_book(title)`. Note `book_id` and `chunks` count.

### 2. Read a chunk
Call `koredata_get_library_book_chunk(book_id, offset_chars, length_chars=16000)`.
Start with `offset_chars=0`.

**Critical:** Analyze the `chunk` field text directly in your reasoning.
Do NOT call `scratchpad_query` on the auto-saved key — it extracts only headings, not facts.

### 3. Extract connections from prose paragraphs
Good nodes: named scientists (Pythagoras, Newton), named theories (heliocentrism),
named instruments (astrolabe), named places (Alexandria).

Good relationship types: `discovered`, `invented`, `proposed`, `developed`, `studied`,
`wrote`, `influenced`, `precedes`, `lived_in`, `disproved`, `is_a`, `part_of`,
`contributed_to`, `applied_to`, `is_type_of`, `succeeded`.

Do NOT create `"X is_a Science"` for chapter headings or topic groups.

### 4. Submit as one batch
Call `graph_connection_create_many([...])` with all graph connections from the chunk at once.
Never use `graph_connection_create` (single) when you have multiple graph connections.

### 5. Continue
Use `next_offset` for the next call. Repeat until `has_more` is false.

---

## Anti-patterns

- **Do not** call `scratchpad_query` on chunk keys — it returns headings, not facts.
- **Do not** invent connections from training knowledge. Only extract what is stated in the text.
- **Do not** create nodes for chapter headings, historical eras, or abstract category labels.
- **Do not** use single `graph_connection_create` calls in a loop — batch always.
- **Do not** stop at chunk 0. Chunk 0 is usually a table of contents.

---

## Triggers
- `read a book and add connections`
- `extract connections from book`
- `build a knowledge graph from`
- `library book to KoreGraph`
- `book to graph`
- `read chunks and propose connections`
- `graph connections from science book`
- `process all chunks`
