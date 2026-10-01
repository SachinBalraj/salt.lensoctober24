"""Formula-injection guard for human-entered text exported to CSV.

A cell that begins with ``=``, ``+``, ``-``, ``@``, a tab, or a carriage return
is interpreted by spreadsheet software as a formula and executed on open. Any
free-text column a user can affect — experiment notes, evaluation and
validation reasons — is therefore prefixed with a leading apostrophe as it is
written, so the cell stays text.

This is only for prose. The dataset CSV is deliberately untouched: its cells are
measurements (which routinely start with ``-``) and another module parses every
value back, so rewriting them here would corrupt the science rather than defend
it. The rule is: a hyphen is a number unless the column is prose.
"""

from __future__ import annotations

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_prose_cell(value: object) -> str:
    """Prefix a leading apostrophe onto text a spreadsheet would read as a formula."""
    text = str(value) if value is not None else ""
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


__all__ = ["sanitize_prose_cell"]