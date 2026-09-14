# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Retention policy for KoreTest result artefacts.
#
# Each run can produce a CSV, Markdown summary, and optional gap report.  The
# policy preserves the most recent runs per suite while enforcing an absolute
# maximum age, keeping artefacts for the same run together.
# ====================================================================================================

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


RESULT_RETENTION_DAYS       = 60
RESULT_RETENTION_RUN_LIMIT  = 10

_ARTEFACT_NAME = re.compile(
    r"^(?:test_results|summary)_(?P<timestamp>\d{8}_\d{6})_(?P<suite>.+?)(?P<gaps>_gaps)?\.(?:csv|md|txt)$"
)


def prune_test_results(
    result_root: Path,
    *,
    now: datetime | None = None,
    retention_days: int = RESULT_RETENTION_DAYS,
    run_limit: int = RESULT_RETENTION_RUN_LIMIT,
    dry_run: bool = False,
) -> dict:
    """Prune old and superseded KoreTest result artefacts below *result_root*.

    A run is identified by its timestamp and suite suffix.  A run is removed
    when it is older than ``retention_days`` or falls outside the newest
    ``run_limit`` runs for its suite.  Files outside the recognised result
    naming convention are never touched.
    """
    if retention_days < 0:
        raise ValueError("retention_days must be zero or greater")
    if run_limit < 1:
        raise ValueError("run_limit must be at least one")

    root = Path(result_root).resolve()
    if not root.exists():
        return _report(root, [], bytes_reclaimed=0, dry_run=dry_run)
    if not root.is_dir():
        raise ValueError(f"KoreTest result root is not a directory: {root}")

    current_time = now or datetime.now()
    cutoff       = current_time - timedelta(days=retention_days)
    runs: dict[str, dict[datetime, list[Path]]] = defaultdict(lambda: defaultdict(list))

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        match = _ARTEFACT_NAME.match(path.name)
        if match is None:
            continue
        try:
            run_at = datetime.strptime(match.group("timestamp"), "%Y%m%d_%H%M%S")
        except ValueError:
            continue
        suite = match.group("suite")
        if match.group("gaps"):
            suite = suite.removesuffix("_gaps")
        suite = suite.removesuffix("_analysis")
        runs[suite][run_at].append(path)

    delete_paths: set[Path] = set()
    for suite_runs in runs.values():
        newest_first = sorted(suite_runs, reverse=True)
        retained     = set(newest_first[:run_limit])
        for run_at, paths in suite_runs.items():
            if run_at < cutoff or run_at not in retained:
                delete_paths.update(paths)

    ordered_paths = sorted(delete_paths)
    bytes_reclaimed = sum(path.stat().st_size for path in ordered_paths if path.exists())
    if not dry_run:
        for path in ordered_paths:
            path.unlink(missing_ok=True)
        _remove_empty_date_directories(root)

    return _report(root, ordered_paths, bytes_reclaimed=bytes_reclaimed, dry_run=dry_run)


def _remove_empty_date_directories(root: Path) -> None:
    """Remove empty dated result folders without touching arbitrary directories."""
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name):
            continue
        try:
            path.rmdir()
        except OSError:
            continue


def _report(root: Path, paths: list[Path], *, bytes_reclaimed: int, dry_run: bool) -> dict:
    return {
        "root":          str(root),
        "deleted":       len(paths),
        "bytes_reclaimed": bytes_reclaimed,
        "dry_run":       dry_run,
        "paths":         [str(path) for path in paths],
    }
