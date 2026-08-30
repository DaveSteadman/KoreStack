"""Generate the reviewed KoreDocs skill manifest from its public REST wrappers.

Run this intentionally when a public `koredocs_*` wrapper is added or changed:
    python build_manifest.py

The generated JSON is committed/reviewed before the service registers it.  This
keeps the manifest complete without making runtime registration inspect source.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = Path(__file__).with_name("skills.json")

# This native wrapper predates the former MCP surface and remains part of the
# public REST contract alongside the reviewed `koredocs_*` wrappers below.
_EXTRA_NATIVE_SKILLS = [
    {
        "name": "koredocs_search",
        "purpose": "Search the full text of KoreDocs files.",
        "selection_description": "Search text inside stored KoreDocs files; use a document type or folder filter only when the user specifies one.",
        "keywords": ["document", "file_handling", "search", "full_text"],
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words or phrases to find."},
                "type": {"type": "string", "description": "Optional document type filter."},
                "folder_path": {"type": "string", "description": "Optional folder path filter."},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
        "returns": "Matching document excerpts and metadata.",
        "invoke_path": "/api/skills/koredocs_search/invoke",
    },
]


def _annotation_type(annotation: ast.expr | None) -> str:
    """Convert the outer type of a typed wrapper argument to JSON Schema."""
    if annotation is None:
        return "string"
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name) and annotation.value.id == "Annotated":
        values = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else []
        return _annotation_type(values[0] if values else None)
    text = ast.unparse(annotation).lower()
    if "bool" in text:
        return "boolean"
    if "int" in text:
        return "integer"
    if "float" in text:
        return "number"
    if "list" in text:
        return "array"
    if "dict" in text or "any" in text or "object" in text:
        return "object"
    return "string"


def _annotation_description(annotation: ast.expr | None) -> str:
    if not isinstance(annotation, ast.Subscript) or not isinstance(annotation.value, ast.Name) or annotation.value.id != "Annotated":
        return ""
    values = annotation.slice.elts if isinstance(annotation.slice, ast.Tuple) else []
    if len(values) > 1 and isinstance(values[1], ast.Constant) and isinstance(values[1].value, str):
        return values[1].value
    return ""


def _keywords(source_file: str, name: str) -> list[str]:
    """Reviewed, deliberately broad capability tags for the selection control."""
    if source_file == "tools_koresheet.py":
        return ["spreadsheet", "dataset", "file_handling"]
    if source_file == "tools_korediag.py":
        return ["diagram", "document", "file_handling"]
    if source_file == "tools_koredoc.py":
        tags = ["document", "koredoc", "file_handling"]
        if "metadata" in name:
            tags.append("metadata")
        elif "section" in name or "markdown" in name or "outline" in name:
            tags.append("document_editing")
        return tags
    tags = ["document", "file_handling"]
    if "search" in name:
        tags.append("search")
    if "history" in name:
        tags.append("revision_history")
    if "folder" in name:
        tags.append("folders")
    if "format" in name or "types" in name:
        tags.append("formats")
    return tags


def _purpose(name: str, docstring: str) -> str:
    first_line = docstring.strip().splitlines()[0] if docstring.strip() else ""
    if first_line and not first_line.startswith("Canonical prefixed alias"):
        return first_line
    action = name.removeprefix("koredocs_").replace("_", " ")
    action = action.replace("diag", "diagram").replace("doc", "document")
    action = action.replace("files", "files").replace("file", "file")
    action = action.replace("sheet", "spreadsheet")
    return f"KoreDocs: {action}."


def _skill(node: ast.FunctionDef | ast.AsyncFunctionDef, source_file: str) -> dict:
    arguments = node.args.args
    defaults = [None] * (len(arguments) - len(node.args.defaults)) + list(node.args.defaults)
    properties: dict[str, dict] = {}
    required: list[str] = []
    for argument, default in zip(arguments, defaults):
        property_schema: dict[str, object] = {"type": _annotation_type(argument.annotation)}
        description = _annotation_description(argument.annotation)
        if description:
            property_schema["description"] = description
        if default is None:
            required.append(argument.arg)
        else:
            try:
                property_schema["default"] = ast.literal_eval(default)
            except (ValueError, TypeError):
                pass
        properties[argument.arg] = property_schema
    parameters: dict[str, object] = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required
    return {
        "name": node.name,
        "purpose": _purpose(node.name, ast.get_docstring(node) or ""),
        "selection_description": _purpose(node.name, ast.get_docstring(node) or ""),
        "keywords": _keywords(source_file, node.name),
        "parameters": parameters,
        "returns": "A KoreDocs operation result.",
        "invoke_path": f"/api/skills/{node.name}/invoke",
    }


def build_manifest() -> dict:
    skills: list[dict] = list(_EXTRA_NATIVE_SKILLS)
    for source in sorted((ROOT / "app" / "mcp").glob("tools_*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("koredocs_"):
                skills.append(_skill(node, source.name))
    return {
        "schema_version": 1,
        "service": "koredocs",
        "service_label": "KoreDocs",
        "skills": sorted(skills, key=lambda skill: skill["name"]),
    }


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(build_manifest(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}")
