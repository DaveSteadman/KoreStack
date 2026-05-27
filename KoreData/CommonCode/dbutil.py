# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared database utilities for KoreData sub-services.
#
# Provides fts_build_query(): converts a raw user search string into a safe FTS5 MATCH
# expression supporting phrase search, explicit boolean operators, grouping, and
# bare word AND-joining.
# Also provides word_count() for estimating content length in words.
#
# Related modules:
#   - KoreRAG/app/database.py        -- passes user queries through fts_build_query
#   - KoreReference/app/database.py  -- same
#   - KoreLibrary/app/database.py    -- same
# ====================================================================================================
import re
from typing import Optional


def fts_build_query(q: str) -> str:
    """Convert a raw user search string into a safe FTS5 MATCH expression.

    Quoted substrings are treated as phrase searches (words must appear
    consecutively in that order). Unquoted words are individual terms.
    Adjacent terms are combined with AND by default. Users may also write
    explicit OR / NOT operators and parentheses for grouping.

    Examples:
        "art of war"          -> "art of war"          (exact phrase)
        art of war            -> "art" "of" "war"       (all three words, any order)
        sun tzu "art of war"  -> "sun" "tzu" "art of war"
        plato OR aristotle    -> "plato" OR "aristotle"
        stoic NOT roman       -> "stoic" NOT "roman"
        (plato OR socrates) dialogue -> ( "plato" OR "socrates" ) AND "dialogue"
    """
    token_re = re.compile(r'"([^"]+)"|(\()|(\))|(\|)|\b(AND|OR|NOT)\b|,|([^\s(),|]+)', re.IGNORECASE)

    def _quote_term(value: str) -> str:
        clean = value.strip().replace('"', '""')
        return f'"{clean}"' if clean else ""

    out: list[str] = []
    open_parens = 0
    expect_operand = True

    for match in token_re.finditer((q or "").strip()):
        phrase = match.group(1)
        lparen = match.group(2)
        rparen = match.group(3)
        bar = match.group(4)
        keyword = match.group(5)
        word = match.group(6)

        if phrase is not None:
            token = _quote_term(phrase)
            if not token:
                continue
            if not expect_operand:
                out.append("AND")
            out.append(token)
            expect_operand = False
            continue

        if lparen:
            if not expect_operand:
                out.append("AND")
            out.append("(")
            open_parens += 1
            expect_operand = True
            continue

        if rparen:
            if open_parens <= 0 or expect_operand:
                continue
            out.append(")")
            open_parens -= 1
            expect_operand = False
            continue

        if bar or (keyword and keyword.upper() == "OR"):
            if expect_operand:
                continue
            out.append("OR")
            expect_operand = True
            continue

        if keyword:
            op = keyword.upper()
            if op == "AND":
                if expect_operand:
                    continue
                out.append("AND")
                expect_operand = True
                continue
            if op == "NOT":
                if expect_operand:
                    continue
                out.append("NOT")
                expect_operand = True
                continue

        if match.group(0) == ",":
            if expect_operand:
                continue
            out.append("AND")
            expect_operand = True
            continue

        if word:
            token = _quote_term(word)
            if not token:
                continue
            if not expect_operand:
                out.append("AND")
            out.append(token)
            expect_operand = False

    while out and out[-1] in {"AND", "OR", "NOT", "("}:
        tail = out.pop()
        if tail == "(":
            open_parens = max(0, open_parens - 1)

    out.extend(")" for _ in range(open_parens) if out)
    return " ".join(out)


def compute_word_count(text: Optional[str]) -> Optional[int]:
    """Return the number of whitespace-separated words in *text*, or None if empty."""
    if not text:
        return None
    return len(text.split())
