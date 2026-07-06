# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# FileAccess skill module for KoreAgent.
#
# Provides safe file read/write/append operations constrained to the shared datauser root, with
# sensible defaults for relative paths.
#
# Path behavior:
#   - bare file name or relative path resolves under datauser/
#   - paths that already begin with legacy prefixes like "data/", "datauser/", or "KoreDocs/"
#     are accepted and normalized
#   - absolute paths are allowed only when they resolve inside the datauser directory
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import json
import re

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
    try:
        target_path = resolve_datauser_path(path)
    except DataUserPathError as err:
        return f"Error: {err}"
    text_to_write = str(content).replace("\\n", "\n")  # unescape literal \n from model output
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
    try:
        target_path = resolve_datauser_path(path)
    except DataUserPathError as err:
        return f"Error: {err}"
    text_to_write = str(content).replace("\\n", "\n")  # unescape literal \n from model output
    target_path = write_text_file(target_path, text_to_write, append=True, ensure_trailing_newline=True)
    return f"Appended {display_datauser_path(target_path)}"


# ----------------------------------------------------------------------------------------------------
def file_read(path: str, max_chars: int = 8000) -> str:
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
def file_find(keywords: list[str], search_root: str = "") -> str:
    """Search the shared datauser tree for files whose name contains all keywords.

    Returns a newline-separated list of matching datauser-relative paths.
    Pass an empty list (or omit keywords) to list all files.
    Pass search_root (e.g. 'RadarData' or 'KoreDocs/RadarData') to restrict the search.
    """
    keywords_clean = _normalise_keywords(keywords)

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
    keywords_clean = _normalise_keywords(keywords)

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
def file_write_from_scratch(scratch_key: str, path: str, skip_content_guard: bool = False) -> str:
    """Write the content stored in a scratchpad key to a file at path.

    Reads the auto-saved scratchpad key (e.g. _tc_r5_fetch_page_text shown in a truncation
    notice) and writes it to the given path. The path follows the same resolution rules as
    write_file. Creates parent directories automatically.

    Use this instead of write_file when the content to write is already in the scratchpad
    (e.g. a large page fetch that was auto-saved), to avoid putting large content into tool
    call arguments where JSON encoding can cause errors.
    """
    from scratchpad import scratch_load as _scratch_load

    content = _scratch_load(scratch_key)
    if "not found" in content.lower() and len(content) < 200:
        return f"Error: scratchpad key {scratch_key!r} does not exist"
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
    return f"Wrote {display_datauser_path(target_path)} ({len(content):,} chars from scratch key {scratch_key!r})"
