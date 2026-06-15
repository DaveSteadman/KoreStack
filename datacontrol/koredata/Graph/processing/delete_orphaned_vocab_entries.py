"""Delete vocab concepts that are not referenced by any graph relation."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _find_suite_root(script_path: Path) -> Path:
    env_root = os.environ.get("KORE_SUITE_ROOT", "").strip()
    if env_root:
        return Path(env_root).resolve()

    for parent in script_path.parents:
        candidate = parent / "KoreStack"
        if (candidate / "KoreData" / "KoreGraph" / "app").exists():
            return candidate.resolve()

    cwd_candidate = Path.cwd().resolve()
    if (cwd_candidate / "KoreData" / "KoreGraph" / "app").exists():
        return cwd_candidate

    raise RuntimeError("Unable to locate KoreStack suite root for KoreGraph imports")


def _bootstrap_paths() -> None:
    script_path = Path(__file__).resolve()
    suite_root = _find_suite_root(script_path)
    sys.path.insert(0, str(suite_root / "KoreData" / "CommonCode"))
    sys.path.insert(0, str(suite_root / "KoreData" / "KoreGraph"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Delete vocab concepts that have no incoming, outgoing, or predicate relations."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report orphaned concepts without deleting them.",
    )
    args = parser.parse_args()

    _bootstrap_paths()

    from app.database import delete_orphaned_vocab_terms, init_db

    init_db()
    result = delete_orphaned_vocab_terms(dry_run=args.dry_run)

    if args.dry_run:
        print(
            f"Dry run: found {result['orphaned_concepts']} orphaned concepts "
            f"covering {result['orphaned_vocab_terms']} vocab terms."
        )
    else:
        print(
            f"Deleted {result['deleted_concepts']} orphaned concepts "
            f"covering {result['deleted_vocab_terms']} vocab terms."
        )

    if result["sample_terms"]:
        print("Sample concepts:")
        for term in result["sample_terms"]:
            print(f"  - {term}")
    else:
        print("No orphaned concepts found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())