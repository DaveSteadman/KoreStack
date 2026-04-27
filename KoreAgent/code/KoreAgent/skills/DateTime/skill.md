# DateTime Skill

## Purpose
Return the current date, time, day name, and month name. Prefer `get_datetime_data()` in all cases - it returns both date and time in a single call. Use `get_day_name()` or `get_month_name()` only when you specifically need just that one value.

## Trigger keyword: current date, time, day of the week, or month name

## Interface
- Module: `code/KoreAgent/skills/DateTime/datetime_skill.py`
- Functions:
  - `get_datetime_data()`
  - `get_day_name()`
  - `get_month_name()`

## Parameters

### `get_datetime_data()`
No parameters.

### `get_day_name()`
No parameters.

### `get_month_name()`
No parameters.

## Output
- `get_datetime_data()` - returns a dict with two string fields:
  - `date` (str) - current date as `"YYYY-MM-DD"`
  - `time` (str) - current time as `"HH:MM:SS"`
- `get_day_name()` - returns the full name of the current day of the week, e.g. `"Saturday"`
- `get_month_name()` - returns the full name of the current month, e.g. `"March"`

## Triggers
Invoke this skill when the prompt contains any of these concepts or phrases:
- `what is the date`, `current date`, `today's date`
- `what time is it`, `current time`
- `what day is it`, `what year is it`
- `what month is it`, `current month`, `month name`
- `day of the week`, `day name`

## Scratchpad integration
Not typically applicable - output is a small dict or a short string.  If this skill is one
step in a multi-step plan that builds a larger composite string (e.g. a timestamped report),
use `scratch_save` to park the date/time string alongside other collected values before
assembling the final output.

## Examples
- `get_datetime_data()` - get the current date and time
  - Returns: `{"date": "2026-03-21", "time": "14:30:00"}`
- `get_day_name()` - get the current day name
  - Returns: `"Saturday"`
- `get_month_name()` - get the current month name
  - Returns: `"March"`
