# DateTime Skill

## Purpose
Return the current local date and time in one structured response.

## Interface
- Module: `KoreAgent/app/system_skills/DateTime/datetime_skill.py`
- Functions:
  - `get_datetime_data()`

## Parameters

### `get_datetime_data()`
No parameters.

## Output
- `get_datetime_data()` - returns a dict with three string fields:
  - `date` (str) - current date as `"YYYY-MM-DD"`
  - `time` (str) - current time as `"HH:MM:SS"`
  - `day_name` (str) - full current day name, e.g. `"Saturday"`
  - `month_name` (str) - full current month name, e.g. `"March"`

## Examples
- `get_datetime_data()` - get the current date and time
  - Returns: `{"date": "2026-03-21", "time": "14:30:00"}`
