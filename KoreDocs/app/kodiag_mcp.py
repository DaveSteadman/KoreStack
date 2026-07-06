# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# MCP tools and helpers for .korediag diagram documents.
#
# .korediag files are stored as JSON in the KoreFile virtual FS with a node/edge/style
# format.  The _normalize_node_for_editor() helper ensures consistent node shapes
# before serialization.  UUID-based node IDs are generated at creation time.
#
# Related modules:
#   - app/_mcp_shared.py    -- _create_serialized_file, _ensure_extension
#   - app/koredocs_mcp.py   -- imports this module to register its tools
#   - app/korefile.py       -- underlying virtual FS
# ====================================================================================================

from __future__ import annotations
import json
from typing import Optional, Annotated
from uuid import uuid4
from . import korefile
from ._mcp_shared import mcp, _file_summary, _create_serialized_file, _ensure_extension, _now_iso


def _normalize_node_for_editor(node: dict) -> dict:
    obj = dict(node)
    if isinstance(obj.get('label'), str):
        obj['label'] = obj['label'].replace('\\n', '\n')
    if 'width' not in obj and 'w' in obj:
        obj['width'] = obj['w']
    if 'height' not in obj and 'h' in obj:
        obj['height'] = obj['h']

    style = obj.get('style')
    if isinstance(style, dict):
        style = dict(style)
        if 'fillColor' not in style and 'fill' in style:
            style['fillColor'] = style['fill']
        obj['style'] = style

    children = obj.get('children')
    if isinstance(children, list):
        obj['children'] = [
            _normalize_node_for_editor(child) if isinstance(child, dict) else child
            for child in children
        ]
    else:
        obj.setdefault('children', [])
    obj.setdefault('meta', {})
    return obj


def _normalize_edge_for_editor(edge: dict) -> dict:
    obj = dict(edge)
    if isinstance(obj.get('label'), str):
        obj['label'] = obj['label'].replace('\\n', '\n')
    if 'from' not in obj and 'fromNode' in obj:
        obj['from'] = obj['fromNode']
    if 'to' not in obj and 'toNode' in obj:
        obj['to'] = obj['toNode']
    obj.setdefault('via', [])
    obj.setdefault('style', {})
    obj.setdefault('meta', {})
    return obj


def _normalize_diagram_for_editor(diagram: dict) -> dict:
    obj = dict(diagram)
    obj['nodes'] = [
        _normalize_node_for_editor(node) if isinstance(node, dict) else node
        for node in obj.get('nodes', [])
    ]
    obj['edges'] = [
        _normalize_edge_for_editor(edge) if isinstance(edge, dict) else edge
        for edge in obj.get('edges', [])
    ]
    return obj


def _diag_content(title: str, diagram: Optional[dict] = None) -> str:
    if diagram is not None:
        obj = _normalize_diagram_for_editor(diagram)
        obj.setdefault('koreDiag', '1.0')
        obj.setdefault('id', str(uuid4()))
        obj.setdefault('title', title)
        obj.setdefault('created', _now_iso())
        obj.setdefault('modified', _now_iso())
        obj.setdefault('settings', {})
        obj['settings'].setdefault('gridSize', 20)
        obj['settings'].setdefault('defaultArrow', 'forward')
        obj['settings'].setdefault('showGrid', True)
        obj['settings'].setdefault('defaultNodeStyle', {
            'fillColor': '#ffffff',
            'strokeColor': '#5a5a8a',
            'strokeWidth': 1.5,
            'fontSize': 13,
        })
        obj['settings'].setdefault('customColors', [])
        obj.setdefault('nodes', [])
        obj.setdefault('edges', [])
    else:
        now = _now_iso()
        obj = {
            'koreDiag': '1.0',
            'id': str(uuid4()),
            'title': title,
            'created': now,
            'modified': now,
            'settings': {
                'gridSize': 20,
                'defaultArrow': 'forward',
                'showGrid': True,
                'defaultNodeStyle': {
                    'fillColor': '#ffffff',
                    'strokeColor': '#5a5a8a',
                    'strokeWidth': 1.5,
                    'fontSize': 13,
                },
                'customColors': [],
            },
            'nodes': [],
            'edges': [],
        }
    return json.dumps(obj, indent=2)


def create_korediag(
    folder_path: Annotated[str, 'Folder path in KoreFile, such as "/" or "/Projects". Missing folders are created.'],
    name: Annotated[str, 'Filename, with or without the .korediag extension.'],
    diagram: Annotated[Optional[dict], 'Optional partial or complete diagram object. Missing top-level fields are filled in.'] = None,
    title: Annotated[Optional[str], 'Diagram title. Defaults to the filename stem or diagram title.'] = None,
) -> dict:
    """Create a .korediag document from a diagram object, with safe defaults filled in."""
    doc_title = title or (diagram or {}).get('title') or name.rsplit('.', 1)[0]
    content = _diag_content(doc_title, diagram)
    return _create_serialized_file(folder_path, name, 'korediag', content, {'title': doc_title})


@mcp.tool()
def koredocs_diag_create(
    folder_path: Annotated[str, 'Folder path in KoreFile, such as "/" or "/Projects". Missing folders are created.'],
    name: Annotated[str, 'Filename, with or without the .korediag extension.'],
    diagram: Annotated[Optional[dict], 'Optional partial or complete diagram object.'] = None,
    title: Annotated[Optional[str], 'Diagram title.'] = None,
) -> dict:
    """Canonical prefixed alias for create_korediag."""
    return create_korediag(folder_path=folder_path, name=name, diagram=diagram, title=title)


@mcp.tool()
def koredocs_diag_spec_get() -> str:
    """Return a comprehensive description of the .korediag JSON format specification.

    Use this tool before creating or editing a .korediag diagram document to
    understand the full schema, available node and edge types, and style options.
    """
    return """.korediag Document Format Specification (version 1.0)
=====================================================

A .korediag file is a JSON object. All top-level fields:

koreDiag  (string, required)
  Schema version string. Current value: "1.0"

id  (string, required)
  UUID string uniquely identifying the diagram. Generated automatically on create.
  Example: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

title  (string, required)
  Human-readable diagram title displayed in the editor header.

created  (string, required)
  ISO 8601 UTC datetime string when the diagram was first created.
  Example: "2024-01-15T10:30:00Z"

modified  (string, required)
  ISO 8601 UTC datetime string of the last modification.

settings  (object, required)
  Diagram-level display and default style settings:

    gridSize  (integer)
      Grid snap size in pixels. Default: 20

    defaultArrow  ("forward" | "backward" | "both" | "none")
      Default arrow direction applied to new edges when no per-edge override is set.
        forward   – Arrow head points toward to
        backward  – Arrow head points toward from
        both      – Arrow heads at both ends
        none      – No arrow heads (plain connector line)
      Default: "forward"

    showGrid  (boolean)
      Whether the background grid is visible in the editor. Default: true

    defaultNodeStyle  (object)
      Default visual style applied to all new nodes unless overridden per-node:
        fillColor   (string)  Hex background colour. Default: "#ffffff"
        strokeColor (string)  Hex border colour.     Default: "#5a5a8a"
        strokeWidth (number)  Border thickness in pixels. Default: 1.5
        fontSize    (number)  Label text size in points.  Default: 13

    customColors  (array of hex strings)
      User-defined colour palette entries shown in the colour picker.
      Example: ["#ff6b6b", "#4ecdc4", "#45b7d1"]

nodes  (array, required)
  Array of node objects. Each node has:

    id  (string, required)
      Identifier unique within this diagram. Referenced by edge from/to.

    type  ("rect" | "rounded" | "diamond" | "ellipse" | "text" | "image", required)
      Shape type:
        rect     – Plain rectangle
        rounded  – Rectangle with rounded corners
        diamond  – Diamond / rhombus shape (decision node)
        ellipse  – Oval / ellipse (start/end terminal node)
        text     – Label-only node with no visible border
        image    – Image placeholder node

    x  (number, required)
      Horizontal position of the node's top-left corner in diagram units.

    y  (number, required)
      Vertical position of the node's top-left corner in diagram units.

    width  (number, required)
      Node width in diagram units.

    height  (number, required)
      Node height in diagram units.

    label  (string, required)
      Text displayed inside the node. May be an empty string.

    style  (object, optional)
      Per-node style overrides. Accepts the same fields as defaultNodeStyle:
        fillColor, strokeColor, strokeWidth, fontSize
      Only the fields provided are overridden; unspecified fields fall back to
      defaultNodeStyle.

edges  (array, required)
  Array of edge (connector) objects. Each edge has:

    id  (string, required)
      Identifier unique within this diagram.

    from  (string, required)
      id of the source node where the edge originates.

    to  (string, required)
      id of the target node where the edge terminates.

    label  (string, optional)
      Text label displayed near the midpoint of the edge.

    arrow  ("forward" | "backward" | "both" | "none", optional)
      Per-edge arrow direction override. When omitted, settings.defaultArrow is used.

    style  (object, optional)
      Per-edge style overrides:
        strokeColor  (string)   Hex line colour
        strokeWidth  (number)   Line width in pixels
        fontSize     (number)   Label font size in points
        dashed       (boolean)  When true, render the connector as a dashed line

Example minimal diagram JSON:
{
  "koreDiag": "1.0",
  "id": "00000000-0000-0000-0000-000000000001",
  "title": "Simple Flow",
  "created": "2024-01-15T10:00:00Z",
  "modified": "2024-01-15T10:00:00Z",
  "settings": {
    "gridSize": 20,
    "defaultArrow": "forward",
    "showGrid": true,
    "defaultNodeStyle": {
      "fillColor": "#ffffff",
      "strokeColor": "#5a5a8a",
      "strokeWidth": 1.5,
      "fontSize": 13
    },
    "customColors": []
  },
  "nodes": [
    {"id": "n1", "type": "ellipse", "x": 100, "y":  60, "width": 120, "height": 60, "label": "Start"},
    {"id": "n2", "type": "rect",    "x": 100, "y": 180, "width": 120, "height": 60, "label": "Process"},
    {"id": "n3", "type": "diamond", "x": 100, "y": 300, "width": 120, "height": 80, "label": "Decision?"},
    {"id": "n4", "type": "ellipse", "x": 100, "y": 440, "width": 120, "height": 60, "label": "End"}
  ],
  "edges": [
    {"id": "e1", "from": "n1", "to": "n2"},
    {"id": "e2", "from": "n2", "to": "n3"},
    {"id": "e3", "from": "n3", "to": "n4", "label": "Yes"},
    {"id": "e4", "from": "n3", "to": "n2", "label": "No", "style": {"dashed": true}}
  ]
}
"""
