# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# System-level token resolution for user prompts and skill arguments.
#
# Provides two utilities used across the framework:
#
#   resolve_tokens(text)       -- replaces {today}, {yesterday}, {month_year}, {month},
#                                 {year}, and {week} in any string with their current values.
#                                 {week} returns the ISO week number (ISO 8601, Monday-anchored,
#                                 01-53, January 4th is always in week 1).
#                                 Also resolves {scratch:key} to the current scratchpad value.
#                                 Applied automatically to user prompts in orchestration.py
#                                 and to string skill arguments in skill_executor.py.
#
#   parse_flexible_date(s)     -- converts "today", "yesterday", "YYYY-MM-DD", "YYYY/MM/DD"
#                                 to a date object.  Used by skills whose public API accepts
#                                 a human-readable date parameter.
#
# Tokens are case-insensitive and resolved at call time, so stored/scheduled prompts and
# queries stay perpetually current without manual edits.
#
# Related modules:
#   - orchestration.py              -- calls resolve_tokens on the user prompt
#   - skill_executor.py             -- calls resolve_tokens on string skill arguments
#   - skills/KoreMine/...             -- imports resolve_tokens as _resolve_query_tokens
#   - skills/KoreAnalysis/           -- imports parse_flexible_date as _parse_date
#   - skills/WebResearchOutput/...  -- imports parse_flexible_date as _parse_date
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
import re
from datetime import date as _date
from datetime import timedelta as _timedelta

from scratchpad import get_store as _get_store


# ====================================================================================================
# MARK: TOKEN RESOLUTION
# ====================================================================================================
def _longdate(d: _date) -> str:
    """Return a date formatted as 'March 19, 2026' (no leading zero) cross-platform."""
    return d.strftime("%B {day}, %Y").replace("{day}", str(d.day))


_TOKEN_RE = re.compile(
    r"\{(today|yesterday|longdate|longdateyesterday|month_year|month|year|week)\}",
    re.IGNORECASE,
)

_SCRATCH_TOKEN_RE = re.compile(r"\{scratch:([a-zA-Z0-9_]+)\}", re.IGNORECASE)


def resolve_tokens(text: str) -> str:
    """Replace date/time tokens in a string with their current values.

    Tokens (case-insensitive):
      {today}             -> YYYY-MM-DD          (e.g. 2026-03-08)
      {yesterday}         -> YYYY-MM-DD          (e.g. 2026-03-07)
      {longdate}          -> Month D, YYYY        (e.g. March 8, 2026)  -- better for web searches
      {longdateyesterday} -> Month D, YYYY        (e.g. March 7, 2026)
      {month_year}        -> Month YYYY           (e.g. March 2026)
      {month}             -> full month name      (e.g. March)
      {year}              -> four-digit year      (e.g. 2026)
      {week}              -> ISO week number (ISO 8601, 01-53) (e.g. 10)

    Tokens are resolved at call time so that stored/scheduled prompts and queries
    stay perpetually current without manual edits.

    Also resolves {scratch:key} to the current scratchpad value for that key.  Unrecognised
    scratch keys are left as-is.  Resolution is single-pass and non-recursive - the substituted
    value is never re-scanned, which prevents prompt-injection via stored content.
    """
    today     = _date.today()
    yesterday = today - _timedelta(days=1)
    _values   = {
        "today":             today.strftime("%Y-%m-%d"),
        "yesterday":         yesterday.strftime("%Y-%m-%d"),
        "longdate":          _longdate(today),
        "longdateyesterday": _longdate(yesterday),
        "month_year":        today.strftime("%B %Y"),
        "month":             today.strftime("%B"),
        "year":              today.strftime("%Y"),
        # %V: ISO 8601 week number (Monday-anchored, 01-53; January 4th is always in week 1).
        # Using %V rather than %W (%W is Sunday-anchored and can return "00" in early January).
        "week":              today.strftime("%V"),
    }

    def _replace(match: re.Match) -> str:
        return _values[match.group(1).lower()]

    # Single-pass date/time token resolution.
    result = _TOKEN_RE.sub(_replace, text)

    # Single-pass scratch token resolution.
    if "{scratch:" in result:
        store = _get_store()
        if store:
            def _replace_scratch(m: re.Match) -> str:
                val = store.get(m.group(1).lower())
                return val if val is not None else m.group(0)
            result = _SCRATCH_TOKEN_RE.sub(_replace_scratch, result)

    return result


# ====================================================================================================
# MARK: FLEXIBLE DATE PARSING
# ====================================================================================================
def parse_flexible_date(date_str: str) -> _date:
    """Parse a human-readable date string into a date object.

    Accepts:
      ""            -> today
      "today"       -> today
      "yesterday"   -> yesterday
      "YYYY-MM-DD"  -> that date
      "YYYY/MM/DD"  -> that date (normalised to ISO format)

    Raises ValueError for any unrecognised format.
    """
    s = date_str.strip().lower()
    if not s or s == "today":
        return _date.today()
    if s == "yesterday":
        return _date.today() - _timedelta(days=1)
    return _date.fromisoformat(date_str.strip().replace("/", "-"))
