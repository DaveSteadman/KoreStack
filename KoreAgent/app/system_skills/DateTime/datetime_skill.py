# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# DateTime skill module for KoreAgent.
#
# Provides a single callable function that returns structured date and time values.
#
# This module is discovered automatically by skills_catalog_builder.py via the accompanying
# skill.md definition file and added to the generated skills catalog.
#
# Related modules:
#   - skill_executor.py         -- dynamically imports and calls functions from this module
#   - skills_catalog_builder.py -- reads skill.md to build the catalog entry for this skill
# MARK: FUNCTIONS
# Function inventory:
# - get_datetime_data: Returns datetime data for this module.
# ====================================================================================================


# ====================================================================================================
# MARK: IMPORTS
# ====================================================================================================
from datetime import datetime


# ====================================================================================================
# MARK: PUBLIC SKILL API
# ====================================================================================================
def get_datetime_data() -> dict:
    """Return current local date, time, day name, and month name in one structured response."""
    current_local = datetime.now()
    return {
        "date":       current_local.strftime("%Y-%m-%d"),
        "time":       current_local.strftime("%H:%M:%S"),
        "day_name":   current_local.strftime("%A"),
        "month_name": current_local.strftime("%B"),
    }


# ----------------------------------------------------------------------------------------------------
def get_day_name() -> str:
    """Return the full name of the current day of the week, e.g. 'Saturday'."""
    return datetime.now().strftime("%A")


# ----------------------------------------------------------------------------------------------------
def get_month_name() -> str:
    """Return the full name of the current month, e.g. 'March'."""
    return datetime.now().strftime("%B")
