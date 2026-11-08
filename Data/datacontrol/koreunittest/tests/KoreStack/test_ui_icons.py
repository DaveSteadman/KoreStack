from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(os.environ["KORESTACK_ROOT"])
UI = ROOT / "KoreUI"
ICONS = UI / "UIElements" / "assets" / "icons"


class UIIconTests(unittest.TestCase):
    def test_ui_icon_references_exist_and_use_approved_sources(self):
        references = set()
        for path in UI.rglob("*"):
            if path.suffix not in {".js", ".html", ".css"}:
                continue
            text = path.read_text(encoding="utf-8-sig")
            references.update(re.findall(
                r"/ui-elements/assets/icons/([A-Za-z0-9-]+)/([A-Za-z0-9-]+\.svg)", text,
            ))
            references.update(("dazzle-line", name + ".svg") for name in re.findall(
                r"""['"]([a-z0-9-]+-svgrepo-com)['"]""", text,
            ))
        self.assertTrue(references)
        for pack, filename in sorted(references):
            with self.subTest(pack=pack, filename=filename):
                self.assertTrue(pack == "dazzle-line" or
                                (pack, filename) == ("Gentlecons", "hand-svgrepo-com.svg"))
                path = ICONS / pack / filename
                self.assertTrue(path.is_file(), str(path))
                self.assertEqual("{http://www.w3.org/2000/svg}svg", ElementTree.parse(path).getroot().tag)

    def test_dazzle_manifest_matches_assets_and_documents_source(self):
        path = ICONS / "dazzle-line" / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        filenames = [icon["file"] for icon in manifest["icons"]]
        self.assertEqual(len(filenames), len(set(filenames)))
        self.assertEqual(manifest["count"], len(filenames))
        self.assertEqual({file.name for file in path.parent.glob("*.svg")}, set(filenames))
        self.assertEqual("https://www.svgrepo.com/collection/dazzle-line-icons/", manifest["collection"])

    def test_pan_uses_approved_hand_and_landing_actions_use_direct_icons(self):
        diagram = (UI / "KoreDocs" / "static" / "diag" / "index.html").read_text(encoding="utf-8")
        self.assertIn("/ui-elements/assets/icons/Gentlecons/hand-svgrepo-com.svg", diagram)
        self.assertNotIn("<svg", diagram)
        landing = (UI / "KoreStack" / "static" / "stack" / "index.html").read_text(encoding="utf-8")
        for action in ("start", "stop", "restart"):
            self.assertRegex(landing, rf'<button[^>]+data-action="{action}"[^>]+data-action-icon="{action}"[^>]*></button>')
        self.assertNotIn("service-action-icon", landing)
