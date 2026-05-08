from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

HTML_JS_GLOB = ["**/*.html", "**/*.js"]
CSS_GLOB = ["**/*.css"]

EXCLUDE_PARTS = {
    "node_modules",
    ".git",
    "__pycache__",
    "logs",
}

CHROME_IMPORT_RE = re.compile(r"/ui-elements/assets/js/chrome\.js\?v=([A-Za-z0-9._-]+)")
DIRECT_UI_IMPORT_RE = re.compile(r"/ui-elements/assets/js/(topbar|appbar|appMenu)\.js(?:\?v=[^'\"]+)?")
INLINE_EVENT_RE = re.compile(r"\son[a-z]+\s*=")
SHELL_TOKEN_OVERRIDE_RE = re.compile(
    r"--(bg|bg-2|surface|surface-2|panel|border|border-2|text|text-2|text-dim|accent|accent-2|success|warning|danger|info)\s*:",
    re.IGNORECASE,
)


def parse_args(argv: list[str]) -> tuple[bool]:
    strict = "--strict" in argv
    return (strict,)


def is_excluded(path: Path) -> bool:
    return any(part in EXCLUDE_PARTS for part in path.parts)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def collect_files(globs: list[str]) -> list[Path]:
    files: list[Path] = []
    for pattern in globs:
        for p in ROOT.glob(pattern):
            if not p.is_file():
                continue
            if is_excluded(p):
                continue
            files.append(p)
    return files


def main(argv: list[str] | None = None) -> int:
    strict, = parse_args(argv or sys.argv[1:])
    files = collect_files(HTML_JS_GLOB)
    css_files = collect_files(CSS_GLOB)
    versions: dict[str, list[str]] = {}
    direct_import_hits: list[str] = []
    inline_handler_hits: list[str] = []
    shell_token_override_hits: list[str] = []

    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        text = read_text(path)
        if not text:
            continue

        for m in CHROME_IMPORT_RE.finditer(text):
            version = m.group(1)
            versions.setdefault(version, []).append(rel)

        if DIRECT_UI_IMPORT_RE.search(text):
            direct_import_hits.append(rel)

        if path.suffix == ".html" and INLINE_EVENT_RE.search(text):
            inline_handler_hits.append(rel)

    for path in css_files:
        rel = path.relative_to(ROOT).as_posix()
        text = read_text(path)
        if not text:
            continue
        if rel.lower().startswith("uielements/"):
            continue
        if SHELL_TOKEN_OVERRIDE_RE.search(text):
            shell_token_override_hits.append(rel)

    print("Web Consistency Report")
    print("======================")

    if versions:
        print("\\nchrome.js version usage:")
        for version, refs in sorted(versions.items()):
            print(f"  - {version}: {len(refs)} files")
    else:
        print("\\nNo chrome.js versioned imports found.")

    exit_code = 0

    if len(versions) > 1:
        print("\\nFAIL: Multiple chrome.js version stamps found.")
        for version, refs in sorted(versions.items()):
            print(f"  {version}")
            for rel in refs:
                print(f"    {rel}")
        exit_code = 1

    if direct_import_hits:
        print("\\nFAIL: Direct UIElements imports found (use chrome.js bundle instead):")
        for rel in sorted(set(direct_import_hits)):
            print(f"  {rel}")
        exit_code = 1

    if inline_handler_hits:
        level = "FAIL" if strict else "WARN"
        print(f"\n{level}: Inline HTML event handlers found (migrate to addEventListener):")
        for rel in sorted(set(inline_handler_hits)):
            print(f"  {rel}")
        if strict:
            exit_code = 1

    if shell_token_override_hits:
        level = "FAIL" if strict else "WARN"
        print(f"\n{level}: Service CSS overrides shared shell tokens (prefer aliases/custom locals):")
        for rel in sorted(set(shell_token_override_hits)):
            print(f"  {rel}")
        if strict:
            exit_code = 1

    if exit_code == 0:
        print("\\nPASS: Core consistency checks passed.")

    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
