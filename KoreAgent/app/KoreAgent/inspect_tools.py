# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Standalone CLI tool that shows the tool definitions derived from the current skills catalog.
#
# Loads the runtime skills_catalog.json catalog and prints the JSON Schema tool definitions that are sent to
# the model via /v1/chat/completions. Useful for debugging which tools are visible to the model and
# verifying that skill signatures are parsed correctly.
#
# Usage:
#   python inspect_tools.py
#   python inspect_tools.py --skills-catalog /path/to/skills_catalog.json
#   python inspect_tools.py --output /path/to/tool_definitions.json
#
# Related modules:
#   - skills_catalog_builder.py  -- catalog loading and tool definition building
#   - orchestration.py           -- uses build_tool_definitions at runtime
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import argparse
import json
from pathlib import Path

from KoreAgent.skills_catalog_builder import build_tool_definitions
from KoreAgent.skills_catalog_builder import load_skills_payload


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
DEFAULT_SKILLS_CATALOG = Path(__file__).resolve().parent / "skills" / "skills_catalog.json"


# ====================================================================================================
# MARK: CLI
# ====================================================================================================
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show tool definitions derived from the skills catalog.")
    parser.add_argument("--skills-catalog", default=str(DEFAULT_SKILLS_CATALOG), help="Path to skills_catalog.json file.")
    parser.add_argument("--output", default=None, help="Optional path to write tool definitions JSON.")
    return parser.parse_args()


# ----------------------------------------------------------------------------------------------------
def main() -> None:
    args = parse_args()

    skills_catalog_path = Path(args.skills_catalog).resolve()
    skills_payload = load_skills_payload(skills_catalog_path)
    tool_defs = build_tool_definitions(skills_payload)

    output_text = json.dumps(tool_defs, indent=2)

    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output_text, encoding="utf-8")
        print(f"Wrote {len(tool_defs)} tool definitions: {output_path.as_posix()}")
    else:
        print(f"# {len(tool_defs)} tool definitions from {skills_catalog_path.name}\n")
        print(output_text)


# ----------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    main()
