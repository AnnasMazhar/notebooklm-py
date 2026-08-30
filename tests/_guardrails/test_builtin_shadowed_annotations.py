"""Guard that a class-body annotation never names a builtin the class shadows.

``WebArtifactsAPI`` defines a method called ``list``, so inside that class body
the name ``list`` is the method, not the builtin. Until Python 3.14 that was
invisible: annotations were evaluated eagerly at ``def`` time, before the name
was bound as a class attribute, so ``-> list[Artifact]`` resolved to the
builtin and everything worked. PEP 649 defers the evaluation into a synthesized
``__annotate__`` whose scope includes the class body, so the same annotation now
resolves *after* the class exists and raises ``TypeError: 'function' object is
not subscriptable`` the moment anything reads it (#2266).

The repo's answer predates the break: ``builtins.list[...]`` is spelled
explicitly at ~40 sites across the client classes for exactly this reason. The
twelve sites #2266 found were the ones that missed the convention, not a
different design. This lint keeps them from coming back.

**Static on purpose.** It parses rather than imports, so it catches a 3.14-only
break while running on 3.12 — which is where it will actually run. The failing
signature check is marked ``repo_lint``, and every compatibility cell in
``test.yml`` runs ``-m "not repo_lint"``, while both lanes that do execute
``repo_lint`` pin 3.12. Adding a 3.14 lane would close today's hole on one
version; parsing closes it on every version, including whichever one breaks
next.

**Every builtin, not just ``list``.** Only ``list`` is shadowed today. A future
method named ``type``, ``filter`` or ``id`` fails the same way for the same
reason, and there is no cost to covering it now.

Modules carrying ``from __future__ import annotations`` are out of scope: their
annotations are stored as strings and evaluated against module globals, where
the builtin is still the builtin.

Only annotations evaluated **in the class-body scope** count — method
signatures and class-level variable annotations. A local annotation inside a
method body (``source_ids: list[str] = []``) is not evaluated at all, and a
nested ``def`` resolves in its enclosing function scope, so neither can see the
class attribute. Flagging those would report a break that cannot happen.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_lint

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src" / "notebooklm"


def _has_future_annotations(tree: ast.Module) -> bool:
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _shadowed_builtins(cls: ast.ClassDef) -> set[str]:
    """Return the builtin names this class body rebinds."""
    names: set[str] = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return {name for name in names if hasattr(builtins, name)}


def _class_scope_annotations(cls: ast.ClassDef) -> list[ast.expr]:
    """Return the annotations evaluated in the class-body scope."""
    annotations: list[ast.expr] = []
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
                if arg is not None and arg.annotation is not None:
                    annotations.append(arg.annotation)
            if node.returns is not None:
                annotations.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
    return annotations


def test_class_body_annotations_do_not_name_a_shadowed_builtin() -> None:
    offenders: list[str] = []

    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _has_future_annotations(tree):
            continue
        for cls in [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]:
            shadowed = _shadowed_builtins(cls)
            if not shadowed:
                continue
            for annotation in _class_scope_annotations(cls):
                for node in ast.walk(annotation):
                    if isinstance(node, ast.Name) and node.id in shadowed:
                        offenders.append(
                            f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} "
                            f"({cls.name} shadows '{node.id}')"
                        )

    assert not offenders, (
        "class-body annotation names a builtin the class rebinds, which resolves to the "
        "class attribute under PEP 649 (#2266). Qualify it as 'builtins."
        + "<name>[...]', the spelling the rest of the client classes already use:\n  "
        + "\n  ".join(sorted(set(offenders)))
    )
