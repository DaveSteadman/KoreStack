from __future__ import annotations

# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Shared SQLite/FTS utility helpers.
# ====================================================================================================

import re
from typing import Optional


def fts_build_query(q: str) -> str:
    """Convert a raw user search string into a safe FTS5 MATCH expression."""
    token_re = re.compile(r'"([^"]+)"|(\()|(\))|(\|)|\b(AND|OR|NOT)\b|,|([^\s(),|]+)', re.IGNORECASE)

    def _quote_term(value: str) -> str:
        clean = value.strip().replace('"', '""')
        return f'"{clean}"' if clean else ""

    out: list[str] = []
    open_parens = 0
    expect_operand = True

    for match in token_re.finditer((q or "").strip()):
        phrase  = match.group(1)
        lparen  = match.group(2)
        rparen  = match.group(3)
        bar     = match.group(4)
        keyword = match.group(5)
        word    = match.group(6)

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
    if not text:
        return None
    return len(text.split())


__all__ = ["compute_word_count", "fts_build_query"]
