# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# MCP tools for .koredoc documents.
#
# Imported by koredocs_mcp.py after shared helpers are available.
# Registers tool handlers for creating, reading, and editing .koredoc Markdown files
# stored in the KoreFile virtual file system.  Heading-based editing tools allow
# surgical modification of individual document sections.
#
# Related modules:
#   - app/_mcp_shared.py    -- parser helpers (_koredoc_parse, _koredoc_find_heading, etc.)
#   - app/koredocs_mcp.py   -- imports this module to register its tools
#   - app/korefile.py       -- underlying virtual FS
# MARK: FUNCTIONS
# Function inventory:
# - create_koredoc: Creates koredoc for this module.
# - _metadata_paths: Implements the  metadata paths operation for this module.
# - _metadata_files: Implements the  metadata files operation for this module.
# - koredocs_metadata_inventory: Implements the koredocs metadata inventory operation for this module.
# - koredocs_metadata_find_variants: Implements the koredocs metadata find variants operation for this module.
# - _metadata_migration_matches: Implements the  metadata migration matches operation for this module.
# - koredocs_metadata_rename_field: Implements the koredocs metadata rename field operation for this module.
# - koredocs_metadata_replace_value: Implements the koredocs metadata replace value operation for this module.
# - koredocs_doc_outline_get: Implements the koredocs doc outline get operation for this module.
# - koredocs_doc_section_read: Implements the koredocs doc section read operation for this module.
# - koredocs_doc_section_replace: Implements the koredocs doc section replace operation for this module.
# - koredocs_doc_section_insert: Implements the koredocs doc section insert operation for this module.
# - koredocs_doc_markdown_append: Implements the koredocs doc markdown append operation for this module.
# - koredocs_doc_create: Implements the koredocs doc create operation for this module.
# - koredocs_doc_create_from_scratchpad: Implements the koredocs doc create from scratchpad operation for this module.
# ====================================================================================================

from __future__ import annotations
import json
from difflib import SequenceMatcher
from typing import Optional, Annotated
from ..documents.korefile import service as korefile
from .shared import (
    mcp, _file_summary, _koredoc_file, _koredoc_parse, _koredoc_find_heading,
    _koredoc_extract_lines, _koredoc_normalize_block, _koredoc_splice,
    _create_serialized_file, _ensure_extension,
)


def create_koredoc(
    folder_path: Annotated[str, 'Folder path in the shared KoreDocs/datauser tree, such as "/" or "/Projects". Missing folders are created.'],
    name: Annotated[str, 'Filename, with or without the .koredoc extension.'],
    markdown: Annotated[str, 'Markdown body for the document.'],
    title: Annotated[Optional[str], 'Optional title stored in the embedded KoreDocs JSON header.'] = None,
    tags: Annotated[Optional[list[str]], 'Optional tags stored in the embedded KoreDocs JSON header.'] = None,
    metadata: Annotated[Optional[dict], 'Optional artefact metadata, e.g. artefact_type, geography, period, provenance, and source_refs.'] = None,
) -> dict:
    """Create a self-contained .koredoc with metadata embedded in a JSON header."""
    stored_metadata = dict(metadata or {})
    if title:
        stored_metadata['title'] = title or name.rsplit('.', 1)[0]
    if tags:
        stored_metadata['tags'] = tags
    return _create_serialized_file(folder_path, name, 'koredoc', markdown, stored_metadata or None)


def _metadata_paths(value: object, prefix: str = "") -> list[tuple[str, object]]:
    """Return every metadata key path and scalar/list value without changing it."""
    if isinstance(value, dict):
        paths: list[tuple[str, object]] = []
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(_metadata_paths(child, path))
        return paths
    return [(prefix, value)] if prefix else []


def _metadata_files(folder_path: str | None = None) -> list[dict]:
    return korefile.list_files(folder_path=folder_path, ext="koredoc")


@mcp.tool()
def koredocs_metadata_inventory(
    folder_path: Annotated[Optional[str], "Optional folder path to limit the inventory."] = None,
    max_examples: Annotated[int, "Maximum example documents returned for each metadata path."] = 3,
) -> dict:
    """Inventory embedded KoreDoc metadata keys, values, counts, and example files."""
    examples_limit = max(1, min(int(max_examples), 20))
    fields: dict[str, dict] = {}
    files = _metadata_files(folder_path)
    for file in files:
        metadata = file.get("metadata") if isinstance(file.get("metadata"), dict) else {}
        for path, value in _metadata_paths(metadata):
            entry = fields.setdefault(path, {"document_count": 0, "value_counts": {}, "examples": []})
            entry["document_count"] += 1
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
            entry["value_counts"][encoded] = entry["value_counts"].get(encoded, 0) + 1
            if len(entry["examples"]) < examples_limit:
                entry["examples"].append({"id": file["id"], "path": file["path"], "value": value})
    return {
        "document_count": len(files),
        "field_count": len(fields),
        "fields": [
            {
                "path": path,
                "document_count": entry["document_count"],
                "value_counts": [
                    {"value": json.loads(value), "count": count}
                    for value, count in sorted(entry["value_counts"].items(), key=lambda item: (-item[1], item[0]))
                ],
                "examples": entry["examples"],
            }
            for path, entry in sorted(fields.items())
        ],
    }


@mcp.tool()
def koredocs_metadata_find_variants(
    field_name: Annotated[str, "Field name or dotted field path to compare, for example artefact_type."],
    folder_path: Annotated[Optional[str], "Optional folder path to limit the search."] = None,
) -> dict:
    """Find metadata field names that are close to a requested name, to expose naming drift."""
    target = str(field_name or "").strip().lower().replace("-", "_")
    if not target:
        raise ValueError("field_name cannot be empty")
    inventory = koredocs_metadata_inventory(folder_path=folder_path)
    variants = []
    for field in inventory["fields"]:
        candidate = field["path"]
        normalized = candidate.lower().replace("-", "_")
        similarity = SequenceMatcher(None, target, normalized).ratio()
        if target == normalized or target in normalized or normalized in target or similarity >= 0.78:
            variants.append({**field, "similarity": round(similarity, 3)})
    return {"field_name": field_name, "variant_count": len(variants), "variants": variants}


def _metadata_migration_matches(
    *,
    folder_path: str | None,
    field_name: str,
    old_value: object = None,
    require_value: bool = False,
) -> list[dict]:
    matches: list[dict] = []
    for file in _metadata_files(folder_path):
        metadata = file.get("metadata") if isinstance(file.get("metadata"), dict) else {}
        if field_name not in metadata:
            continue
        if require_value and metadata[field_name] != old_value:
            continue
        matches.append(file)
    return matches


@mcp.tool()
def koredocs_metadata_rename_field(
    old_field: Annotated[str, "Exact top-level metadata field to rename."],
    new_field: Annotated[str, "Replacement top-level metadata field name."],
    folder_path: Annotated[Optional[str], "Optional folder path to limit the migration."] = None,
    apply_changes: Annotated[bool, "False returns a dry run. True performs the listed exact changes."] = False,
) -> dict:
    """Rename one exact top-level metadata field while preserving content and unrelated metadata."""
    old_field = str(old_field or "").strip()
    new_field = str(new_field or "").strip()
    if not old_field or not new_field:
        raise ValueError("old_field and new_field cannot be empty")
    if old_field == new_field:
        raise ValueError("old_field and new_field must differ")
    matches = _metadata_migration_matches(folder_path=folder_path, field_name=old_field)
    conflicts = [file for file in matches if new_field in (file.get("metadata") or {})]
    proposed = [{"id": file["id"], "path": file["path"]} for file in matches]
    if conflicts:
        return {"applied": False, "proposed": proposed, "conflicts": [{"id": file["id"], "path": file["path"]} for file in conflicts]}
    if apply_changes:
        for file in matches:
            metadata = dict(file.get("metadata") or {})
            metadata[new_field] = metadata.pop(old_field)
            korefile.update_file(file["id"], metadata=metadata, expected_revision=file.get("revision"))
    return {"applied": bool(apply_changes), "proposed": proposed, "changed_count": len(matches), "conflicts": []}


@mcp.tool()
def koredocs_metadata_replace_value(
    field_name: Annotated[str, "Exact top-level metadata field whose value will be replaced."],
    old_value: Annotated[object, "Exact JSON value to replace."],
    new_value: Annotated[object, "Replacement JSON value."],
    folder_path: Annotated[Optional[str], "Optional folder path to limit the migration."] = None,
    apply_changes: Annotated[bool, "False returns a dry run. True performs the listed exact changes."] = False,
) -> dict:
    """Replace one exact top-level metadata value while preserving content and unrelated metadata."""
    field_name = str(field_name or "").strip()
    if not field_name:
        raise ValueError("field_name cannot be empty")
    matches = _metadata_migration_matches(folder_path=folder_path, field_name=field_name, old_value=old_value, require_value=True)
    proposed = [{"id": file["id"], "path": file["path"], "old_value": old_value, "new_value": new_value} for file in matches]
    if apply_changes:
        for file in matches:
            metadata = dict(file.get("metadata") or {})
            metadata[field_name] = new_value
            korefile.update_file(file["id"], metadata=metadata, expected_revision=file.get("revision"))
    return {"applied": bool(apply_changes), "proposed": proposed, "changed_count": len(matches)}


@mcp.tool()
def koredocs_doc_outline_get(
    id: Annotated[int, 'KoreDoc file id.'],
) -> dict:
    """Return the heading outline for a .koredoc document."""
    file = _koredoc_file(id)
    parsed = _koredoc_parse(file.get('content') or '')
    headings = [
        {
            'level': heading['level'],
            'title': heading['title'],
            'path': heading['path'],
            'line_start': heading['line_start'],
            'line_end': heading['line_end'],
        }
        for heading in parsed['headings']
    ]
    return {
        **_file_summary(file),
        'heading_count': len(headings),
        'line_count': parsed['line_count'],
        'frontmatter_end_line': parsed['frontmatter']['frontmatter_end_line'],
        'headings': headings,
    }


@mcp.tool()
def koredocs_doc_section_read(
    id: Annotated[int, 'KoreDoc file id.'],
    heading_path: Annotated[Optional[list[str]], 'Optional heading path such as ["Overview", "Risks"].'] = None,
    start_line: Annotated[Optional[int], 'Optional 1-based start line for direct line-range reads.'] = None,
    end_line: Annotated[Optional[int], 'Optional 1-based end line for direct line-range reads.'] = None,
) -> dict:
    """Read a full .koredoc document, one heading section, or an explicit line range."""
    file = _koredoc_file(id)
    content = file.get('content') or ''
    parsed = _koredoc_parse(content)

    if start_line is not None or end_line is not None:
        if start_line is None or end_line is None:
            raise ValueError('start_line and end_line must be provided together')
        markdown = _koredoc_extract_lines(parsed, start_line, end_line)
        return {
            **_file_summary(file),
            'mode': 'line_range',
            'start_line': start_line,
            'end_line': end_line,
            'markdown': markdown,
        }

    if heading_path:
        heading = _koredoc_find_heading(parsed, heading_path)
        markdown = _koredoc_extract_lines(parsed, heading['line_start'], heading['line_end'])
        return {
            **_file_summary(file),
            'mode': 'heading_section',
            'heading_path': heading['path'],
            'start_line': heading['line_start'],
            'end_line': heading['line_end'],
            'markdown': markdown,
        }

    return {
        **_file_summary(file),
        'mode': 'full_document',
        'start_line': 1,
        'end_line': parsed['line_count'],
        'markdown': content,
    }


@mcp.tool()
def koredocs_doc_section_replace(
    id: Annotated[int, 'KoreDoc file id.'],
    heading_path: Annotated[list[str], 'Heading path identifying the section to replace.'],
    markdown: Annotated[str, 'Complete replacement markdown for the section, including the heading line.'],
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check. When provided, the document must still be at this revision.'] = None,
) -> dict:
    """Replace one heading section inside a .koredoc document."""
    file = _koredoc_file(id)
    content = file.get('content') or ''
    parsed = _koredoc_parse(content)
    heading = _koredoc_find_heading(parsed, heading_path)
    new_content = _koredoc_splice(content, heading['line_start'], heading['line_end'], markdown)
    updated = korefile.update_file(id, new_content, expected_revision=expected_revision)
    if updated is None:
        raise ValueError(f'File not found: {id}')
    reparsed = _koredoc_parse(new_content)
    return {
        **_file_summary(updated),
        'replaced_heading_path': heading['path'],
        'line_count': reparsed['line_count'],
    }


@mcp.tool()
def koredocs_doc_section_insert(
    id: Annotated[int, 'KoreDoc file id.'],
    markdown: Annotated[str, 'Markdown block to insert. Typically starts with a heading line.'],
    after_heading_path: Annotated[Optional[list[str]], 'Insert after the matching section.'] = None,
    parent_heading_path: Annotated[Optional[list[str]], 'Insert inside this parent section when after_heading_path is omitted.'] = None,
    insert_at_start: Annotated[bool, 'When parent_heading_path is used, insert immediately after the parent heading instead of at the end of the parent section.'] = False,
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check. When provided, the document must still be at this revision.'] = None,
) -> dict:
    """Insert a markdown block into a .koredoc document by section anchor."""
    if after_heading_path and parent_heading_path:
        raise ValueError('Provide either after_heading_path or parent_heading_path, not both')

    file = _koredoc_file(id)
    content = file.get('content') or ''
    parsed = _koredoc_parse(content)

    if after_heading_path:
        anchor = _koredoc_find_heading(parsed, after_heading_path)
        start_line = anchor['line_end'] + 1
        end_line = anchor['line_end']
        placement = {'mode': 'after_heading', 'heading_path': anchor['path']}
    elif parent_heading_path:
        parent = _koredoc_find_heading(parsed, parent_heading_path)
        if insert_at_start:
            start_line = parent['content_start_line']
            end_line = parent['content_start_line'] - 1
            placement = {'mode': 'parent_start', 'heading_path': parent['path']}
        else:
            start_line = parent['line_end'] + 1
            end_line = parent['line_end']
            placement = {'mode': 'parent_end', 'heading_path': parent['path']}
    else:
        start_line = parsed['line_count'] + 1
        end_line = parsed['line_count']
        placement = {'mode': 'document_end'}

    new_content = _koredoc_splice(content, start_line, end_line, markdown)
    updated = korefile.update_file(id, new_content, expected_revision=expected_revision)
    if updated is None:
        raise ValueError(f'File not found: {id}')
    reparsed = _koredoc_parse(new_content)
    return {
        **_file_summary(updated),
        **placement,
        'line_count': reparsed['line_count'],
    }


@mcp.tool()
def koredocs_doc_markdown_append(
    id: Annotated[int, 'KoreDoc file id.'],
    markdown: Annotated[str, 'Markdown block to append to the end of the document.'],
    expected_revision: Annotated[Optional[int], 'Optional optimistic concurrency check. When provided, the document must still be at this revision.'] = None,
) -> dict:
    """Append markdown to the end of a .koredoc document."""
    file = _koredoc_file(id)
    content = file.get('content') or ''
    parsed = _koredoc_parse(content)
    new_content = _koredoc_splice(content, parsed['line_count'] + 1, parsed['line_count'], markdown)
    updated = korefile.update_file(id, new_content, expected_revision=expected_revision)
    if updated is None:
        raise ValueError(f'File not found: {id}')
    reparsed = _koredoc_parse(new_content)
    return {
        **_file_summary(updated),
        'mode': 'document_end',
        'line_count': reparsed['line_count'],
    }


@mcp.tool()
def koredocs_doc_create(
    folder_path: Annotated[str, 'Folder path in the shared KoreDocs/datauser tree, such as "/" or "/Projects". Missing folders are created.'],
    name: Annotated[str, 'Filename, with or without the .koredoc extension.'],
    markdown: Annotated[str, 'Markdown body for the document.'],
    title: Annotated[Optional[str], 'Optional title.'] = None,
    tags: Annotated[Optional[list[str]], 'Optional tags.'] = None,
    metadata: Annotated[Optional[dict], 'Optional artefact metadata.'] = None,
) -> dict:
    """Canonical prefixed alias for create_koredoc."""
    return create_koredoc(folder_path=folder_path, name=name, markdown=markdown, title=title, tags=tags, metadata=metadata)


@mcp.tool()
def koredocs_doc_create_from_scratchpad(
    folder_path: Annotated[str, 'Folder path for the new KoreDoc.'],
    name: Annotated[str, 'Filename, with or without the .koredoc extension.'],
    scratchpad_content: Annotated[str, 'Pass the exact token {scratchpad:key} shown after a prior tool result. KoreAgent resolves it before this tool runs, preserving generated text without model transcription.'],
    metadata: Annotated[Optional[dict], 'Optional artefact metadata and provenance.'] = None,
    title: Annotated[Optional[str], 'Optional title stored in the embedded KoreDocs JSON header.'] = None,
    tags: Annotated[Optional[list[str]], 'Optional tags stored in the embedded KoreDocs JSON header.'] = None,
) -> dict:
    """Create a KoreDoc from an exact prior tool result held in the scratchpad.

    Use after Python, retrieval, or transformation tools when copying their output
    into a document could introduce transcription or encoding errors.
    """
    return create_koredoc(
        folder_path=folder_path,
        name=name,
        markdown=scratchpad_content,
        title=title,
        tags=tags,
        metadata=metadata,
    )
