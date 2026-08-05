"""Meta-lint: ``storage_state.json`` writes funnel through the canonical writer.

Part of refactor (b) — the canonical storage writer. The single sanctioned home
for mutating ``storage_state.json`` is
``src/notebooklm/_auth/storage_writer.py``. This AST guardrail enforces the
boundary by construction (import/name-based, following ``_ast_reach_in.py`` and
the other ``tests/_guardrails/`` lints) so a new writer is loud in CI rather than
silently re-opening the lost-update / policy-bypass classes the refactor closes.

Four decidable clauses (plan §b.2 enforcement, layer 1 — AST guardrail):

(i)  Outside ``storage_writer.py`` (and ``migration.py``), no ``_auth/`` module
     may import an ``_atomic_io`` **write primitive** (``atomic_write_json`` /
     ``replace_file_atomically``) except the modules on the frozen
     ``_AUTH_WRITE_PRIMITIVE_IMPORTERS`` allowlist. Each allowlisted module is
     annotated with WHY it is (still) allowed; the storage-state-writing ones
     shrink to just the canonical writer as later PRs migrate them (b-PR3
     acceptance: the storage-state exemption shrinks to ``{migration.py}``).

(ii) Dependency-seam bindings of a write primitive (the CLI
     ``RefreshDeps.atomic_write_json=...`` keyword-binding shape) are flagged and
     must appear on the frozen ``_WRITE_PRIMITIVE_SEAM_BINDINGS`` allowlist.

(iii) A write-primitive **call** (``open`` / ``os.open`` in write mode /
     ``Path.write_text`` / ``Path.write_bytes`` / ``os.replace``) whose target is
     a ``storage_state.json``-named **string literal** is forbidden anywhere
     outside ``storage_writer.py`` / ``migration.py``.

(iv) An **equality-asserted** frozenset of EVERY module repo-wide that imports
     ``atomic_write_json`` (clause (iii) cannot see the CLI writers, which call it
     with variable paths — so this allowlist keeps them visible). A new importer
     — anywhere — turns this assertion red until it is triaged onto the list.

The allowlists are module-level frozensets asserted by **equality** (not
subset), and every entry is existence-checked, so a stale entry (a module that no
longer imports the primitive) is also caught.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "notebooklm"

# The two ``_atomic_io`` symbols that perform (or wrap) the final write of a JSON
# state file. ``atomic_update_json`` is deliberately NOT in this set: it is the
# sanctioned locked read-modify-write helper for ``context.json`` / ``config.json``
# and already rejects ``storage_state.json`` paths at runtime (#1215).
_WRITE_PRIMITIVES = frozenset({"atomic_write_json", "replace_file_atomically"})

# Bare-name write primitives that, when *called* against a storage-state literal,
# are a storage-state write. ``os.open`` / ``os.replace`` are matched as
# attribute calls separately.
_BUILTIN_WRITE_CALLS = frozenset({"open"})
_OS_WRITE_CALLS = frozenset({"open", "replace"})
_PATH_WRITE_METHODS = frozenset({"write_text", "write_bytes"})

_STORAGE_STATE_LITERAL = "storage_state.json"


# --- Clause (iv): every module repo-wide that imports ``atomic_write_json`` ----
#
# Equality-asserted. Each entry is a source path relative to ``SRC_ROOT``.
# Categorised only for reviewer clarity — the guardrail asserts the whole set.
_ATOMIC_WRITE_JSON_IMPORTERS: frozenset[str] = frozenset(
    {
        # Canonical storage-state writer (refactor (b)).
        "_auth/storage_writer.py",
        # Legitimate NON-storage-state writers (permanent).
        "io.py",  # public re-export of the _atomic_io helpers
        "_auth/account.py",  # writes the legacy sibling context.json (not storage_state)
        "cli/context.py",  # CLI context.json / config.json
        "mcp/_oauth.py",  # writes the MCP OAuth token file
        # Storage-state writers still to be migrated onto storage_writer
        # (tracked; removed as later PRs land — see _STORAGE_STATE_WRITE_EXEMPTIONS).
        "_auth/browser_capture.py",  # browser-capture re-mint (migrates in b-PR2)
        "cli/services/login/refresh.py",  # CLI login writer (migrates in b-PR3)
        "cli/services/login/cookie_writes.py",  # CLI login writer (migrates in b-PR3)
        "cli/_cookie_import.py",  # CLI auth import-cookies writer (migrates in b-PR3)
    }
)

# --- Clause (iv, parallel): repo-wide importers of ``replace_file_atomically`` --
#
# ``replace_file_atomically`` is the OTHER ``_atomic_io`` write primitive, and (like
# ``atomic_write_json``) can be called with a VARIABLE path that clause (iii)
# cannot see. Track it with its own equality-asserted allowlist so a future
# ``cli/``/``mcp/`` module writing storage_state via ``replace_file_atomically``
# can't escape every clause. Both current importers are legitimate non-storage
# users.
_REPLACE_FILE_ATOMICALLY_IMPORTERS: frozenset[str] = frozenset(
    {
        "io.py",  # public re-export of the _atomic_io helpers
        "cli/skill_cmd.py",  # writes skill definition files (not storage_state)
        "migration.py",  # crash-safe legacy-layout migration (#2085); the sole
        # guardrail-exempt storage_state writer (moves files via temp+rename).
    }
)

# --- Clause (i): ``_auth/`` modules allowed to import a write primitive ---------
#
# Outside these, an ``_auth/`` module importing a write primitive is a violation.
# ``storage_writer.py`` is the canonical home; the rest are annotated exemptions.
_AUTH_WRITE_PRIMITIVE_IMPORTERS: frozenset[str] = frozenset(
    {
        "_auth/storage_writer.py",  # canonical (writes storage_state.json)
        "_auth/account.py",  # context.json cleanup only (not storage_state)
        "_auth/browser_capture.py",  # storage_state re-mint — migrates in b-PR2
    }
)

# --- Clause (i, sub): storage-state write exemptions OUTSIDE storage_writer -----
#
# Modules that still perform a ``storage_state.json`` write via a primitive,
# beyond the canonical writer. ``migration.py`` is the permanent sole exemption
# (it moves whole files with ``shutil`` + ``atomic_update_json`` for config.json,
# #2085). The b-PR3 acceptance criterion shrinks this set to ``{migration.py}``.
_STORAGE_STATE_WRITE_EXEMPTIONS: frozenset[str] = frozenset(
    {
        "migration.py",  # permanent: legacy-profile file migration (#2085)
        "_auth/browser_capture.py",  # migrates in b-PR2
        "cli/services/login/refresh.py",  # migrates in b-PR3
        "cli/services/login/cookie_writes.py",  # migrates in b-PR3
        "cli/_cookie_import.py",  # migrates in b-PR3
    }
)

# --- Clause (ii): sanctioned dependency-seam bindings of a write primitive ------
#
# ``(relative source path, lineno-free descriptor)``. The CLI login refresh deps
# object binds ``atomic_write_json`` so the injected writer can be swapped in
# tests; it migrates in b-PR3.
_WRITE_PRIMITIVE_SEAM_BINDINGS: frozenset[str] = frozenset(
    {
        "cli/services/login/refresh.py",  # RefreshDeps(atomic_write_json=...) — b-PR3
    }
)


def _iter_src_files() -> list[Path]:
    return sorted(p for p in SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(path: Path) -> str:
    return path.relative_to(SRC_ROOT).as_posix()


# Exact module names that resolve to the ``_atomic_io`` write primitives, across
# relative (``from .._atomic_io``/``from ....io`` — the dots live in ``node.level``,
# stripped from ``node.module``) and absolute spellings. Exact matching (not
# ``endswith("io")``) so an unrelated module like ``studio`` cannot over-match.
_ATOMIC_IO_MODULE_NAMES = frozenset({"_atomic_io", "io", "notebooklm._atomic_io", "notebooklm.io"})


def _is_atomic_io_module(module: str) -> bool:
    return module in _ATOMIC_IO_MODULE_NAMES


def _imports_write_primitive(tree: ast.AST) -> set[str]:
    """Names of ``_atomic_io`` write primitives imported by this module."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _is_atomic_io_module(node.module or ""):
            for alias in node.names:
                if alias.name in _WRITE_PRIMITIVES:
                    found.add(alias.name)
    return found


def _imports_named_primitive(tree: ast.AST, name: str) -> bool:
    """Does this module ``from … import <name>`` an ``_atomic_io`` write primitive?"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _is_atomic_io_module(node.module or ""):
            if any(alias.name == name for alias in node.names):
                return True
    return False


def _has_write_primitive_seam_binding(tree: ast.AST) -> bool:
    """Detect a ``foo(atomic_write_json=<name>)`` / ``x.atomic_write_json = <name>``
    dependency-seam binding of a write primitive."""
    for node in ast.walk(tree):
        # Keyword binding: ``RefreshDeps(atomic_write_json=atomic_write_json)``.
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _WRITE_PRIMITIVES:
                    return True
        # Attribute assignment: ``deps.atomic_write_json = atomic_write_json``.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr in _WRITE_PRIMITIVES:
                    return True
    return False


def _is_storage_state_literal(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _STORAGE_STATE_LITERAL in node.value
    )


def _write_primitive_calls_on_storage_literal(tree: ast.AST) -> list[int]:
    """Line numbers of write-primitive calls whose first arg is a storage-state
    string literal (``open("…/storage_state.json", "w")``, ``os.replace``,
    ``Path("storage_state.json").write_text(...)``)."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        target: ast.expr | None = node.args[0] if node.args else None

        # Bare builtin ``open(<literal>, "w"...)``.
        if isinstance(func, ast.Name) and func.id in _BUILTIN_WRITE_CALLS:
            mode = node.args[1] if len(node.args) >= 2 else None
            writes = mode is None or (
                isinstance(mode, ast.Constant)
                and isinstance(mode.value, str)
                and any(c in mode.value for c in ("w", "a", "x", "+"))
            )
            if writes and target is not None and _is_storage_state_literal(target):
                hits.append(node.lineno)
            continue

        # Attribute calls: ``os.open`` / ``os.replace`` / ``<path>.write_text``.
        if isinstance(func, ast.Attribute):
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and func.attr in _OS_WRITE_CALLS
            ):
                # ``os.open(path, flags)`` writes to args[0]; ``os.replace(src, dst)``
                # writes to args[1] (the DESTINATION). Check the right position for
                # each so a storage-state destination literal is actually matched.
                os_target = (
                    node.args[1] if func.attr == "replace" and len(node.args) >= 2 else target
                )
                if os_target is not None and _is_storage_state_literal(os_target):
                    hits.append(node.lineno)
                continue
            if func.attr in _PATH_WRITE_METHODS:
                # ``Path("…/storage_state.json").write_text(...)``.
                recv = func.value
                if (
                    isinstance(recv, ast.Call)
                    and recv.args
                    and _is_storage_state_literal(recv.args[0])
                ):
                    hits.append(node.lineno)
    return hits


def test_atomic_write_json_importers_frozen_allowlist() -> None:
    """Clause (iv): the repo-wide set of ``atomic_write_json`` importers is frozen.

    Equality (not subset): a NEW importer OR a stale entry both turn this red.
    Variable-path CLI writers (which clause (iii) cannot see) stay visible here.
    """
    actual = {
        _rel(p)
        for p in _iter_src_files()
        if _imports_named_primitive(ast.parse(p.read_text("utf-8")), "atomic_write_json")
    }
    assert actual == set(_ATOMIC_WRITE_JSON_IMPORTERS), (
        "atomic_write_json importer set drifted from the frozen allowlist. "
        f"Unexpected new importers: {sorted(actual - _ATOMIC_WRITE_JSON_IMPORTERS)}; "
        f"stale allowlist entries: {sorted(_ATOMIC_WRITE_JSON_IMPORTERS - actual)}. "
        "Any new storage_state.json writer must go through storage_writer.py; a new "
        "non-storage writer must be triaged onto _ATOMIC_WRITE_JSON_IMPORTERS."
    )


def test_replace_file_atomically_importers_frozen_allowlist() -> None:
    """Clause (iv, parallel): the repo-wide set of ``replace_file_atomically``
    importers is frozen (equality-asserted), so a future ``cli``/``mcp`` module
    writing storage_state via this variable-path primitive is loud."""
    actual = {
        _rel(p)
        for p in _iter_src_files()
        if _imports_named_primitive(ast.parse(p.read_text("utf-8")), "replace_file_atomically")
    }
    assert actual == set(_REPLACE_FILE_ATOMICALLY_IMPORTERS), (
        "replace_file_atomically importer set drifted from the frozen allowlist. "
        f"Unexpected new importers: {sorted(actual - _REPLACE_FILE_ATOMICALLY_IMPORTERS)}; "
        f"stale allowlist entries: {sorted(_REPLACE_FILE_ATOMICALLY_IMPORTERS - actual)}. "
        "A new storage_state.json writer must go through storage_writer.py."
    )


def test_atomic_write_json_allowlist_entries_exist() -> None:
    """Every allowlist entry must name a real source file (no stale paths)."""
    for rel in _ATOMIC_WRITE_JSON_IMPORTERS:
        assert (SRC_ROOT / rel).is_file(), f"allowlisted importer no longer exists: {rel}"
    for rel in _REPLACE_FILE_ATOMICALLY_IMPORTERS:
        assert (SRC_ROOT / rel).is_file(), f"rfa allowlisted importer no longer exists: {rel}"
    for rel in _AUTH_WRITE_PRIMITIVE_IMPORTERS:
        assert (SRC_ROOT / rel).is_file(), f"allowlisted _auth importer no longer exists: {rel}"
    for rel in _STORAGE_STATE_WRITE_EXEMPTIONS:
        assert (SRC_ROOT / rel).is_file(), f"storage-state exemption no longer exists: {rel}"
    for rel in _WRITE_PRIMITIVE_SEAM_BINDINGS:
        assert (SRC_ROOT / rel).is_file(), f"seam-binding entry no longer exists: {rel}"


def test_auth_write_primitive_importers_frozen() -> None:
    """Clause (i): only allowlisted ``_auth/`` modules import a write primitive.

    Equality-asserted, so both a new ``_auth/`` importer and a stale entry fail.
    """
    actual = {
        _rel(p)
        for p in _iter_src_files()
        if "_auth" in p.relative_to(SRC_ROOT).parts
        if _imports_write_primitive(ast.parse(p.read_text("utf-8")))
    }
    assert actual == set(_AUTH_WRITE_PRIMITIVE_IMPORTERS), (
        "_auth write-primitive importer set drifted from the frozen allowlist. "
        f"Unexpected: {sorted(actual - _AUTH_WRITE_PRIMITIVE_IMPORTERS)}; "
        f"stale: {sorted(_AUTH_WRITE_PRIMITIVE_IMPORTERS - actual)}. "
        "storage_state.json writes belong in storage_writer.py."
    )


def test_write_primitive_seam_bindings_frozen() -> None:
    """Clause (ii): dependency-seam bindings of a write primitive are allowlisted."""
    actual = {
        _rel(p)
        for p in _iter_src_files()
        if _has_write_primitive_seam_binding(ast.parse(p.read_text("utf-8")))
    }
    assert actual == set(_WRITE_PRIMITIVE_SEAM_BINDINGS), (
        "write-primitive dependency-seam bindings drifted from the frozen allowlist. "
        f"Unexpected: {sorted(actual - _WRITE_PRIMITIVE_SEAM_BINDINGS)}; "
        f"stale: {sorted(_WRITE_PRIMITIVE_SEAM_BINDINGS - actual)}."
    )


@pytest.mark.parametrize(
    ("snippet", "should_flag"),
    [
        ('open("/x/storage_state.json", "w")', True),
        ('open("/x/storage_state.json")', True),  # default mode is read+write ambiguous → flag
        ('open("/x/storage_state.json", "r")', False),
        ('os.open("/x/storage_state.json", os.O_WRONLY)', True),
        # os.replace's DESTINATION is args[1] — the fix (NIT #5) must catch this.
        ('os.replace(tmp, "/x/storage_state.json")', True),
        ('os.replace("/x/storage_state.json", other)', False),  # source, not destination
        ('Path("/x/storage_state.json").write_text(data)', True),
        ('open("/x/context.json", "w")', False),
    ],
)
def test_write_primitive_literal_detector_self_check(snippet: str, should_flag: bool) -> None:
    """Self-test of the clause (iii) detector — in particular that ``os.replace``
    is matched on its destination (args[1]), per NIT #5."""
    hits = _write_primitive_calls_on_storage_literal(ast.parse(snippet))
    assert bool(hits) is should_flag, f"{snippet!r} -> hits={hits}, expected flag={should_flag}"


def test_no_storage_state_literal_write_primitive_calls() -> None:
    """Clause (iii): no write-primitive call on a ``storage_state.json`` literal
    outside the canonical writer / migration."""
    allowed = {"_auth/storage_writer.py", "migration.py"}
    violations: dict[str, list[int]] = {}
    for path in _iter_src_files():
        rel = _rel(path)
        if rel in allowed:
            continue
        hits = _write_primitive_calls_on_storage_literal(ast.parse(path.read_text("utf-8")))
        if hits:
            violations[rel] = hits
    assert violations == {}, (
        "write-primitive call(s) targeting a storage_state.json literal outside "
        f"storage_writer.py / migration.py: {violations}"
    )


def test_storage_state_write_exemptions_are_atomic_write_json_importers() -> None:
    """Coherence: every tracked storage-state exemption (except migration.py,
    which writes via shutil/atomic_update_json) is also an ``atomic_write_json``
    importer — so shrinking one list shrinks the other in lock-step."""
    for rel in _STORAGE_STATE_WRITE_EXEMPTIONS - {"migration.py"}:
        assert rel in _ATOMIC_WRITE_JSON_IMPORTERS, (
            f"{rel} is a storage-state exemption but not an atomic_write_json importer; "
            "the two tracking lists have drifted."
        )
