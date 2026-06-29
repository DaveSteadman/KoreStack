"""
Graph extraction using an LLM via LMStudio (OpenAI-compatible endpoint).
Reads every chunk from a KoreLibrary book, asks the LLM to extract
entity-relationship triples, and submits them to KoreGraph.

Backend: LMStudio   http://localhost:1234/v1/chat/completions

Usage:
    python extract_llm_lmstudio.py sciencehistory:2
    python extract_llm_lmstudio.py sciencehistory:2 --model openai/gpt-oss-20b
    python extract_llm_lmstudio.py sciencehistory:2 --dry-run
"""

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Extract llm lmstudio helpers for datacontrol/koredata/Graph/processing.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

import re
import json
import argparse
import httpx
import time
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

def _find_repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "config" / "korestack_config.json").exists():
            return candidate
    raise RuntimeError("Could not locate repo root from script path")

def _load_suite_config() -> dict:
    """Load config/korestack_config.json from the repo root."""
    _root = _find_repo_root()
    try:
        return json.loads((_root / "config" / "korestack_config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_llm_config() -> dict:
    _path = _find_repo_root() / "config" / "llm_config.json"
    try:
        return json.loads(_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


_suite_cfg  = _load_suite_config()
_svc_host   = _suite_cfg.get("network", {}).get("host", "127.0.0.1")
_lib_port   = _suite_cfg.get("services", {}).get("korelibrary",  {}).get("port", 9605)
_graph_port = _suite_cfg.get("services", {}).get("koregraph",    {}).get("port", 9608)

LIBRARY  = f"http://{_svc_host}:{_lib_port}"
GRAPH    = f"http://{_svc_host}:{_graph_port}"
LMSTUDIO = "http://localhost:1234"

# ── Prompt ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You extract entity-relationship triples from history-of-science texts. "
    "Return ONLY a JSON array — no markdown, no explanation, no thinking. "
    'Each element: {"start": "name", "connection": "verb", "end": "entity"}. '
    "Rules:\n"
    "- start/end must be named entities: people, places, works, concepts (not pronouns)\n"
    "- connection must be a short lowercase verb or phrase\n"
    "- Use these verbs where possible: discovered, invented, proposed, developed, "
    "founded, proved, wrote, calculated, introduced, studied, formulated, described, "
    "lived_in, is_a, influenced, preceded, translated, observed, measured, theorised\n"
    "- Skip vague or trivial facts\n"
    "- Return [] if nothing clear is found\n"
    "Example: "
    '[{"start":"Archimedes","connection":"invented","end":"water-screw"},'
    '{"start":"Archimedes","connection":"lived_in","end":"Syracuse"},'
    '{"start":"Archimedes","connection":"is_a","end":"mathematician"}]'
)

USER_TEMPLATE = "/no_think\nExtract triples from this text:\n\n{text}"

# ── Helpers ───────────────────────────────────────────────────────────────────

def fetch_chunks(book_id: str, chunk_size: int):
    client = httpx.Client(timeout=30)
    offset = 0
    while True:
        r = client.get(f"{LIBRARY}/books/{book_id}/chunk",
                       params={"offset": offset, "length": chunk_size})
        r.raise_for_status()
        data = r.json()
        yield data.get("chunk", ""), offset
        if not data.get("has_more"):
            break
        offset = data["next_offset"]


def _parse_response(raw: str) -> list[dict]:
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        items = json.loads(m.group())
    except json.JSONDecodeError:
        return []
    result = []
    for item in items:
        if (
            isinstance(item, dict)
            and item.get("start") and item.get("connection") and item.get("end")
            and item["start"].lower() not in
                ("he", "she", "they", "it", "his", "her", "this", "that", "we", "you")
        ):
            result.append({
                "start":      str(item["start"]).strip(),
                "connection": str(item["connection"]).strip().lower(),
                "end":        str(item["end"]).strip(),
            })
    return result


def submit_batch(conns: list[dict], client: httpx.Client, dry_run: bool) -> tuple[int, int]:
    if dry_run:
        return len(conns), 0
    accepted = errors = 0
    for i in range(0, len(conns), 100):
        batch = conns[i:i+100]
        r = client.post(f"{GRAPH}/api/connections/by-name/batch", json=batch, timeout=60)
        if r.is_success:
            d = r.json()
            accepted += d.get("accepted", 0)
            errors   += len(d.get("errors", []))
        else:
            errors += len(batch)
    return accepted, errors

# ── Extraction ────────────────────────────────────────────────────────────────

def llm_extract(text: str, model: str, client: httpx.Client) -> list[dict]:
    payload = {
        "model":       model,
        "stream":      False,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": USER_TEMPLATE.format(text=text)},
        ],
    }
    r = client.post(f"{LMSTUDIO}/v1/chat/completions", json=payload, timeout=300)
    r.raise_for_status()
    return _parse_response(r.json()["choices"][0]["message"]["content"])

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Extract graph triples from a KoreLibrary book using LMStudio."
    )
    ap.add_argument("book_id")
    ap.add_argument("--model",   default="openai/gpt-oss-20b")
    ap.add_argument("--chunk",   type=int, default=3000)
    ap.add_argument("--dry-run", action="store_true",
                    help="Extract but do not submit to KoreGraph")
    args = ap.parse_args()

    print(f"Script:  {Path(__file__).resolve()}", flush=True)
    print(f"Library: {LIBRARY}  Graph: {GRAPH}", flush=True)
    print(f"Book:    {args.book_id}", flush=True)
    print(f"Backend: lmstudio   Model: {args.model}", flush=True)
    print(f"Chunk:   {args.chunk}   dry-run={args.dry_run}", flush=True)
    print(flush=True)

    llm_client   = httpx.Client(timeout=300)
    graph_client = httpx.Client(timeout=60)

    all_conns: list[dict] = []
    chunk_num = 0
    total_submitted = total_errors = 0
    t0 = time.time()

    for text, offset in fetch_chunks(args.book_id, args.chunk):
        chunk_num += 1
        if not text.strip():
            print(f"  chunk {chunk_num:3d}  offset={offset:>8d}  (empty)", flush=True)
            continue

        t1 = time.time()
        try:
            found = llm_extract(text, args.model, llm_client)
        except Exception as exc:
            print(f"  chunk {chunk_num:3d}  offset={offset:>8d}  LLM ERROR: {exc}", flush=True)
            found = []
        elapsed = time.time() - t1

        new_unique = []
        for c in found:
            k = (c["start"].lower(), c["connection"], c["end"].lower())
            if not any(
                (x["start"].lower(), x["connection"], x["end"].lower()) == k
                for x in all_conns
            ):
                all_conns.append(c)
                new_unique.append(c)

        accepted = errors = 0
        if new_unique:
            accepted, errors = submit_batch(new_unique, graph_client, args.dry_run)
            total_submitted += accepted
            total_errors    += errors

        print(
            f"  chunk {chunk_num:3d}  offset={offset:>8d}  "
            f"found={len(found):3d}  new={len(new_unique):3d}  "
            f"submitted={accepted:3d}  ({elapsed:.1f}s)",
            flush=True,
        )
        for c in new_unique[:5]:
            print(f"         {c['start']} --{c['connection']}--> {c['end']}", flush=True)

    total_time = time.time() - t0
    print(flush=True)
    print(f"Done in {total_time:.0f}s", flush=True)
    print(f"Chunks:    {chunk_num}", flush=True)
    print(f"Unique:    {len(all_conns)}", flush=True)
    print(f"Submitted: {total_submitted}", flush=True)
    print(f"Errors:    {total_errors}", flush=True)

    if args.dry_run and all_conns:
        print("\nAll extracted triples (dry-run):", flush=True)
        for c in all_conns:
            print(f"  {c['start']} --{c['connection']}--> {c['end']}", flush=True)


if __name__ == "__main__":
    main()
