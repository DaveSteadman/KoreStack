# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# SystemInfo skill module for the MiniAgentFramework.
#
# Provides a callable tool function the model can invoke when a user prompt requires
# information about the runtime environment:
#   - get_system_info_dict()    -- returns OS, Python/Ollama versions, RAM and disk usage as a
#                                  structured dict.
#
# get_system_info_string() is an internal formatting helper (not a callable tool) used by
# orchestration.py to inject system context into every prompt and by the FileAccess shortcut.
#
# This module is also imported directly by main.py to inject system info as ambient prompt context
# on every orchestration turn, guaranteeing that any prompt touching hardware or runtime state
# receives accurate data even when the model does not explicitly call this tool.
#
# This module is discovered automatically by skills_catalog_builder.py via the accompanying
# skill.md definition file and added to the skills_summary.md catalog.
#
# Related modules:
#   - skill_executor.py         -- dynamically imports and calls functions from this module
#   - skills_catalog_builder.py -- reads skill.md to build the catalog entry for this skill
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import datetime
import os
import re
import shutil
import subprocess
import sys
import platform
from pathlib import Path

from KoreAgent.utils.version import __version__ as _FRAMEWORK_VERSION

if sys.platform.startswith("win"):
    import ctypes


# ====================================================================================================
# MARK: HELPERS
# ====================================================================================================
def _get_python_version() -> str:
    return sys.version.split()[0]


# ----------------------------------------------------------------------------------------------------
def _get_os_name() -> str:
    platform_name = platform.system().strip().lower()
    os_name_map = {
        "windows": "Windows",
        "linux": "Linux",
        "darwin": "macOS",
    }
    if platform_name in os_name_map:
        return os_name_map[platform_name]

    low_level_name = os.name.strip().lower()
    os_name_fallback_map = {
        "nt": "Windows",
        "posix": "Linux",
        "java": "Java",
    }
    if low_level_name in os_name_fallback_map:
        return os_name_fallback_map[low_level_name]

    return "unknown"


# ----------------------------------------------------------------------------------------------------
def _get_ollama_version() -> str:
    try:
        result = subprocess.run(["ollama", "--version"], capture_output=True, text=True, check=False)
        raw_output = f"{result.stdout} {result.stderr}".strip()
        if result.returncode != 0:
            return "unknown"

        match = re.search(r"(\d+\.\d+\.\d+)", raw_output)
        if match:
            return match.group(1)

        return raw_output or "unknown"
    except Exception:
        return "unknown"


# ----------------------------------------------------------------------------------------------------
def _format_bytes(num_bytes: int) -> str:
    units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]
    size  = float(max(num_bytes, 0))

    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024.0

    return "0 B"


# ----------------------------------------------------------------------------------------------------
def _get_memory_usage_bytes() -> tuple[int, int] | tuple[None, None]:
    if sys.platform.startswith("win"):
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        memory_status           = MEMORYSTATUSEX()
        memory_status.dwLength  = ctypes.sizeof(MEMORYSTATUSEX)
        call_success            = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory_status))
        if call_success:
            total_bytes     = int(memory_status.ullTotalPhys)
            available_bytes = int(memory_status.ullAvailPhys)
            used_bytes      = max(total_bytes - available_bytes, 0)
            return used_bytes, available_bytes

    return None, None


# ----------------------------------------------------------------------------------------------------
def _get_disk_usage_bytes() -> tuple[int, int] | tuple[None, None]:
    try:
        current_path    = Path.cwd()
        disk_usage      = shutil.disk_usage(current_path)
        used_bytes      = int(disk_usage.used)
        available_bytes = int(disk_usage.free)
        return used_bytes, available_bytes
    except Exception:
        return None, None


# ====================================================================================================
# MARK: PUBLIC SKILL API
# ====================================================================================================
def get_system_info_dict() -> dict:
    """Return system information as a structured dict with individually addressable fields.

    Keys and types:
      os               (str)   - OS name, e.g. "Windows"
      python_version   (str)   - Python version string, e.g. "3.10.11"
      ollama_version   (str)   - Ollama version string, e.g. "0.18.0"
      ram_used_gb      (float) - RAM in use, GiB rounded to 2 dp
      ram_available_gb (float) - RAM free, GiB rounded to 2 dp
      disk_used_gb     (float) - Disk used, GiB rounded to 2 dp
      disk_available_gb(float) - Disk free, GiB rounded to 2 dp

    Reference individual fields e.g. result["ram_available_gb"] downstream.
    """
    ram_used_bytes, ram_available_bytes   = _get_memory_usage_bytes()
    disk_used_bytes, disk_available_bytes = _get_disk_usage_bytes()

    def _to_gb(b: int | None) -> float:
        return round(b / (1024 ** 3), 2) if b is not None else 0.0

    return {
        "framework_version": _FRAMEWORK_VERSION,
        "os":                _get_os_name(),
        "python_version":    _get_python_version(),
        "ollama_version":    _get_ollama_version(),
        "ram_used_gb":       _to_gb(ram_used_bytes),
        "ram_available_gb":  _to_gb(ram_available_bytes),
        "disk_used_gb":      _to_gb(disk_used_bytes),
        "disk_available_gb": _to_gb(disk_available_bytes),
    }


# ----------------------------------------------------------------------------------------------------

# return the full system infor string

def get_system_info_string() -> str:
    """Format system info as a human-readable string for ambient prompt context and logging.

    Internal helper - not exposed as a callable tool.  Use get_system_info_dict() for tool-call access.
    """
    d = get_system_info_dict()
    return (
        f"System info: framework={d['framework_version']}; os={d['os']}; python={d['python_version']}; ollama={d['ollama_version']}; "
        f"ram_used={d['ram_used_gb']} GiB; ram_available={d['ram_available_gb']} GiB; "
        f"disk_used={d['disk_used_gb']} GiB; disk_available={d['disk_available_gb']} GiB"
    )

# return the static system info string, that won't change with RAM use etc

def get_static_system_info_string() -> str:
    """Format static system info (OS and versions) as a human-readable string for ambient prompt context and logging.

    Internal helper - not exposed as a callable tool.  Use get_system_info_dict() for tool-call access.
    """
    d    = get_system_info_dict()
    date = datetime.date.today().isoformat()
    return (
        f"System info: framework={d['framework_version']}; os={d['os']}; python={d['python_version']}; ollama={d['ollama_version']}; date={date}"
    )