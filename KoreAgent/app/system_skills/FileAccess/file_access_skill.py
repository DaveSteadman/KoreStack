# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FileAccess skill module for KoreAgent.
#
# Provides safe file read/write/append operations constrained to the shared datauser root, with
# sensible defaults for relative paths.
#
# Path behavior:
#   - bare file name, relative path, or ./relative path resolves under datauser/
#   - paths that already begin with legacy prefixes like "data/", "datauser/", or "KoreDocs/"
#     are accepted and normalized
#   - absolute paths are allowed only when they resolve inside the datauser directory
# MARK: FUNCTIONS
# Function inventory:
# - _suspicious_document_write_reason: Implements the  suspicious document write reason operation for this module.
# - file_write: Implements the file write operation for this module.
# - file_append: Implements the file append operation for this module.
# - file_read: Implements the file read operation for this module.
# - _normalise_keywords: Implements the  normalise keywords operation for this module.
# - _normalise_find_arguments: Implements the  normalise find arguments operation for this module.
# - file_find: Implements the file find operation for this module.
# - folder_find: Implements the folder find operation for this module.
# - folder_create: Implements the folder create operation for this module.
# - folder_exists: Implements the folder exists operation for this module.
# - file_write_from_scratchpad: Implements the file write from scratchpad operation for this module.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import re
from pathlib import Path

from KoreCommon.datauser_fs import DataUserPathError
from KoreCommon.datauser_fs import create_folder as create_datauser_folder
from KoreCommon.datauser_fs import display_datauser_path
from KoreCommon.datauser_fs import list_datauser_files
from KoreCommon.datauser_fs import list_datauser_folders
from KoreCommon.datauser_fs import read_text_file
from KoreCommon.datauser_fs import resolve_datauser_directory
from KoreCommon.datauser_fs import resolve_datauser_path
from KoreCommon.datauser_fs import write_text_file


# ====================================================================================================
# MARK: CONSTANTS
# ====================================================================================================
_REMAINING_RECORDS_RE = re.compile(r"\bremaining\s+\d+\s+records?\b", re.IGNORECASE)
_SAME_SCHEMA_RE = re.compile(r"\bfollow\s+the\s+same\s+schema\b|\bsame\s+schema\b", re.IGNORECASE)
_SAMPLE_SNIPPET_RE = re.compile(r"\bsample\s+snippet\b|\bexample\s+snippet\b", re.IGNORECASE)
_PLACEHOLDER_RE = re.compile(r"\bplaceholder\b", re.IGNORECASE)



def _suspicious_document_write_reason(target_path: Path, content: str) -> str:
    if target_path.suffix.lower() != ".koredoc":
        return ""

    text = str(content or "")
    lowered = text.lower()
    record_block_like = "## record " in lowered or "### record " in lowered

    if _REMAINING_RECORDS_RE.search(text):
        return "contains a remaining-records summary instead of full output"
    if _SAME_SCHEMA_RE.search(text):
        return "contains a same-schema summary instead of full output"
    if record_block_like and _SAMPLE_SNIPPET_RE.search(text):
        return "contains sample snippet placeholder text"
    if record_block_like and _PLACEHOLDER_RE.search(text):
        return "contains placeholder text"
    return ""


# ====================================================================================================
# MARK: PUBLIC SKILL API
# ====================================================================================================

# ----------------------------------------------------------------------------------------------------
def file_write(path: str, content: str, skip_content_guard: bool = False) -> str:
    """Overwrite one datauser-relative text file. Call only when a file write was requested."""
    try:
        target_path = resolve_datauser_path(path)
    except DataUserPathError as err:
        return f"Error: {err}"
    text_to_write = str(content)
    if not skip_content_guard:
        reason = _suspicious_document_write_reason(target_path, text_to_write)
        if reason:
            return (
                f"Error: refusing to write suspicious placeholder content to {display_datauser_path(target_path)}; {reason}. "
                "Use dataset_write_koredoc or retrieve the real dataset records first."
            )
    target_path = write_text_file(target_path, text_to_write, ensure_trailing_newline=True)
    return f"Wrote {display_datauser_path(target_path)}"


# ----------------------------------------------------------------------------------------------------
def file_append(path: str, content: str) -> str:
    """Append text to one datauser-relative file. Call only when an append was requested."""
    try:
        target_path = resolve_datauser_path(path)
    except DataUserPathError as err:
        return f"Error: {err}"
    text_to_write = str(content)
    target_path = write_text_file(target_path, text_to_write, append=True, ensure_trailing_newline=True)
    return f"Appended {display_datauser_path(target_path)}"


# ----------------------------------------------------------------------------------------------------
def file_read(path: str, max_chars: int = 8000) -> str:
    """Read one datauser-relative text file, returning at most max_chars characters."""
    try:
        target_path = resolve_datauser_path(path)
    except DataUserPathError as err:
        return f"Error: {err}"
    if not target_path.exists():
        return f"File not found: {display_datauser_path(target_path)}"

    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError):
        max_chars = 8000

    return read_text_file(target_path, max_chars=max_chars)


# ----------------------------------------------------------------------------------------------------
def _normalise_keywords(keywords: list[str] | str) -> list[str]:
    # Models sometimes send a JSON array as a plain string (e.g. '["foo","bar"]')
    # despite the tool schema specifying type:array. Parse it back to a list.
    if isinstance(keywords, str):
        stripped = keywords.strip()
        if stripped.startswith("["):
            try:
                keywords = json.loads(stripped)
            except (json.JSONDecodeError, ValueError):
                pass
        if isinstance(keywords, str):
            # Fallback: treat as a single keyword.
            keywords = [stripped] if stripped else []
    return [str(k).strip().lower() for k in (keywords or []) if str(k).strip()]


# ----------------------------------------------------------------------------------------------------
def _normalise_find_arguments(keywords: list[str] | str, search_root: str) -> tuple[list[str], str]:
    """Accept a directory mistakenly supplied as the sole filename keyword.

    A request such as "list files under datauser/reports" naturally maps to
    ``file_find(keywords="datauser/reports")`` for a model.  A slash cannot be
    part of a filename search term, so treat that unambiguously as ``search_root``
    and list every file below it.
    """
    keywords_clean = _normalise_keywords(keywords)
    if not search_root and len(keywords_clean) == 1 and "/" in keywords_clean[0].replace("\\", "/"):
        return [], keywords_clean[0]
    return keywords_clean, search_root


# ----------------------------------------------------------------------------------------------------
def file_find(keywords: list[str], search_root: str = "") -> str:
    """Search the shared datauser tree for files whose name contains all keywords.

    Returns a newline-separated list of matching datauser-relative paths.
    Pass an empty list (or omit keywords) to list all files.
    Pass search_root (e.g. 'RadarData' or 'KoreDocs/RadarData') to restrict the search.
    """
    keywords_clean, search_root = _normalise_find_arguments(keywords, search_root)

    try:
        matches = [
            display_datauser_path(path)
            for path in list_datauser_files(search_root=search_root, keywords=keywords_clean)
        ]
    except DataUserPathError as err:
        return f"Error: {err}"

    label = ", ".join(f"'{k}'" for k in keywords_clean)
    if not matches:
        return (
            f"No files found matching all of {label}" + (f" under {search_root}" if search_root else "") + "."
            if keywords_clean
            else "No files found" + (f" under {search_root}" if search_root else "") + "."
        )
    return "\n".join(matches)


# ----------------------------------------------------------------------------------------------------
def folder_find(keywords: list[str], search_root: str = "") -> str:
    """Search the shared datauser tree for folders whose name contains all keywords.

    Returns a newline-separated list of matching datauser-relative paths.
    Pass an empty list (or omit keywords) to list all folders.
    Pass search_root (e.g. 'RadarData' or 'KoreDocs/RadarData') to restrict the search.
    """
    keywords_clean, search_root = _normalise_find_arguments(keywords, search_root)

    try:
        matches = [
            display_datauser_path(path)
            for path in list_datauser_folders(search_root=search_root, keywords=keywords_clean)
        ]
    except DataUserPathError as err:
        return f"Error: {err}"

    label = ", ".join(f"'{k}'" for k in keywords_clean)
    if not matches:
        return (
            f"No folders found matching all of {label}" + (f" under {search_root}" if search_root else "") + "."
            if keywords_clean
            else "No folders found" + (f" under {search_root}" if search_root else "") + "."
        )
    return "\n".join(matches)


# ----------------------------------------------------------------------------------------------------
def folder_create(path: str) -> str:
    """Create a directory (and any missing parents) at the given workspace-relative path.

    Safe to call when the directory already exists - returns a success message either way.
    """
    try:
        folder = resolve_datauser_directory(path)
    except DataUserPathError as err:
        return f"Error: {err}"
    existed = folder.exists()
    folder = create_datauser_folder(path)
    rel = display_datauser_path(folder)
    return f"Folder already exists: {rel}" if existed else f"Created folder: {rel}"


# ----------------------------------------------------------------------------------------------------
def folder_exists(path: str) -> str:
    """Return whether a directory exists at the given workspace-relative path.

    Returns 'yes' or 'no' so the model can branch on the result directly.
    """
    try:
        folder = resolve_datauser_directory(path)
    except DataUserPathError as err:
        return f"Error: {err}"
    return "yes" if folder.exists() and folder.is_dir() else "no"


# ----------------------------------------------------------------------------------------------------
# ----------------------------------------------------------------------------------------------------
def file_write_from_working_data(working_data_name: str, path: str, skip_content_guard: bool = False) -> str:
    """Write the content stored in a Working Data item to a file at path.

    Reads the named Working Data item (e.g. _wd_r5_fetch_page_text shown in a truncation
    notice) and writes it to the given path. The path follows the same resolution rules as
    file_write. Creates parent directories automatically.

    Use this instead of file_write when the content to write is already in Working Data
    (e.g. a large page fetch that was auto-saved), to avoid putting large content into tool
    call arguments where JSON encoding can cause errors.
    """
    from working_data import working_data_get

    content = working_data_get(working_data_name)
    if "not found" in content.lower() and len(content) < 200:
        return f"Error: Working Data item {working_data_name!r} does not exist"
    try:
        target_path = resolve_datauser_path(path)
    except DataUserPathError as err:
        return f"Error: {err}"
    if not skip_content_guard:
        reason = _suspicious_document_write_reason(target_path, content)
        if reason:
            return (
                f"Error: refusing to write suspicious placeholder content to {display_datauser_path(target_path)}; {reason}. "
                "Use dataset_write_koredoc or retrieve the real dataset records first."
            )
    target_path = write_text_file(target_path, content)
    return f"Wrote {display_datauser_path(target_path)} ({len(content):,} chars from Working Data item {working_data_name!r})"
