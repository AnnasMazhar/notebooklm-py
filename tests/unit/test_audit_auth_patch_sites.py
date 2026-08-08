"""Tests for ``scripts/audit_auth_patch_sites.py``.

The script is the source of the ADR-0033 patch-site metric quoted in review and
in PR descriptions, and it has already miscounted twice: once by resolving
aliases too loosely, and once (caught by review on #2156) by reading only
POSITIONAL arguments, so every keyword-form ``monkeypatch.setattr`` and
``patch.object`` went uncounted. Both failure modes are silent and both bias the
number DOWNWARD, which reads as "the metric improved".

A detector nobody tests is a number nobody can trust, so these pin the shapes it
must see and the shapes it must not count.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_lint

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "audit_auth_patch_sites.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("audit_auth_patch_sites", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_module()


def _sites(script, tmp_path: Path, body: str, *, auth_module: str, module_body: str):
    """Run ``collect_sites`` over a one-file fake tests tree and fake ``_auth``."""
    auth_dir = tmp_path / "_auth"
    auth_dir.mkdir()
    (auth_dir / "__init__.py").write_text("", encoding="utf-8")
    (auth_dir / f"{auth_module}.py").write_text(module_body, encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_fake.py").write_text(body, encoding="utf-8")
    return script.collect_sites(tests_dir, auth_dir)


_MODULE_BODY = "SEAM = None\n_PRIVATE_SEAM = None\n"

# Assembled rather than written literally. A literal ``patch("notebooklm…")`` in
# this file is indistinguishable, to a source-scanning gate, from a real
# string-target patch — and it correctly trips both ADR-0007 guardrails
# (``test_no_forbidden_monkeypatches`` and ``test_string_patch_ratchet``). This is
# fixture TEXT the detector under test parses, not a patch this file performs, so
# the honest fix is to keep the pattern out of the source rather than allowlist a
# file that patches nothing.
_STRING_TARGET_FIXTURE = (
    "from unittest.mock import patch\n"
    "def test_x():\n"
    "    patch(" + repr("notebooklm._auth.storage.SEAM") + ")\n"
)


@pytest.mark.parametrize(
    ("label", "body", "expected"),
    [
        (
            "positional-setattr",
            "from notebooklm._auth import storage\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr(storage, 'SEAM', 1)\n",
            {("storage", "SEAM")},
        ),
        # The regression this file exists for: both idioms take their first two
        # arguments by keyword, and a positional-only scan silently drops them.
        (
            "keyword-setattr",
            "from notebooklm._auth import storage\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr(target=storage, name='SEAM', value=1)\n",
            {("storage", "SEAM")},
        ),
        (
            "keyword-patch-object",
            "from unittest.mock import patch\n"
            "from notebooklm._auth import storage\n"
            "def test_x():\n"
            "    patch.object(target=storage, attribute='SEAM')\n",
            {("storage", "SEAM")},
        ),
        (
            "mixed-positional-and-keyword",
            "from notebooklm._auth import storage\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr(storage, name='SEAM', value=1)\n",
            {("storage", "SEAM")},
        ),
        (
            "plain-assignment-rebinding",
            "from notebooklm._auth import storage\ndef test_x():\n    storage.SEAM = 1\n",
            {("storage", "SEAM")},
        ),
        (
            "annotated-assignment-rebinding",
            "from notebooklm._auth import storage\ndef test_x():\n    storage.SEAM: int = 1\n",
            {("storage", "SEAM")},
        ),
        (
            "aliased-module-import",
            "from notebooklm._auth import storage as _st\n"
            "def test_x(monkeypatch):\n"
            "    monkeypatch.setattr(_st, '_PRIVATE_SEAM', 1)\n",
            {("storage", "_PRIVATE_SEAM")},
        ),
    ],
)
def test_counted_shapes(script, tmp_path, label, body, expected):
    sites = _sites(script, tmp_path, body, auth_module="storage", module_body=_MODULE_BODY)
    assert {(s.module, s.attribute) for s in sites} == expected


@pytest.mark.parametrize(
    ("label", "body"),
    [
        # A BARE annotation rebinds nothing — ``storage.SEAM: int`` is a type
        # statement, not a patch. Counting it would inflate the metric.
        (
            "bare-annotation-no-value",
            "from notebooklm._auth import storage\ndef test_x():\n    storage.SEAM: int\n",
        ),
        # String-target patching is a separately-banned idiom, not this metric's
        # subject. See _STRING_TARGET_FIXTURE for why it is assembled.
        ("string-target", _STRING_TARGET_FIXTURE),
        # A local that merely SHADOWS a module alias is not a module patch. The
        # alias map is file-global but Python bindings are function-scoped, so
        # without the module-level-name check this would be a false positive.
        (
            "local-shadowing-a-module-alias",
            "from notebooklm._auth import storage\n"
            "def test_x():\n"
            "    storage = object()\n"
            "    storage.NOT_A_REAL_MODULE_NAME = 1\n",
        ),
        ("unrelated-module", "import os\ndef test_x():\n    os.environ = {}\n"),
    ],
)
def test_uncounted_shapes(script, tmp_path, label, body):
    sites = _sites(script, tmp_path, body, auth_module="storage", module_body=_MODULE_BODY)
    assert sites == [], f"{label} must not be counted, got {sites}"


def test_private_and_public_are_split(script, tmp_path):
    body = (
        "from notebooklm._auth import storage\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(storage, 'SEAM', 1)\n"
        "    monkeypatch.setattr(storage, '_PRIVATE_SEAM', 1)\n"
    )
    summary = script.summarize(
        _sites(script, tmp_path, body, auth_module="storage", module_body=_MODULE_BODY)
    )
    assert summary["storage"] == {"public": 1, "private": 1, "total": 2}
    assert summary["TOTAL"]["total"] == 2


def test_missing_auth_dir_is_loud_not_a_silent_zero(script, tmp_path):
    """A renamed/missing ``_auth`` must not read as "the metric went down"."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_fake.py").write_text("", encoding="utf-8")
    with pytest.raises(SystemExit):
        script.main(["--tests-dir", str(tests_dir), "--auth-dir", str(tmp_path / "nope")])


def test_script_parses_and_exposes_its_contract(script):
    """Guards the loader itself: the API these tests drive must exist."""
    for name in ("collect_sites", "summarize", "main", "load_module_level_names"):
        assert hasattr(script, name), f"{name} disappeared from the audit script"
    ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
