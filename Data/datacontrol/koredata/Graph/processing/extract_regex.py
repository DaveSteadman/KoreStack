"""
Graph extraction using regex pattern matching (no LLM).
Reads every chunk from a KoreLibrary book, applies rule-based triple
extraction, and submits the results to KoreGraph.

Patterns covered: discovered/invented/wrote/etc, is_a, lived_in,
influenced/preceded/succeeded.

Usage:
    python extract_regex.py
    (edit BOOK_ID and CHUNK at the top of this file to change targets)
"""

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Extract regex helpers for datacontrol/koredata/Graph/processing.
# Provides the focused helpers and module-level behaviour grouped into this file.
# ====================================================================================================

import re
import httpx
import json
from pathlib import Path


def _find_repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "config" / "korestack_config.json").exists():
            return candidate
    raise RuntimeError("Could not locate repo root from script path")


def _load_suite_config() -> dict:
    _root = _find_repo_root()
    try:
        return json.loads((_root / "config" / "korestack_config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


_suite_cfg  = _load_suite_config()
_svc_host   = _suite_cfg.get("network", {}).get("host", "127.0.0.1")
_lib_port   = _suite_cfg.get("services", {}).get("korelibrary",  {}).get("port", 9605)
_graph_port = _suite_cfg.get("services", {}).get("koregraph",    {}).get("port", 9608)

LIBRARY = f"http://{_svc_host}:{_lib_port}"
GRAPH   = f"http://{_svc_host}:{_graph_port}"
BOOK_ID = 'sciencehistory:2'
CHUNK   = 16000

_STOP = {
    'man','men','time','times','way','ways','fact','thing','things','world','work','works',
    'first','last','great','part','parts','same','such','this','that','these','those',
    'which','what','one','two','three','four','five','many','more','most','place','places',
    'name','names','view','views','form','forms','new','old','long','large','small',
    'early','late','good','life','hand','head','body','line','point','case','kind',
}


def _extract(sent):
    s = sent.strip()
    if len(s) < 20 or s.startswith('#'):
        return []
    out = []
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})'
        r'\s+(discovered|invented|proposed|developed|founded|established|proved|disproved|'
        r'wrote|described|calculated|measured|introduced|studied|applied|created|derived|'
        r'formulated|demonstrated|showed)'
        r'\s+(?:(?:the|a|an|his|her|its|that|how)\s+)?'
        r'([A-Za-z][a-z]{2,}(?:\s+(?:of\s+)?[a-z]{2,}){0,3})', s
    ):
        subj, verb, obj = m.group(1), m.group(2), m.group(3).strip()
        if obj.split()[0].lower() not in _STOP and len(obj) >= 4:
            out.append({'start': subj, 'connection': verb, 'end': obj})
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})'
        r'\s+was\s+(?:a|an)\s+'
        r'(Greek|Roman|Egyptian|Arab|Persian|Babylonian|Chinese|Indian|mathematician|'
        r'philosopher|astronomer|physicist|chemist|biologist|physician|geographer|'
        r'geometer|naturalist|historian|engineer|theologian|logician|scholar|scientist)', s
    ):
        out.append({'start': m.group(1), 'connection': 'is_a', 'end': m.group(2)})
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})'
        r'\s+(?:lived|worked|resided|taught|studied)\s+(?:in|at)\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,1})', s
    ):
        out.append({'start': m.group(1), 'connection': 'lived_in', 'end': m.group(2)})
    for m in re.finditer(
        r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})'
        r'\s+(influenced|inspired|succeeded|preceded)\s+'
        r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})', s
    ):
        out.append({'start': m.group(1), 'connection': m.group(2), 'end': m.group(3)})
    return out


if __name__ == "__main__":
    print(f"Script: {Path(__file__).resolve()}", flush=True)
    print(f"Library: {LIBRARY}  Graph: {GRAPH}", flush=True)
    client = httpx.Client(timeout=60)
    all_conns, offset, cn = [], 0, 0
    while True:
        r = client.get(f'{LIBRARY}/books/{BOOK_ID}/chunk', params={'offset': offset, 'length': CHUNK})
        data = r.json()
        text = data.get('chunk', '')
        found = [c for s in re.split(r'(?<=[.!?])\s+', text) for c in _extract(s)]
        all_conns.extend(found)
        cn += 1
        print(f'Chunk {cn:3d}  offset={offset:>7d}  +{len(found):3d}  total={len(all_conns)}')
        if not data.get('has_more'):
            break
        offset = data['next_offset']

    seen = set()
    unique = []
    for c in all_conns:
        k = (c['start'].lower(), c['connection'], c['end'].lower())
        if k not in seen:
            seen.add(k)
            unique.append(c)

    print(f'\nSample connections:')
    for c in unique[:25]:
        print(f'  {c["start"]} --{c["connection"]}--> {c["end"]}')
    print(f'\nTotal unique: {len(unique)}')

    submitted, errors = 0, 0
    for i in range(0, len(unique), 100):
        batch = unique[i:i+100]
        gr = client.post(f'{GRAPH}/api/connections/by-name/batch', json=batch, timeout=60)
        if gr.is_success:
            result = gr.json()
            submitted += result.get('accepted', len(batch))
            errors    += len(result.get('errors', []))
        else:
            print(f'Batch error: {gr.status_code}')
            errors += len(batch)

    print(f'\nSubmitted: {submitted}  Errors: {errors}')
