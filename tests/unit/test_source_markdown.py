"""Unit tests for the source HTML → Markdown conversion policy.

Covers the two rendering defects fixed in the ``output_format='markdown'``
path: ``<br>`` flattening inside table cells (#1900) and inline-LaTeX
corruption next to bold markup (#1899).
"""

from __future__ import annotations

import pytest

pytest.importorskip("markdownify")

from notebooklm._source.markdown import html_to_markdown  # noqa: E402


def test_br_in_table_cell_is_preserved() -> None:
    """A ``<br>`` inside a ``<td>`` renders as a literal ``<br>``, not a space (#1900)."""
    html = "<table><tr><td>Column 1 <br> Line 2</td><td>Value</td></tr></table>"

    out = html_to_markdown(html)

    assert "Column 1 <br> Line 2" in out
    # The row stays a single pipe-table line (no physical newline in the cell).
    row = next(line for line in out.splitlines() if "Column 1" in line)
    assert row.count("|") == 3


def test_br_in_header_cell_is_preserved() -> None:
    html = "<table><tr><th>H1<br>x</th></tr><tr><td>v</td></tr></table>"

    out = html_to_markdown(html)

    assert "H1<br>x" in out


def test_br_outside_table_is_unchanged() -> None:
    """A ``<br>`` outside a table keeps markdownify's normal hard-break rendering."""
    out = html_to_markdown("<p>a<br>b</p>")

    assert "<br>" not in out
    assert "a" in out and "b" in out


def test_inline_latex_next_to_bold_is_repaired() -> None:
    """Inline LaTeX adjacent to bold markup stays valid (#1899).

    The HTML rendition overlaps ``<strong>`` markup with an inline ``$...$``
    span; the conversion must keep the bold delimiters outside the math and
    the LaTeX subscripts intact.
    """
    # Exercise the repair directly on the mangled Markdown markdownify emits.
    from notebooklm._source.markdown import _repair_inline_math

    mangled = "**(B)** **membrane-bound** \\*\\*$LT\\alpha_1\\beta_2**$"

    assert _repair_inline_math(mangled) == "**(B)** **membrane-bound** $LT\\alpha_1\\beta_2$"


def test_inline_latex_escaped_underscores_are_restored() -> None:
    """``\\_`` introduced inside inline math is restored to ``_`` (#1899)."""
    from notebooklm._source.markdown import _repair_inline_math

    assert _repair_inline_math("$LT\\alpha\\_1\\beta\\_2$") == "$LT\\alpha_1\\beta_2$"


@pytest.mark.parametrize(
    "text",
    [
        "prices $5 and $10 today",  # currency, no LaTeX signal
        "bold price **$5**",  # bold currency, no LaTeX signal
        "already clean $a_1 + b^2$ math",  # no backslash -> already valid
        "normal **bold** with no math",
    ],
)
def test_repair_leaves_non_latex_text_untouched(text: str) -> None:
    from notebooklm._source.markdown import _repair_inline_math

    assert _repair_inline_math(text) == text


def test_inline_latex_bold_wrapping_math_end_to_end() -> None:
    """A ``<strong>`` wrapping a whole math span keeps balanced bold, valid LaTeX."""
    html = "<p><strong>note</strong> <strong>$x_1 = y_2$</strong></p>"

    out = html_to_markdown(html)

    # Balanced ``**$...$**`` is intentional bold math; the subscripts are intact.
    assert "**$x_1 = y_2$**" in out
    assert "\\_" not in out
