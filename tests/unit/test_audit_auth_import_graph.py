"""Detector tests for the static ``notebooklm._auth`` import-graph audit."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.repo_lint

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "audit_auth_import_graph.py"


@pytest.fixture(scope="module")
def audit():
    spec = importlib.util.spec_from_file_location("audit_auth_import_graph", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _project(audit, tmp_path: Path, files: dict[str, str]):
    auth_dir = tmp_path / "src" / "notebooklm" / "_auth"
    auth_dir.mkdir(parents=True)
    for name, body in files.items():
        (auth_dir / f"{name}.py").write_text(body, encoding="utf-8")
    return audit.build_projection(auth_dir, repo_root=tmp_path)


def test_resolves_relative_absolute_aliased_and_package_imports(audit, tmp_path):
    result = _project(
        audit,
        tmp_path,
        {
            "__init__": "from . import alpha as renamed\n",
            "alpha": (
                "from .beta import VALUE as v\n"
                "from notebooklm._auth import gamma as g\n"
                "import notebooklm._auth.delta as d\n"
            ),
            "beta": "VALUE = 1\n",
            "gamma": "VALUE = 1\n",
            "delta": "VALUE = 1\n",
        },
    )
    assert result["edges"] == [
        {"source": "__init__", "target": "alpha", "scope": "module"},
        {"source": "alpha", "target": "beta", "scope": "module"},
        {"source": "alpha", "target": "delta", "scope": "module"},
        {"source": "alpha", "target": "gamma", "scope": "module"},
    ]
    assert result["modules"][0]["name"] == "__init__"


def test_scope_duplicate_self_and_scc_rules(audit, tmp_path):
    result = _project(
        audit,
        tmp_path,
        {
            "__init__": "",
            "a": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n    from . import b\n"
                "class C:\n    from . import b\n"
                "from . import a\n"
                "def outer():\n"
                "    from . import c\n"
                "    def inner():\n        from . import c\n"
            ),
            "b": "from . import a\n",
            "c": "def f():\n    from . import a\n",
        },
    )
    assert result["edges"] == [
        {"source": "a", "target": "b", "scope": "module"},
        {"source": "a", "target": "c", "scope": "function"},
        {"source": "b", "target": "a", "scope": "module"},
        {"source": "c", "target": "a", "scope": "function"},
    ]
    assert result["sccs"] == {
        "module_level": [["a", "b"]],
        "all_scopes": [["a", "b", "c"]],
    }


def test_live_projection_is_the_frozen_scorecard(audit):
    result = audit.build_projection()
    assert result["summary"] == {
        "modules": 26,
        "total_lines": 13874,
        "unique_edges": 69,
        "module_edges": 55,
        "function_local_edges": 14,
    }
    assert result["sccs"] == {
        "module_level": [],
        "all_scopes": [["cookies", "keepalive", "master_token", "psidts_recovery", "storage"]],
    }
    edges = {(edge["source"], edge["target"], edge["scope"]) for edge in result["edges"]}
    assert ("cookie_types", "cookies", "module") not in edges
    assert {
        ("cookie_types", "cookie_policy", "module"),
        ("cookie_types", "cookie_semantics", "module"),
        ("cookies", "cookie_types", "module"),
        ("cookies", "cookie_semantics", "module"),
    } <= edges
