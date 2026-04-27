# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Test results analyzer for MiniAgentFramework.
#
# Reads a test results CSV produced by test_wrapper.py, then for each row parses the associated
# log file to extract structured information about:
#   - Which skill functions the model called during tool rounds
#   - Whether the model used tool calls or answered directly without tools
#   - How many tool rounds were required before a final answer
#   - Whether the run succeeded or failed at validation
#
# Hard-signal failure detection (no LLM required):
#   - Non-zero exit code
#   - Empty final output
#   - Capability gap admissions in the output ("I cannot", "I don't have access", etc.)
#   - Leaked template placeholders in the output
#   - Timeout (exit_code 124)
#
# Produces two output files alongside the source CSV:
#   <source>_analysis.csv   -- one row per prompt with richer diagnostics
#   <source>_gaps.txt       -- summary of inferred missing or weak skill coverage
#
# Usage:
#   python code/testing/test_analyzer.py datacontrol/test_results/test_results_<timestamp>.csv
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "code"))
from KoreAgent.utils.workspace_utils import trunc  # noqa: E402


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
# Phrases in final output that strongly suggest the agent could not satisfy the request
CAPABILITY_GAP_PHRASES = [
    "i cannot",
    "i don't have access",
    "i don't have the ability",
    "i'm unable to",
    "i am unable to",
    "as an ai",
    "as a language model",
    "i have no access",
    "i lack the ability",
    "not able to",
    "cannot access the internet",
    "cannot browse",
    "cannot search",
    "no tools available",
    "i don't have real-time",
    "i cannot retrieve",
]

# Regex to detect un-resolved template placeholders that leaked into the final output
# Matches Jinja-style {{variable}} (paired braces with content) or ${output1} patterns.
# Does NOT match bare }} from LaTeX math like \frac{QK^\top}{\sqrt{d_k}}.
_TEMPLATE_LEAK_RE = re.compile(r"\{\{[^}\n]*\}\}|\$\{output\d+")

# Regex patterns used to parse the structured log sections
_SECTION_RE       = re.compile(r"^={5,}\s*$")
_SECTION_TITLE_RE = re.compile(r"^={5,}[\s\n]*(.*?)[\s\n]*={5,}", re.MULTILINE | re.DOTALL)

# Analysis CSV output fields
ANALYSIS_FIELDS = [
    "timestamp",
    "prompt",
    "exit_code",
    "duration_seconds",
    "final_output_length",
    "outcome",               # PASS / FAIL / TIMEOUT / GAP
    "failure_reason",
    "iterations_used",
    "planner_mode",          # TOOL_CALLS / DIRECT / UNKNOWN
    "skills_selected",       # comma-separated list
    "skills_reasons",        # pipe-separated list of reasons
    "validation_result",     # PASS / FAIL / UNKNOWN
    "final_output_preview",  # first 120 chars of final output
]


# Regex to match tool call function entries in the TOOL CALL SUMMARY section
_TOOL_FUNC_RE = re.compile(r"^([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\(\)", re.MULTILINE)


# ====================================================================================================
# MARK: LOG FILE PARSING
# ====================================================================================================
def _split_log_sections(log_text: str) -> dict[str, str]:
    """Split a log file into a dict mapping section title -> section body text."""
    sections: dict[str, str] = {}
    # Each section is delimited by a line of ===... followed by a title line followed by ===...
    pattern = re.compile(
        r"={5,}\n([^\n]+)\n={5,}\n(.*?)(?=={5,}\n[^\n]+\n={5,}|\Z)",
        re.DOTALL,
    )
    for match in pattern.finditer(log_text):
        title = match.group(1).strip()
        body  = match.group(2).strip()
        sections[title] = body
    return sections


# ----------------------------------------------------------------------------------------------------
def _extract_tool_calls(sections: dict[str, str]) -> tuple[str, str]:
    """Return (skills_selected, skills_reasons) extracted from the TOOL CALL SUMMARY section."""
    for title, body in sections.items():
        if "TOOL CALL SUMMARY" in title:
            matches = _TOOL_FUNC_RE.findall(body)
            if matches:
                return ", ".join(matches), ""
    return "", ""


# ----------------------------------------------------------------------------------------------------
def _count_iterations(sections: dict[str, str]) -> int:
    """Count how many distinct tool rounds appear in the log."""
    round_numbers: set[int] = set()
    for title in sections:
        # Match "TOOL ROUND 1  [HH:MM:SS]" but not "TOOL ROUND 1 - EXECUTION FLOW  [...]"
        m = re.match(r"TOOL ROUND (\d+)(?:\s+\[|\s*$)", title)
        if m:
            round_numbers.add(int(m.group(1)))
    return len(round_numbers) if round_numbers else 1


# ----------------------------------------------------------------------------------------------------
def _detect_planner_mode(sections: dict[str, str]) -> str:
    """Return 'TOOL_CALLS' if the model used tools, 'DIRECT' if it answered without tools."""
    for title in sections:
        # Any TOOL ROUND N section (not the EXECUTION FLOW sub-section) signals tool use.
        if re.match(r"TOOL ROUND \d+", title) and " - EXECUTION FLOW" not in title:
            return "TOOL_CALLS"
    # An ORCHESTRATION RUN section with no tool rounds means the model answered directly.
    if any("ORCHESTRATION RUN" in title for title in sections):
        return "DIRECT"
    return "UNKNOWN"


# ----------------------------------------------------------------------------------------------------
def _extract_validation_result(sections: dict[str, str]) -> str:
    """Return 'PASS', 'FAIL', or 'UNKNOWN' from the last VALIDATION section."""
    result = "UNKNOWN"
    for title, body in sections.items():
        if "VALIDATION" in title:
            low = body.lower()
            if "validation passed" in low or "orchestration succeeded" in low:
                result = "PASS"
            elif "validation" in low:
                result = "FAIL"
    return result


# ----------------------------------------------------------------------------------------------------
def parse_log_file(log_path: Path) -> dict:
    """Parse a run log file and return a dict of extracted diagnostic fields."""
    result = {
        "iterations_used":    1,
        "planner_mode":       "UNKNOWN",
        "skills_selected":    "",
        "skills_reasons":     "",
        "validation_result":  "UNKNOWN",
    }

    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result

    sections = _split_log_sections(log_text)

    result["iterations_used"]   = _count_iterations(sections)
    result["planner_mode"]      = _detect_planner_mode(sections)
    result["validation_result"] = _extract_validation_result(sections)

    skills_selected, skills_reasons = _extract_tool_calls(sections)
    result["skills_selected"] = skills_selected
    result["skills_reasons"]  = skills_reasons

    return result


# ====================================================================================================
# MARK: HARD-SIGNAL FAILURE DETECTION
# ====================================================================================================
def classify_outcome(exit_code: int, final_output: str) -> tuple[str, str]:
    """Return (outcome_label, failure_reason) using only deterministic checks."""
    if exit_code == 124:
        return "TIMEOUT", "Subprocess timed out waiting for response"

    if exit_code not in (0, None, ""):
        try:
            code = int(exit_code)
        except (ValueError, TypeError):
            code = -1
        if code != 0:
            return "FAIL", f"Non-zero exit code: {code}"

    output_lower = final_output.lower().strip()

    if not output_lower:
        return "FAIL", "Empty final output"

    if _TEMPLATE_LEAK_RE.search(final_output):
        return "FAIL", "Unresolved template placeholder leaked into output"

    for phrase in CAPABILITY_GAP_PHRASES:
        if phrase in output_lower:
            return "GAP", f"Capability gap admission detected: '{phrase}'"

    return "PASS", ""


# ====================================================================================================
# MARK: GAP REPORT
# ====================================================================================================
def build_gap_report(analysis_rows: list[dict]) -> str:
    """Produce a plain-text gap/weakness report from the full set of analyzed rows."""
    lines = ["=" * 80, "TEST ANALYSIS - GAP REPORT", "=" * 80, ""]

    total     = len(analysis_rows)
    outcomes  = Counter(r["outcome"] for r in analysis_rows)
    pass_rate = outcomes.get("PASS", 0) / total * 100 if total > 0 else 0.0

    lines.append(f"Total prompts:  {total}")
    lines.append(f"PASS:           {outcomes.get('PASS', 0)}  ({pass_rate:.1f}%)")
    lines.append(f"FAIL:           {outcomes.get('FAIL', 0)}")
    lines.append(f"TIMEOUT:        {outcomes.get('TIMEOUT', 0)}")
    lines.append(f"GAP (admitted): {outcomes.get('GAP', 0)}")
    lines.append("")

    # Tool-calling mode breakdown
    planner_counts = Counter(r["planner_mode"] for r in analysis_rows)
    lines.append("Tool-calling mode breakdown:")
    for mode, count in planner_counts.most_common():
        lines.append(f"  {mode:<12} {count}")
    lines.append("")

    # Tool rounds breakdown
    iter_counts = Counter(r["iterations_used"] for r in analysis_rows)
    lines.append("Tool rounds used:")
    for rounds, count in sorted(iter_counts.items()):
        lines.append(f"  {rounds} round(s): {count} prompt(s)")
    lines.append("")

    # Skill coverage
    skill_counter: Counter = Counter()
    for row in analysis_rows:
        for skill in row["skills_selected"].split(", "):
            if skill.strip():
                skill_counter[skill.strip()] += 1
    if skill_counter:
        lines.append("Skill usage frequency:")
        for skill, count in skill_counter.most_common():
            lines.append(f"  {count:>3}x  {skill}")
        lines.append("")

    # Failing prompts
    failing = [r for r in analysis_rows if r["outcome"] != "PASS"]
    if failing:
        lines.append("-" * 60)
        lines.append("FAILING / GAP PROMPTS:")
        lines.append("-" * 60)
        for row in failing:
            lines.append(f"  [{row['outcome']}]  {row['prompt']!r}")
            if row["failure_reason"]:
                lines.append(f"         Reason: {row['failure_reason']}")
            if row["skills_selected"]:
                lines.append(f"         Skills: {row['skills_selected']}")
        lines.append("")

    # Capability gap hints
    gap_rows = [r for r in analysis_rows if r["outcome"] == "GAP"]
    if gap_rows:
        lines.append("-" * 60)
        lines.append("CAPABILITY GAP SIGNALS (inferred missing skills):")
        lines.append("-" * 60)
        for row in gap_rows:
            lines.append(f"  Prompt:  {row['prompt']!r}")
            lines.append(f"  Reason:  {row['failure_reason']}")
            lines.append(f"  Preview: {row['final_output_preview']!r}")
            lines.append("")

    # Prompts where the model answered without using any tools
    direct_rows = [r for r in analysis_rows if r["planner_mode"] == "DIRECT"]
    if direct_rows:
        lines.append("-" * 60)
        lines.append("PROMPTS ANSWERED WITHOUT TOOL CALLS:")
        lines.append("-" * 60)
        for row in direct_rows:
            lines.append(f"  [{row['outcome']}]  {row['prompt']!r}")
        lines.append("")

    return "\n".join(lines)


# ====================================================================================================
# MARK: ANALYSIS RUNNER
# ====================================================================================================
def analyze_results_file(csv_path: Path) -> tuple[Path, Path]:
    """Analyze a test results CSV. Returns (analysis_csv_path, gap_report_path)."""
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        print(f"No rows found in {csv_path}")
        return csv_path, csv_path

    analysis_rows: list[dict] = []

    for row in rows:
        prompt       = row.get("prompt", "")
        final_output = row.get("final_output", "")
        exit_code    = row.get("exit_code", "0")
        duration     = row.get("duration_seconds", "0")
        timestamp    = row.get("timestamp", "")
        log_file     = row.get("log_file", "").strip()

        try:
            exit_code_int = int(exit_code)
        except (ValueError, TypeError):
            exit_code_int = -1

        assert_result = row.get("assert_result", "").strip().upper()

        outcome, failure_reason = classify_outcome(exit_code_int, final_output)

        log_info = {}
        if log_file:
            log_info = parse_log_file(Path(log_file))

        # If hard-signal says PASS but the assert expression failed, downgrade
        if outcome == "PASS" and assert_result == "FAIL":
            outcome        = "FAIL"
            failure_reason = "Assert expression failed"

        # If hard-signal says PASS but validation in log says FAIL, downgrade
        if outcome == "PASS" and log_info.get("validation_result") == "FAIL":
            outcome        = "FAIL"
            failure_reason = "Orchestration validation failed (from log)"

        preview = trunc(final_output.replace("\n", " ").replace("\r", ""), 120)

        analysis_rows.append({
            "timestamp":           timestamp,
            "prompt":              prompt,
            "exit_code":           exit_code,
            "duration_seconds":    duration,
            "final_output_length": len(final_output),
            "outcome":             outcome,
            "failure_reason":      failure_reason,
            "iterations_used":     log_info.get("iterations_used", 1),
            "planner_mode":        log_info.get("planner_mode", "UNKNOWN"),
            "skills_selected":     log_info.get("skills_selected", ""),
            "skills_reasons":      log_info.get("skills_reasons", ""),
            "validation_result":   log_info.get("validation_result", "UNKNOWN"),
            "final_output_preview": preview,
        })

    # Write analysis CSV
    stem          = csv_path.stem
    analysis_path = csv_path.with_name(f"{stem}_analysis.csv")
    with analysis_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=ANALYSIS_FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(analysis_rows)

    # Write gap report
    gap_report_path = csv_path.with_name(f"{stem}_gaps.txt")
    gap_text        = build_gap_report(analysis_rows)
    gap_report_path.write_text(gap_text, encoding="utf-8")

    return analysis_path, gap_report_path


# ====================================================================================================
# MARK: CONSOLE SUMMARY
# ====================================================================================================
def print_summary(analysis_rows: list[dict], analysis_path: Path, gap_path: Path) -> None:
    total    = len(analysis_rows)
    outcomes = Counter(r["outcome"] for r in analysis_rows)
    pass_pct = outcomes.get("PASS", 0) / total * 100 if total > 0 else 0.0

    print()
    print("=" * 60)
    print("  TEST ANALYSIS SUMMARY")
    print("=" * 60)
    print(f"  Total prompts : {total}")
    print(f"  PASS          : {outcomes.get('PASS', 0)}  ({pass_pct:.1f}%)")
    print(f"  FAIL          : {outcomes.get('FAIL', 0)}")
    print(f"  TIMEOUT       : {outcomes.get('TIMEOUT', 0)}")
    print(f"  GAP           : {outcomes.get('GAP', 0)}")
    print()

    for row in analysis_rows:
        icon = {"PASS": "OK  ", "FAIL": "FAIL", "TIMEOUT": "TIME", "GAP": "GAP "}.get(row["outcome"], "??? ")
        skills = row["skills_selected"] or "(none)"
        print(f"  [{icon}]  {trunc(row['prompt'], 55):<55}  skills: {skills}")
        if row["failure_reason"]:
            print(f"           -> {row['failure_reason']}")

    print()
    print(f"  Analysis CSV : {analysis_path}")
    print(f"  Gap report   : {gap_path}")
    print("=" * 60)
    print()


# ====================================================================================================
# MARK: ENTRYPOINT
# ====================================================================================================
def run_analysis(csv_path: Path) -> None:
    """Public entry point - called from main.py or directly from CLI."""
    if not csv_path.exists():
        print(f"ERROR: CSV file not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Analysing: {csv_path}")

    # Read rows for the console summary (re-parsed after writing to avoid holding two copies)
    source_rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            source_rows.append(row)

    analysis_path, gap_path = analyze_results_file(csv_path)

    # Re-read the written analysis CSV for the summary printer
    analysis_rows = []
    with analysis_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            analysis_rows.append(row)

    print_summary(analysis_rows, analysis_path, gap_path)


# ----------------------------------------------------------------------------------------------------
def _parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyse a MiniAgentFramework test results CSV.")
    parser.add_argument(
        "csv_file",
        type=Path,
        help="Path to the test_results_*.csv file to analyse.",
    )
    return parser.parse_args()


# ----------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    args = _parse_cli_args()
    run_analysis(args.csv_file)
