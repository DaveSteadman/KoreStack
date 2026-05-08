#!/usr/bin/env python3
"""Import an SVG icon pack zip into UIElements/SVGicons/<set-name>.

Usage:
  python tools/import_icon_pack.py --zip <path-to-zip> --set dazzle-line

The script:
- Extracts .svg files recursively from the zip
- Normalizes names to kebab-case
- Deduplicates collisions with numeric suffixes
- Writes source files to UIElements/SVGicons/<set-name>
- Mirrors files to UIElements/assets/icons/<set-name> for browser access
- Generates manifest.json with icon names and source paths in both locations
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ICON_SOURCE_ROOT = ROOT / "UIElements" / "SVGicons"
ICON_PUBLIC_ROOT = ROOT / "UIElements" / "assets" / "icons"


def normalize_name(raw_name: str) -> str:
    base = Path(raw_name).stem.strip().lower()
    base = re.sub(r"[^a-z0-9_-]+", "-", base)
    base = re.sub(r"-+", "-", base).strip("-")
    return base or "icon"


def ensure_unique(name: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    i = 2
    while True:
        candidate = f"{name}-{i}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        i += 1


def import_pack(zip_path: Path, set_name: str) -> dict:
    source_dir = ICON_SOURCE_ROOT / set_name
    public_dir = ICON_PUBLIC_ROOT / set_name
    source_dir.mkdir(parents=True, exist_ok=True)
    public_dir.mkdir(parents=True, exist_ok=True)

    used_names = {p.stem.lower() for p in source_dir.glob("*.svg")}
    imported = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        entries = [e for e in zf.infolist() if not e.is_dir() and e.filename.lower().endswith(".svg")]
        for entry in entries:
            raw = zf.read(entry.filename)
            name = ensure_unique(normalize_name(entry.filename), used_names)
            source_path = source_dir / f"{name}.svg"
            public_path = public_dir / f"{name}.svg"
            source_path.write_bytes(raw)
            public_path.write_bytes(raw)
            imported.append({
                "name": name,
                "file": source_path.name,
                "source": entry.filename,
            })

    manifest = {
        "set": set_name,
        "count": len(imported),
        "icons": sorted(imported, key=lambda i: i["name"]),
    }
    manifest_text = json.dumps(manifest, indent=2)
    (source_dir / "manifest.json").write_text(manifest_text, encoding="utf-8")
    (public_dir / "manifest.json").write_text(manifest_text, encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Import SVG icon zip into UIElements SVGicons + public assets")
    parser.add_argument("--zip", dest="zip_path", required=True, help="Path to downloaded icon zip")
    parser.add_argument("--set", dest="set_name", default="dazzle-line", help="Target icon set folder name")
    args = parser.parse_args()

    zip_path = Path(args.zip_path)
    if not zip_path.exists():
        raise SystemExit(f"Zip file not found: {zip_path}")

    manifest = import_pack(zip_path, args.set_name.strip().lower())
    print(f"Imported {manifest['count']} icons into UIElements/SVGicons/{manifest['set']}")
    print("Source manifest:", ICON_SOURCE_ROOT / manifest["set"] / "manifest.json")
    print("Public manifest:", ICON_PUBLIC_ROOT / manifest["set"] / "manifest.json")


if __name__ == "__main__":
    main()
