# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Unit tests for strip_cot_preamble() in tool_loop.py.
#
# When a model uses extended thinking (e.g. QwQ, DeepSeek-R1), it emits a reasoning
# preamble before its final answer - phrases like "Let me think through this" or
# "We should consider whether...". strip_cot_preamble() detects and removes that
# preamble so only the actual answer reaches the user.
#
# These tests run without an LLM and complete in under a second.
# Called automatically by _run_post_test_checks after every /test run.
# ====================================================================================================
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "code"))

from KoreAgent.tool_loop import strip_cot_preamble

# Case 1: multi-paragraph CoT, clean last para -> return last para only.
deliberation = (
    "It appears DuckDuckGo returns no results for this query.\n"
    "We should consider whether the environment has internet access.\n"
    "Maybe we can try a different approach, or just report no results.\n\n"
    "No results were found for open source LLM inference engines."
)
result = strip_cot_preamble(deliberation)
assert result == "No results were found for open source LLM inference engines.", repr(result)

# Case 2: single paragraph with planning language -> untouched (no fallback fires).
single_para = "We should look this up. No results were found."
result2 = strip_cot_preamble(single_para)
assert result2 == single_para, repr(result2)

# Case 3: clean response -> untouched.
clean = "No results were found for the query."
result3 = strip_cot_preamble(clean)
assert result3 == clean, repr(result3)

# Case 4: structured preamble (original behaviour preserved).
structured = "Let me think through this.\n\n**Answer**\nPython 3.13 adds free-threaded mode."
result4 = strip_cot_preamble(structured)
assert "**Answer**" in result4 and "Let me think" not in result4, repr(result4)

# Case 5: last paragraph also has planning language -> untouched.
both_planning = (
    "We should try the search tool.\nMaybe it will work.\n\n"
    "We need to report the results now."
)
result5 = strip_cot_preamble(both_planning)
assert result5 == both_planning, repr(result5)

print("strip_cot_preamble: all 5 assertions passed")
