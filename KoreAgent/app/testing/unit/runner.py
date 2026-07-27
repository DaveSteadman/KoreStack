"""Fast deterministic checks used by the KoreAgent ``/unittest`` command.

This intentionally complements the prompt-driven system-test runner.  It does
not call a model, start services, or write test-result CSV files.
"""

from __future__ import annotations

import compileall
import shutil
import subprocess
import sys
from pathlib import Path


APP_DIR   = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]

_AGENT_SMOKE_TARGETS = (
    "testing.unit.test_guardrail_smoke.GuardrailSmokeTests.test_test_wrapper_extracts_delegate2_log_evidence",
    "testing.unit.test_guardrail_smoke.GuardrailSmokeTests.test_test_wrapper_fails_single_prompt_on_no_results_output",
    "testing.unit.test_guardrail_smoke.GuardrailSmokeTests.test_test_wrapper_fails_exchange_on_search_failure_output",
    "testing.unit.test_guardrail_smoke.GuardrailSmokeTests.test_queue_timeout_for_prompt_disables_scheduler_timeout_only_for_test",
    "testing.unit.test_guardrail_smoke.GuardrailSmokeTests.test_slash_command_outputs_use_ascii_arrows",
    "testing.unit.test_guardrail_data.GuardrailDataTests.test_koreconv_prompt_renders_datasets_separately",
    "testing.unit.test_guardrail_data.GuardrailDataTests.test_koreconv_event_restores_datasets_before_orchestration",
)


def _run(command: list[str], *, cwd: Path) -> bool:
    print(f"[UNITTEST] {' '.join(command)}")
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, encoding="utf-8")
    output = (completed.stdout + completed.stderr).strip()
    if output:
        print(output)
    return completed.returncode == 0


def run_core_checks() -> bool:
    """Run compilation, correctness linting, and high-value local regression tests."""
    passed = total = 0

    total += 1
    compiled = compileall.compile_dir(REPO_ROOT, quiet=1)
    print("[UNITTEST] Python compilation " + ("passed" if compiled else "failed"))
    passed += int(compiled)

    ruff = shutil.which("ruff")
    if ruff:
        total += 1
        passed += int(
            _run(
                [
                    ruff,
                    "check",
                    "--select",
                    "F811,F821",
                    "KoreCommon/service_logging.py",
                    "KoreCode/app/server.py",
                    "KoreData/KoreDataGateway/app",
                    "KoreAgent/app/input_layer/slash_command_handlers_testing.py",
                ],
                cwd=REPO_ROOT,
            )
        )
    else:
        print("[UNITTEST] Ruff unavailable; correctness lint check skipped")

    checks = (
        (
            [
                sys.executable,
                "-m",
                "unittest",
                "testing.unit.test_slash_command_registry",
                "testing.unit.test_service_logging",
                "testing.unit.test_koreconv_input",
                "testing.unit.test_suite_config_loader",
                "testing.unit.test_task_conversations",
            ],
            APP_DIR,
        ),
        ([sys.executable, "-m", "unittest", *_AGENT_SMOKE_TARGETS], APP_DIR),
        ([sys.executable, "-m", "unittest", "KoreCode.app.testing.test_run_executor"], REPO_ROOT),
        ([sys.executable, "KoreData/KoreDataGateway/app/test_server_artifact_refs.py"], REPO_ROOT),
        ([sys.executable, "KoreData/KoreDataGateway/app/test_gateway_library_write.py"], REPO_ROOT),
    )
    for command, cwd in checks:
        total += 1
        passed += int(_run(command, cwd=cwd))

    print(f"[UNITTEST COMPLETE] passed={passed} total={total}")
    return passed == total


if __name__ == "__main__":
    raise SystemExit(0 if run_core_checks() else 1)
