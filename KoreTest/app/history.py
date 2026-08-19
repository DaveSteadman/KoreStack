# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# history module. This file groups related implementation behind a focused module boundary;
# callers use its types and functions instead of duplicating its local policy or mechanics.
# MARK: FUNCTIONS
# Function inventory:
# - row_outcome: Implements the row outcome operation for this module.
# - result_counts: Implements the result counts operation for this module.
# ====================================================================================================

"""Result-history helpers owned by KoreTest."""
from pathlib import Path
import re


def row_outcome(row: dict) -> str:
    value = str(row.get("passed", "")).strip().upper()
    if value in {"PASS", "TRUE", "1", "YES", "OK"}:
        return "PASS"
    if value in {"FAIL", "FALSE", "0", "NO"} or str(row.get("failure_reason", "")).strip():
        return "FAIL"
    if str(row.get("assert_result", "")).strip().upper() == "FAIL":
        return "FAIL"
    try:
        exit_code = int(row.get("exit_code", "0"))
    except (TypeError, ValueError):
        exit_code = -1
    return "FAIL" if exit_code or not str(row.get("final_output", "")).strip() else "PASS"


def result_counts(rows: list[dict], csv_path: Path) -> tuple[int, int, int, int]:
    persisted = any(str(row.get("passed", "")).strip() or str(row.get("failure_reason", "")).strip() for row in rows)
    if not persisted:
        summary = csv_path.with_name(csv_path.stem.replace("test_results", "summary", 1) + ".md")
        try:
            match = re.search(r"Passed:\\s+\\*\\*(\\d+)/(\\d+)\\*\\*", summary.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            match = None
        if match:
            passed, total = map(int, match.groups())
            return total, passed, total - passed, 0
    outcomes = [row_outcome(row) for row in rows]
    return len(outcomes), outcomes.count("PASS"), outcomes.count("FAIL"), outcomes.count("GAP")
