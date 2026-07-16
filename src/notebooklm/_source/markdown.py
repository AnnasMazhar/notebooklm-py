"""HTML → Markdown conversion for the source fulltext ``markdown`` path.

The ``output_format='markdown'`` branch of :meth:`SourceContentRenderer.get_fulltext`
requests the HTML rendition of a source and converts it with ``markdownify``.
A plain ``markdownify(html, heading_style="ATX")`` call has two rendering
defects on real NotebookLM sources:

* ``<br>`` inside a table cell is flattened to spaces, dropping the cell's
  internal line break (#1900).
* Inline LaTeX (``$...$``) adjacent to bold markup is emitted with escaped
  underscores (``\\_``) and with Markdown bold delimiters (``**`` / ``\\*\\*``)
  overlapping the math span, producing invalid LaTeX (#1899).

This module centralises the conversion policy in one testable place:
:func:`html_to_markdown` drives a :class:`_NotebookLMConverter` (which preserves
``<br>`` in table cells) and then repairs inline-math spans.
"""

from __future__ import annotations

import re
from typing import Any

from markdownify import MarkdownConverter

# A single-``$`` inline math span (not ``$$`` block math, no embedded newline),
# together with any bold-delimiter runs markdownify left hugging the opening and
# closing ``$``. The math body is non-greedy so the first closing ``$`` ends the
# span; ``lead``/``trail`` capture bold runs (``**`` or ``\*\*``) directly
# touching the delimiters.
_INLINE_MATH = re.compile(
    r"(?P<lead>(?:\\?\*\\?\*)*)"  # bold run hugging the opening delimiter
    r"(?<!\$)\$(?!\$)"  # opening $ (not part of $$)
    r"(?P<body>[^\n$]+?)"  # math body
    r"(?<!\$)\$(?!\$)"  # closing $ (not part of $$)
    r"(?P<trail>(?:\\?\*\\?\*)*)"  # bold run hugging the closing delimiter
)
_BOLD_RUN = re.compile(r"\\?\*\\?\*")


class _NotebookLMConverter(MarkdownConverter):
    """``markdownify`` converter that keeps ``<br>`` inside table cells.

    ``markdownify`` renders a ``<br>`` in a ``<td>``/``<th>`` as a hard line
    break which :meth:`convert_td` then collapses to a space, losing the cell's
    internal structure (#1900). Emitting a literal ``<br>`` instead survives the
    cell's newline flattening and renders as a line break in GitHub-flavoured /
    Obsidian Markdown tables.
    """

    def convert_br(self, el: Any, text: str, parent_tags: Any) -> str:
        if "td" in parent_tags or "th" in parent_tags:
            return "<br>"
        return super().convert_br(el, text, parent_tags)  # type: ignore[misc]


def _looks_like_latex(body: str) -> bool:
    """Whether an inline ``$...$`` span carries a LaTeX signal.

    A backslash is present in real LaTeX commands (``\\alpha``) and in the
    ``\\_`` / ``\\*`` escapes markdownify introduces inside math. Requiring one
    keeps prose and currency (``$5 and $10``, a bold ``**$5**`` price) untouched.
    """
    return "\\" in body


def _repair_inline_math(text: str) -> str:
    """Restore inline LaTeX spans mangled by Markdown escaping (#1899).

    For each ``$...$`` span that looks like LaTeX: unescape ``\\_``/``\\*`` back
    to ``_``/``*`` and drop bold-delimiter runs (``**`` / ``\\*\\*``) that landed
    inside the span. Bold runs hugging the delimiters are handled by whether
    they are balanced: a run on *both* sides (``**$x$**``) is intentional bold
    math and kept, while a run on only one side is a stray overlap artifact and
    dropped. Non-LaTeX spans are returned verbatim.
    """

    def _fix(match: re.Match[str]) -> str:
        body = match.group("body")
        if not _looks_like_latex(body):
            return match.group(0)
        body = body.replace("\\_", "_").replace("\\*", "*")
        body = _BOLD_RUN.sub("", body)
        lead, trail = match.group("lead"), match.group("trail")
        if lead and trail:
            # Balanced bold wrapping the whole span — keep it as bold math.
            return f"{lead}${body}${trail}"
        # A one-sided run overlaps the math; drop it.
        return f"${body}$"

    return _INLINE_MATH.sub(_fix, text)


def html_to_markdown(html: str) -> str:
    """Convert an HTML source rendition to Markdown for ``get_fulltext``.

    Preserves ``<br>`` inside table cells (#1900) and repairs inline LaTeX
    spans that Markdown escaping would otherwise corrupt (#1899).
    """
    converted = _NotebookLMConverter(heading_style="ATX").convert(html)
    return _repair_inline_math(converted)


__all__ = ["html_to_markdown"]
