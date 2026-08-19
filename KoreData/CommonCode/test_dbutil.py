# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Test coverage for dbutil.
# Exercises the expected behaviour and regression boundaries for this area.
# MARK: FUNCTIONS
# Primary types: DbUtilTests.
# Function inventory:
# - test_terms_default_to_and: Implements the test terms default to and operation for this module.
# - test_phrase_and_term_mix: Implements the test phrase and term mix operation for this module.
# - test_explicit_or_is_preserved: Implements the test explicit or is preserved operation for this module.
# - test_pipe_is_or_shorthand: Implements the test pipe is or shorthand operation for this module.
# - test_parentheses_and_not_are_preserved: Implements the test parentheses and not are preserved operation for this module.
# - test_comma_remains_and_separator: Implements the test comma remains and separator operation for this module.
# - test_trailing_operator_is_trimmed: Implements the test trailing operator is trimmed operation for this module.
# ====================================================================================================

import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from dbutil import fts_build_query


class DbUtilTests(unittest.TestCase):
    def test_terms_default_to_and(self) -> None:
        self.assertEqual(fts_build_query("art of war"), '"art" AND "of" AND "war"')

    def test_phrase_and_term_mix(self) -> None:
        self.assertEqual(fts_build_query('sun tzu "art of war"'), '"sun" AND "tzu" AND "art of war"')

    def test_explicit_or_is_preserved(self) -> None:
        self.assertEqual(fts_build_query("plato OR aristotle"), '"plato" OR "aristotle"')

    def test_pipe_is_or_shorthand(self) -> None:
        self.assertEqual(fts_build_query("plato | aristotle"), '"plato" OR "aristotle"')

    def test_parentheses_and_not_are_preserved(self) -> None:
        self.assertEqual(
            fts_build_query('(plato OR socrates) NOT sophists'),
            '( "plato" OR "socrates" ) NOT "sophists"',
        )

    def test_comma_remains_and_separator(self) -> None:
        self.assertEqual(fts_build_query("roman, republic"), '"roman" AND "republic"')

    def test_trailing_operator_is_trimmed(self) -> None:
        self.assertEqual(fts_build_query("plato OR"), '"plato"')


if __name__ == "__main__":
    unittest.main()