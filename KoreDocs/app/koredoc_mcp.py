"""MCP tools for .koredoc documents. Imported by koredocs_mcp.py after shared helpers are available."""

from __future__ import annotations
from typing import Optional, Annotated
from . import korefile
from ._mcp_shared import (
    mcp, _file_summary, _koredoc_file, _koredoc_parse, _koredoc_find_heading,
    _koredoc_extract_lines, _koredoc_normalize_block, _koredoc_splice,
    _create_serialized_file, _ensure_extension,
)


def create_koredoc(
    folder_path: Annotated[str, 'Folder path in KoreFile, such as "/" or "/Projects". Missing folders are created.'],
    name: Annotated[str, 'Filename, with or without the .koredoc extension.'],
    markdown: Annotated[str, 'Markdown body for the document.'],
    title: Annotated[Optional[str], 'Optional title. If provided, YAML frontmatter is added.'] = None,
    tags: Annotated[Optional[list[str]], 'Optional tags to include in YAML frontmatter.'] = None,
) -> dict:
    """Create a .koredoc document from Markdown, adding frontmatter when title or tags are supplied."""
    content = markdown
    metadata = None
    if title or tags:
        lines = ['---']
        if title:
            lines.append(f'title: {title}')
        if tags:
            lines.append('tags: ' + ', '.join(tags))
        lines.extend(['---', '', markdown.lstrip('\n')])
        content = '\n'.join(lines)
        metadata = {'title': title or name.rsplit('.', 1)[0]}
        if tags:
            metadata['tags'] = tags
    return _create_serialized_file(folder_path, name, 'koredoc', content, metadata)


@mcp.tool()
def koredocs_get_koredoc_outline(
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
def koredocs_read_koredoc_section(
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
def koredocs_replace_koredoc_section(
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
def koredocs_insert_koredoc_section(
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
def koredocs_append_koredoc_markdown(
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
def koredocs_create_koredoc(
    folder_path: Annotated[str, 'Folder path in KoreFile, such as "/" or "/Projects". Missing folders are created.'],
    name: Annotated[str, 'Filename, with or without the .koredoc extension.'],
    markdown: Annotated[str, 'Markdown body for the document.'],
    title: Annotated[Optional[str], 'Optional title.'] = None,
    tags: Annotated[Optional[list[str]], 'Optional tags.'] = None,
) -> dict:
    """Canonical prefixed alias for create_koredoc."""
    return create_koredoc(folder_path=folder_path, name=name, markdown=markdown, title=title, tags=tags)
