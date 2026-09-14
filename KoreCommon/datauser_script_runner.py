from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from KoreCommon.suite_paths import get_suite_datauser_dir
from KoreCommon.suite_paths import get_suite_root
from KoreCommon.suite_paths import get_suite_urls_map


DEFAULT_TIMEOUT_SECONDS = 1800
MAX_CAPTURED_CHARS      = 12_000


class DataUserScriptError(RuntimeError):
    """A requested datauser script is invalid or did not complete successfully."""


@dataclass(frozen=True)
class DataUserScriptResult:
    script:    Path
    exit_code: int
    output:    str


def _trim_output(value: str) -> str:
    text = value.strip()
    if len(text) <= MAX_CAPTURED_CHARS:
        return text
    return text[:MAX_CAPTURED_CHARS] + "\n[output truncated]"


def resolve_datauser_script(command: str, *, datauser_dir: Path | None = None) -> tuple[Path, list[str]]:
    """Resolve a trusted Python script below ``datauser/scripts`` plus its arguments."""
    try:
        tokens = shlex.split(command, posix=False)
    except ValueError as exc:
        raise DataUserScriptError(f"Invalid /run command: {exc}") from exc
    if not tokens:
        raise DataUserScriptError("Usage: /run ./scripts/<script>.py [arguments]")

    datauser  = (datauser_dir or get_suite_datauser_dir()).resolve()
    scripts   = (datauser / "scripts").resolve()
    supplied  = Path(tokens[0].replace("\\", "/"))
    candidate = (datauser / supplied).resolve() if not supplied.is_absolute() else supplied.resolve()
    try:
        candidate.relative_to(scripts)
    except ValueError as exc:
        raise DataUserScriptError("/run may execute only Python files below datauser/scripts.") from exc
    if candidate.suffix.lower() != ".py":
        raise DataUserScriptError("/run accepts Python (.py) files only.")
    if not candidate.is_file():
        raise DataUserScriptError(f"Script not found: {candidate.relative_to(datauser).as_posix()}")
    return candidate, tokens[1:]


def run_datauser_script(
    command: str,
    *,
    datauser_dir: Path | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> DataUserScriptResult:
    """Run one trusted datauser script in the current Python environment."""
    script, arguments = resolve_datauser_script(command, datauser_dir=datauser_dir)
    datauser          = (datauser_dir or get_suite_datauser_dir()).resolve()
    urls              = get_suite_urls_map()
    environment       = os.environ.copy()
    environment.update({
        "KORE_RUN_DATAUSER":   str(datauser),
        "KORE_RUN_SUITE_ROOT": str(get_suite_root()),
        "KORE_KOREDATA_URL":   str(urls.get("koredatagateway") or urls.get("koredata") or ""),
        "KORE_KORECOMMS_URL":  str(urls.get("korecomms") or ""),
    })
    try:
        process = subprocess.run(
            [sys.executable, str(script), *arguments],
            cwd     = datauser,
            env     = environment,
            text    = True,
            stdout  = subprocess.PIPE,
            stderr  = subprocess.STDOUT,
            timeout = timeout_seconds,
            check   = False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DataUserScriptError(f"Script timed out after {timeout_seconds}s: {script.name}") from exc
    except OSError as exc:
        raise DataUserScriptError(f"Could not start {script.name}: {exc}") from exc

    output = _trim_output(process.stdout or "")
    if process.returncode != 0:
        detail = f"\n{output}" if output else ""
        raise DataUserScriptError(f"{script.name} exited with code {process.returncode}.{detail}")
    return DataUserScriptResult(script=script, exit_code=process.returncode, output=output)
