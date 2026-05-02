# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Session logger that writes timestamped run output to both stdout and a persistent log file.
#
# SessionLogger is used by main.py to record every stage of an orchestration run - tool call rounds,
# tool execution outputs, the final LLM response, and token metrics - so that runs can be reviewed
# after the fact without re-executing. Each session writes to a unique file named with the run timestamp.
#
# Related modules:
#   - main.py  -- creates a SessionLogger instance and logs all orchestration stages through it
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from datetime import datetime
from pathlib import Path
import sys


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
SECTION_SEPARATOR = "=" * 100


# ====================================================================================================
# MARK: LOGGER
# ====================================================================================================
class SessionLogger:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle   = self.file_path.open("a", encoding="utf-8")

    # ----------------------------------------------------------------------------------------------------
    def close(self) -> None:
        self._handle.close()

    def __enter__(self) -> "SessionLogger":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ----------------------------------------------------------------------------------------------------
    def log(self, message: str = "") -> None:
        text = str(message)

        try:
            print(text)
        except UnicodeEncodeError:
            output_encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            safe_text = text.encode(output_encoding, errors="replace").decode(output_encoding, errors="replace")
            print(safe_text)

        self._handle.write(text + "\n")
        self._handle.flush()

    # ----------------------------------------------------------------------------------------------------
    def log_section(self, title: str) -> None:
        stamped = f"{title}  [{datetime.now().strftime('%H:%M:%S')}]"
        self.log("")
        self.log(SECTION_SEPARATOR)
        self.log(stamped)
        self.log(SECTION_SEPARATOR)
        self.log("")

    # ----------------------------------------------------------------------------------------------------
    def log_file_only(self, message: str = "") -> None:
        """Write to the log file only - no stdout. Used for verbose orchestration detail in chat mode."""
        text = str(message)
        self._handle.write(text + "\n")
        self._handle.flush()

    # ----------------------------------------------------------------------------------------------------
    def log_section_file_only(self, title: str) -> None:
        """Write a section header to the log file only - no stdout."""
        stamped = f"{title}  [{datetime.now().strftime('%H:%M:%S')}]"
        self.log_file_only("")
        self.log_file_only(SECTION_SEPARATOR)
        self.log_file_only(stamped)
        self.log_file_only(SECTION_SEPARATOR)
        self.log_file_only("")


# ====================================================================================================
# MARK: HELPERS
# ====================================================================================================
def create_log_file_path(log_dir: Path) -> Path:
    # Organise logs into YYYY-MM-DD dated subfolders to keep the logs root manageable.
    now      = datetime.now()
    date_dir = log_dir / now.strftime("%Y-%m-%d")
    return date_dir / f"run_{now.strftime('%Y%m%d_%H%M%S')}.txt"
