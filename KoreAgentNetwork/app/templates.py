from __future__ import annotations

import json
import re
import uuid

from .config import TEMPLATES_FILE
from .store import validate_node


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


def _seed(name: str, description: str, kind: str, inputs: list[dict], outputs: list[str], code: str = "", config: dict | None = None) -> dict:
    return {
        "id":          "template_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"),
        "name":        name,
        "description": description,
        "node": {
            "id":       "template_node",
            "label":    name,
            "kind":     kind,
            "config":   config or {},
            "position": {"x": 0, "y": 0},
            "inputs":   inputs,
            "outputs":  [{"name": item} for item in outputs],
            "code":     code,
        },
    }


_SAVED_SEARCH = r'''SEARCH = "AINews"          # KoreData saved search name
LIMIT  = 20

result = api_post("koredatagateway", "/api/savedsearches/" + SEARCH + "/run", {}, timeout=30)
items  = result.get("results", [])[:LIMIT]

outputs["results"] = items
outputs["count"]   = len(items)
outputs["text"]    = "\n\n".join(
    "{} ({}, {})\n{}\n{}".format(i.get("title", ""), i.get("source", ""), i.get("published_at", ""), i.get("snippet", ""), i.get("url", ""))
    for i in items
)
'''

_DEDUPE = r'''items = inputs["results"]

lines = "\n".join(
    "{}: {} | {}".format(n, i.get("title", ""), (i.get("snippet", "") or "")[:160].replace("\n", " "))
    for n, i in enumerate(items)
)
prompt = (
    "Below are numbered news items. Group items that report the SAME underlying story or event. "
    "Reply with JSON only: a list of groups, each group a list of item numbers. "
    "Every number must appear in exactly one group; unique stories are single-item groups.\n\n" + lines
)

reply = llm(prompt, timeout=120)
groups = json.loads(reply[reply.find("["):reply.rfind("]") + 1])

seen, kept, removed = set(), [], 0
for g in groups:
    g = [n for n in g if isinstance(n, int) and 0 <= n < len(items) and n not in seen]
    if not g:
        continue
    seen.update(g)
    kept.append(dict(items[g[0]], also_reported_by=[{"source": items[n].get("source"), "url": items[n].get("url")} for n in g[1:]]))
    removed += len(g) - 1

for n, i in enumerate(items):
    if n not in seen:
        kept.append(dict(i, also_reported_by=[]))

outputs["results"] = kept
outputs["count"]   = len(kept)
outputs["removed"] = removed
'''

_REFERENCE = r'''def enc(s):
    return "".join(
        c if (c.isalnum() or c in "-_.~") else "".join("%{:02X}".format(b) for b in c.encode("utf-8"))
        for c in s
    )

pick    = api_get("korereference", "/api/articles/random")
title   = pick["title"]
article = api_get("korereference", "/api/articles/" + enc(title), timeout=30)

slug = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in title).strip().replace(" ", "_")[:80]
FILE = "KoreNetworks/output/reference_" + slug + ".md"

write_text(FILE, "# " + title + "\n\n" + (article.get("body") or article.get("summary") or "") + "\n")

outputs["title"] = title
outputs["path"]  = FILE
outputs["words"] = article.get("word_count")
'''

_READ_FILE = r'''FILE = inputs["path"] or "KoreNetworks/output/notes.md"   # relative to the datauser folder

if not exists(FILE):
    raise RuntimeError("File not found: " + FILE)

text = read_text(FILE)
outputs["text"]  = text
outputs["chars"] = len(text)
'''

_WRITE_JSON = r'''FILE  = inputs["path"] or "KoreNetworks/output/data.json"   # relative to the datauser folder
value = inputs["value"]

if isinstance(value, str):
    value = json.loads(value)

write_json(FILE, value)
outputs["path"] = FILE
'''

_WRITE_TEXT = r'''FILE = inputs["path"] or "KoreNetworks/output/output.txt"   # relative to the datauser folder

write_text(FILE, str(inputs["text"]))
outputs["path"] = FILE
'''

_MD_TO_HTML = r'''md = inputs["text"]

def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def inline(s):
    s = esc(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", r'<img src="\2" alt="\1">', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', s)
    return s

out, para, items, kind, code = [], [], [], "", None

def flush():
    if para:
        out.append("<p>" + " ".join(para) + "</p>")
        del para[:]
    if items:
        out.append("<" + kind + ">" + "".join("<li>" + i + "</li>" for i in items) + "</" + kind + ">")
        del items[:]

for line in md.splitlines():
    if code is not None:
        if line.strip().startswith("```"):
            out.append("<pre><code>" + esc("\n".join(code)) + "</code></pre>")
            code = None
        else:
            code.append(line)
        continue
    stripped = line.strip()
    if stripped.startswith("```"):
        flush()
        code = []
        continue
    head   = re.match(r"^(#{1,6})\s+(.*)$", stripped)
    bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
    number = re.match(r"^\d+[.)]\s+(.*)$", stripped)
    if not stripped:
        flush()
    elif head:
        flush()
        n = len(head.group(1))
        out.append("<h{0}>{1}</h{0}>".format(n, inline(head.group(2))))
    elif re.match(r"^(---+|\*\*\*+)$", stripped):
        flush()
        out.append("<hr>")
    elif stripped.startswith(">"):
        flush()
        out.append("<blockquote>" + inline(stripped.lstrip("> ")) + "</blockquote>")
    elif bullet or number:
        want = "ul" if bullet else "ol"
        if items and kind != want:
            flush()
        kind = want
        items.append(inline((bullet or number).group(1)))
    else:
        if items:
            flush()
        para.append(inline(stripped))
flush()
if code is not None:
    out.append("<pre><code>" + esc("\n".join(code)) + "</code></pre>")

outputs["html"] = "\n".join(out)
'''

_COMMS_SEND = r'''CONNECTION = "My connection"          # KoreComms connection name

ifaces = api_get("korecomms", "/api/interfaces")
iface  = next((i for i in ifaces if i["name"].casefold() == CONNECTION.casefold()), None)
if iface is None:
    raise RuntimeError("KoreComms connection not found: " + CONNECTION)

content = inputs["content"]
if not isinstance(content, str):
    content = json.dumps(content, ensure_ascii=False)

outputs["result"] = api_post("korecomms", "/api/send", {
    "interface_id": iface["id"],
    "subject":      str(inputs["subject"]),
    "content":      content,
})
'''

DEFAULT_TEMPLATES: list[dict] = [
    _seed("KoreData saved search", "Run a saved search and output its items", "python", [], ["results", "count", "text"], _SAVED_SEARCH),
    _seed("Deduplicate stories", "LLM groups duplicate stories by index; original items and URLs are kept", "python", [{"name": "results", "default": []}], ["results", "count", "removed"], _DEDUPE),
    _seed("Random reference article", "Write a random KoreReference article to a Markdown file", "python", [], ["title", "path", "words"], _REFERENCE),
    _seed("Read file", "Read a text file from the datauser folder", "python", [{"name": "path", "default": ""}], ["text", "chars"], _READ_FILE),
    _seed("Write JSON file", "Write a value as JSON to the datauser folder", "python", [{"name": "value", "default": None}, {"name": "path", "default": ""}], ["path"], _WRITE_JSON),
    _seed("Write text file", "Write a string to the datauser folder", "python", [{"name": "text", "default": ""}, {"name": "path", "default": ""}], ["path"], _WRITE_TEXT),
    _seed("Markdown to HTML", "Basic Markdown to HTML conversion", "python", [{"name": "text", "default": ""}], ["html"], _MD_TO_HTML),
    _seed("KoreComms send", "Send a subject and content through a named KoreComms connection", "python", [{"name": "subject", "default": ""}, {"name": "content", "default": ""}], ["result"], _COMMS_SEND),
    _seed("Summarise text", "LLM summary as bullet points", "llm", [{"name": "text"}], ["response"], "", {"prompt_template": "Summarise the following in a few concise bullet points:\n\n{text}", "model": ""}),
    _seed("Extract JSON", "LLM turns text into a JSON object (reply is text, parse it downstream)", "llm", [{"name": "text"}], ["response"], "", {"prompt_template": "Extract the key facts from the text below as a single JSON object. Reply with JSON only.\n\n{text}", "model": ""}),
]


def _read() -> list[dict]:
    try:
        data = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("templates") if isinstance(data, dict) else None
    return [item for item in items if isinstance(item, dict) and isinstance(item.get("node"), dict)] if isinstance(items, list) else []


def _write(templates: list[dict]) -> None:
    TEMPLATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATES_FILE.write_text(json.dumps({"templates": templates}, indent=2) + "\n", encoding="utf-8")


def list_templates() -> list[dict]:
    if not TEMPLATES_FILE.exists():
        _write(DEFAULT_TEMPLATES)
    return _read()


def add_template(node: object, name: str = "", description: str = "") -> dict:
    clean = validate_node(node)
    template = {
        "id":          f"template_{uuid.uuid4().hex[:10]}",
        "name":        (name or clean["label"]).strip() or clean["label"],
        "description": description.strip() or f"{clean['kind']} block",
        "node":        {**clean, "position": {"x": 0, "y": 0}},
    }
    _write(list_templates() + [template])
    return template


def delete_template(template_id: str) -> None:
    if not _SAFE_ID.fullmatch(template_id):
        raise ValueError("Invalid template id")
    current = list_templates()
    remaining = [item for item in current if item.get("id") != template_id]
    if len(remaining) == len(current):
        raise FileNotFoundError(template_id)
    _write(remaining)
