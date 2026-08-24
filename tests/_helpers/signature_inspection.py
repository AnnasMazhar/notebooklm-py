"""Cross-version helpers for inspecting callable shape without annotation evaluation."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping

try:
    from annotationlib import Format as _AnnotationFormat
except ImportError:  # pragma: no cover - annotationlib is new in Python 3.14
    _SIGNATURE_KWARGS: dict[str, object] = {}
else:
    _SIGNATURE_KWARGS = {"annotation_format": _AnnotationFormat.STRING}


def signature_parameters(value: Callable[..., object]) -> Mapping[str, inspect.Parameter]:
    """Return parameters without triggering deferred annotation evaluation.

    Python 3.14 evaluates deferred annotations when ``inspect.signature`` uses
    its default value format. For a class method named ``list`` whose return
    annotation is ``list[...]``, the class-local method can then shadow the
    builtin and make inspection raise ``TypeError``. These contract tests care
    only about parameter names, kinds, and defaults, so string-format
    annotations are the stable cross-version representation.
    """
    return inspect.signature(value, **_SIGNATURE_KWARGS).parameters


__all__ = ["signature_parameters"]
