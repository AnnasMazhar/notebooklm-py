"""Fail-closed audit and characterization for Phase 7 (P7) entry criteria.

Governed by ADR-0035 and docs/plan/2026-08-13-semantic-backend-refactor.md.
P7 runs last: no runtime collapse is authorized until P1-P6 have isolated
semantic feature callers from RpcCaller (or recorded explicit legacy exceptions),
ErrorInjectionMiddleware is rehomed, and test mutations of mutable chain internals
have migrated.

This audit checks all P7 entry criteria, enumerates current blockers, and fails
closed if entry criteria or internal consumer inventories drift unexpectedly.
It does NOT demand P7 pass now.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "notebooklm"
TESTS_ROOT = REPO_ROOT / "tests"
GUARDRAILS_ROOT = TESTS_ROOT / "_guardrails"

pytestmark = pytest.mark.repo_lint

# Maximum allowed legacy_exception catalog rows per ADR-0035 / Plan line 1385.
MAX_ALLOWED_LEGACY_EXCEPTIONS = 5

# Exact inventory of classes/functions in src/notebooklm/ taking RpcCaller.
# These represent the remaining semantic services/facades that must migrate in P2-P6
# before P7 runtime collapse can begin.
KNOWN_RPC_CALLER_CONSUMERS: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("_artifact/downloads.py", "ArtifactDownloadService.__init__", "rpc"),
        ("_artifact/generation.py", "ArtifactGenerationService.__init__", "rpc"),
        ("_artifacts.py", "ArtifactsAPI.__init__", "rpc"),
        ("_chat/api.py", "ChatAPI.__init__", "rpc"),
        ("_collections.py", "CollectionsAPI.__init__", "rpc"),
        ("_labels.py", "LabelsAPI.__init__", "rpc"),
        ("_mind_maps_api.py", "MindMapsAPI.__init__", "rpc"),
        ("_note_service.py", "LegacyNoteBackedService.__init__", "rpc"),
        ("_notebook_metadata.py", "create_default_source_lister", "rpc"),
        ("_notebooks.py", "NotebooksAPI.__init__", "rpc"),
        ("_research.py", "ResearchAPI.__init__", "rpc"),
        ("_settings.py", "SettingsAPI.__init__", "rpc"),
        ("_sharing.py", "SharingAPI.__init__", "rpc"),
        ("_sharing_manager.py", "ShareManager.__init__", "rpc"),
        ("_source/add.py", "SourceAddService.add_drive", "rpc"),
        ("_source/add.py", "SourceAddService.add_text", "rpc"),
        ("_source/add.py", "SourceAddService.add_url_source", "rpc"),
        ("_source/add.py", "SourceAddService.add_youtube_source", "rpc"),
        ("_source/batch.py", "SourceBatchAddService.add_urls", "rpc"),
        ("_source/content.py", "SourceContentRenderer.__init__", "rpc"),
        ("_source/listing.py", "SourceLister.__init__", "rpc"),
        ("_source/upload.py", "SourceUploadPipeline.__init__", "rpc"),
        ("_sources.py", "SourcesAPI.__init__", "rpc"),
    }
)

# Known test files outside tests/_guardrails/ reaching mutable chain or composed internals.
# P7 entry requires these test seams migrated to backend/provider/clock seams before P7 collapses them.
KNOWN_CHAIN_COMPOSED_TEST_FILES: frozenset[str] = frozenset(
    {
        "integration/concurrency/test_cross_loop_affinity.py",
        "unit/test_composition_primitives.py",
        "unit/test_lifecycle_executor_reuse.py",
        "unit/test_runtime_auth.py",
        "unit/test_semantic_p7_runtime_characterization.py",
    }
)

KNOWN_CHAIN_HOST_TEST_FILES: frozenset[str] = frozenset(
    {
        "_baselines/compatibility_contracts.py",
        "integration/concurrency/test_refresh_cancellation_propagation.py",
        "unit/test_authed_post_pipeline.py",
        "unit/test_chain_wiring.py",
        "unit/test_composition_primitives.py",
        "unit/test_concurrency_refresh_race.py",
        "unit/test_middleware_chain_host.py",
        "unit/test_observability.py",
        "unit/test_semantic_p7_runtime_characterization.py",
    }
)


@dataclass(frozen=True, slots=True)
class LegacyException:
    operation: str
    approver: str
    issue: str


@dataclass(frozen=True, slots=True)
class P7EntryReport:
    ready: bool
    remaining_rpc_consumers: list[tuple[str, str, str]]
    legacy_exceptions: list[LegacyException]
    error_injection_blocked: bool
    chain_composed_test_files: list[str]
    chain_host_test_files: list[str]
    blockers: list[str]


def collect_rpc_caller_consumers(src_dir: Path = SRC_ROOT) -> set[tuple[str, str, str]]:
    """Scan src/notebooklm/ for classes and functions accepting RpcCaller annotations."""
    consumers: set[tuple[str, str, str]] = set()
    for path in sorted(src_dir.rglob("*.py")):
        rel_posix = path.relative_to(src_dir).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        class ConsumerVisitor(ast.NodeVisitor):
            def __init__(self, module: str) -> None:
                self.module = module
                self.owners: list[str] = []

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self.owners.append(node.name)
                self.generic_visit(node)
                self.owners.pop()

            def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
                for arg in node.args.args + node.args.kwonlyargs:
                    if arg.annotation and "RpcCaller" in ast.unparse(arg.annotation):
                        owner = ".".join((*self.owners, node.name))
                        consumers.add((self.module, owner, arg.arg))
                self.owners.append(node.name)
                self.generic_visit(node)
                self.owners.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._visit_function(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._visit_function(node)

        ConsumerVisitor(rel_posix).visit(tree)
    return consumers


def collect_legacy_exceptions(
    operation_specs: Sequence[Any] | None = None,
) -> list[LegacyException]:
    """Collect legacy_exception declarations from operation specs."""
    if operation_specs is None:
        try:
            from scripts._operation_catalog_specs import OPERATION_SPECS

            operation_specs = OPERATION_SPECS
        except ImportError:
            operation_specs = []

    exceptions: list[LegacyException] = []
    for spec in operation_specs:
        legacy = getattr(spec, "legacy_exception", None)
        if legacy is not None:
            approver = getattr(legacy, "approver", "") or ""
            issue = getattr(legacy, "issue", "") or ""
            op_key = getattr(spec, "operation", None)
            op_name = op_key.value if op_key is not None else str(spec)
            exceptions.append(LegacyException(operation=op_name, approver=approver, issue=issue))
    return exceptions


def check_error_injection_middleware_dependency(
    middleware_path: Path = SRC_ROOT / "_middleware" / "error_injection.py",
) -> bool:
    """Check if ErrorInjectionMiddleware still imports from _middleware.core."""
    if not middleware_path.exists():
        return False
    tree = ast.parse(middleware_path.read_text(encoding="utf-8"), filename=str(middleware_path))
    core_imports = {"NextCall", "RpcRequest", "RpcResponse", "core"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in {".core", "notebooklm._middleware.core", "._middleware.core"}:
                return True
            if any(alias.name in core_imports for alias in node.names):
                return True
    return False


def collect_mutable_chain_test_files(
    tests_dir: Path = TESTS_ROOT,
) -> tuple[set[str], set[str]]:
    """Scan tests/ outside _guardrails/ for reaches into ClientComposed or MiddlewareChainHost."""
    composed_files: set[str] = set()
    chain_host_files: set[str] = set()

    for path in sorted(tests_dir.rglob("*.py")):
        if GUARDRAILS_ROOT in path.parents or path.parent == GUARDRAILS_ROOT:
            continue
        rel_posix = path.relative_to(tests_dir).as_posix()
        content = path.read_text(encoding="utf-8")
        if "ClientComposed" in content:
            composed_files.add(rel_posix)
        if (
            "MiddlewareChainHost" in content
            or "_authed_post_chain" in content
            or "_authed_post_chain_terminal" in content
        ):
            chain_host_files.add(rel_posix)

    return composed_files, chain_host_files


def evaluate_p7_entry_readiness(
    src_dir: Path = SRC_ROOT,
    tests_dir: Path = TESTS_ROOT,
    operation_specs: Sequence[Any] | None = None,
) -> P7EntryReport:
    """Evaluate all P7 entry criteria and return a structured audit report."""
    rpc_consumers = sorted(collect_rpc_caller_consumers(src_dir))
    legacy_exceptions = collect_legacy_exceptions(operation_specs)
    ei_blocked = check_error_injection_middleware_dependency(
        src_dir / "_middleware" / "error_injection.py"
    )
    composed_tests, chain_host_tests = collect_mutable_chain_test_files(tests_dir)

    blockers: list[str] = []

    if rpc_consumers:
        blockers.append(
            f"{len(rpc_consumers)} semantic-service call sites still consume RpcCaller directly"
        )

    if len(legacy_exceptions) > MAX_ALLOWED_LEGACY_EXCEPTIONS:
        blockers.append(
            f"legacy_exceptions count ({len(legacy_exceptions)}) exceeds maximum allowed "
            f"ceiling of {MAX_ALLOWED_LEGACY_EXCEPTIONS}"
        )

    for exc in legacy_exceptions:
        if not exc.approver or not exc.issue:
            blockers.append(
                f"legacy_exception for operation {exc.operation!r} must specify both an approver and an open removal issue"
            )

    if ei_blocked:
        blockers.append(
            "ErrorInjectionMiddleware still imports from _middleware.core (must be migrated/rehomed before P7)"
        )

    if composed_tests:
        blockers.append(
            f"{len(composed_tests)} test files outside _guardrails/ still construct or reference ClientComposed"
        )

    if chain_host_tests:
        blockers.append(
            f"{len(chain_host_tests)} test files outside _guardrails/ still reach into MiddlewareChainHost"
        )

    return P7EntryReport(
        ready=len(blockers) == 0,
        remaining_rpc_consumers=rpc_consumers,
        legacy_exceptions=legacy_exceptions,
        error_injection_blocked=ei_blocked,
        chain_composed_test_files=sorted(composed_tests),
        chain_host_test_files=sorted(chain_host_tests),
        blockers=blockers,
    )


# --- Test Suite -------------------------------------------------------------


def test_p7_entry_criteria_blockers_enumeration() -> None:
    """P7 entry is currently blocked; report must accurately enumerate all blockers."""
    report = evaluate_p7_entry_readiness()

    assert not report.ready, "P7 cannot be ready before P1-P6 migrations complete"
    assert len(report.blockers) >= 4, f"Expected at least 4 blocker classes, got: {report.blockers}"

    # Check each blocker category is explicitly reported
    blocker_text = "\n".join(report.blockers)
    assert "semantic-service call sites still consume RpcCaller" in blocker_text
    assert "ErrorInjectionMiddleware still imports from _middleware.core" in blocker_text
    assert "ClientComposed" in blocker_text
    assert "MiddlewareChainHost" in blocker_text


def test_rpccaller_consumer_inventory_is_exact_and_fails_closed() -> None:
    """The set of RpcCaller consumers in src/notebooklm/ matches known baseline."""
    actual_consumers = collect_rpc_caller_consumers()
    unclassified_new = actual_consumers - KNOWN_RPC_CALLER_CONSUMERS
    removed = KNOWN_RPC_CALLER_CONSUMERS - actual_consumers

    assert not unclassified_new, (
        "New, unclassified RpcCaller consumers found in src/notebooklm/:\n  "
        + "\n  ".join(f"{p}:{fn}({arg})" for p, fn, arg in sorted(unclassified_new))
    )
    # When P2-P6 migrate consumers, update KNOWN_RPC_CALLER_CONSUMERS deliberately
    assert not removed, (
        "Migrated RpcCaller consumers must be removed from KNOWN_RPC_CALLER_CONSUMERS:\n  "
        + "\n  ".join(f"{p}:{fn}({arg})" for p, fn, arg in sorted(removed))
    )


def test_legacy_exception_policy_and_ceiling() -> None:
    """Legacy exception rows must be <= 5 and carry valid approver + issue."""
    exceptions = collect_legacy_exceptions()
    assert len(exceptions) <= MAX_ALLOWED_LEGACY_EXCEPTIONS, (
        f"Too many legacy exceptions: {len(exceptions)} > {MAX_ALLOWED_LEGACY_EXCEPTIONS}"
    )
    for exc in exceptions:
        assert exc.approver, f"Legacy exception {exc.operation} missing approver"
        assert exc.issue, f"Legacy exception {exc.operation} missing issue"


def test_error_injection_middleware_imports_blocked_for_p7() -> None:
    """ErrorInjectionMiddleware is confirmed dependent on _middleware.core (blocker for P7)."""
    assert check_error_injection_middleware_dependency() is True


def test_mutable_runtime_test_reach_inventory_is_baselined_and_fails_closed() -> None:
    """Test files outside _guardrails/ touching ClientComposed or ChainHost are baselined."""
    composed_tests, chain_host_tests = collect_mutable_chain_test_files()

    new_composed = composed_tests - KNOWN_CHAIN_COMPOSED_TEST_FILES
    new_chain_host = chain_host_tests - KNOWN_CHAIN_HOST_TEST_FILES

    assert not new_composed, (
        "New tests reaching ClientComposed outside _guardrails/:\n  "
        + "\n  ".join(sorted(new_composed))
    )
    assert not new_chain_host, (
        "New tests reaching MiddlewareChainHost outside _guardrails/:\n  "
        + "\n  ".join(sorted(new_chain_host))
    )


# --- Detector Self-Tests (Fail-Closed Mutation Tests) ------------------------


def test_detector_fails_closed_when_legacy_exceptions_exceed_ceiling() -> None:
    """A catalog with > 5 legacy exceptions triggers a blocker."""
    fake_specs = [
        type(
            "Spec",
            (),
            {
                "operation": type("Op", (), {"value": f"op.{i}"}),
                "legacy_exception": type("Legacy", (), {"approver": "owner", "issue": "#123"}),
            },
        )()
        for i in range(6)
    ]
    report = evaluate_p7_entry_readiness(operation_specs=fake_specs)
    assert any("exceeds maximum allowed ceiling of 5" in b for b in report.blockers)


def test_detector_fails_closed_when_legacy_exception_missing_approver_or_issue() -> None:
    """A legacy exception without approver or issue triggers a blocker."""
    fake_specs = [
        type(
            "Spec",
            (),
            {
                "operation": type("Op", (), {"value": "op.test"}),
                "legacy_exception": type("Legacy", (), {"approver": "", "issue": "#123"}),
            },
        )(),
        type(
            "Spec",
            (),
            {
                "operation": type("Op", (), {"value": "op.test2"}),
                "legacy_exception": type("Legacy", (), {"approver": "owner", "issue": ""}),
            },
        )(),
    ]
    report = evaluate_p7_entry_readiness(operation_specs=fake_specs)
    assert any(
        "must specify both an approver and an open removal issue" in b for b in report.blockers
    )
