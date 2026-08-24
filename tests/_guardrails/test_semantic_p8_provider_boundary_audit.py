"""Fail-closed inventory audit for Phase 8 (P8): the web cookie-provider boundary.

Governed by ADR-0035 and docs/plan/2026-08-13-semantic-backend-refactor.md (P8).
P8 extracts credential acquisition and persistence out of one open web backend
session and behind a ``WebCookieProvider``. Its acceptance criteria are
*negative* about the backend and *conservative* about everything else:

- the backend does not read profile files or launch interactive authentication;
- an injected provider is caller-owned, a convenience factory closes only what
  it created;
- profile file paths, locking, CAS, atomic writes, permissions, account routing,
  and secret redaction are unchanged unless separately reviewed;
- interactive login, browser-cookie capture, doctor, and profile management stay
  outside the backend;
- existing profile storage / refresh / recovery / master-token work is *adapted*
  behind the provider, never duplicated.

Every one of those is an inventory claim about who owns what today, so this
module pins those inventories and fails closed when they drift. It does NOT
demand P8 be implemented now: ``test_p8_provider_is_not_defined_yet`` asserts the
provider is still absent, so the first PR that introduces ``WebCookieProvider``
is forced to revisit this file deliberately instead of inheriting stale
baselines.

Runtime behaviour that P8 must equality-preserve is characterized separately in
``tests/unit/test_semantic_p8_provider_characterization.py``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, fields
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "notebooklm"
WEB_ROOT = SRC_ROOT / "_web"

pytestmark = pytest.mark.repo_lint

# --- P8 target symbols (absent until P8 lands) -------------------------------

#: The provider type P8 introduces. Absent today; see the module docstring.
P8_PROVIDER_SYMBOLS: frozenset[str] = frozenset({"WebCookieProvider"})

# --- Backend package inventory ----------------------------------------------

#: Exact set of first-party ``notebooklm.*`` modules the web backend package
#: imports today, as dotted names relative to ``notebooklm``. P8 adds the
#: provider to this set and nothing else: every name below is a wire/record/
#: policy dependency, not a credential dependency.
KNOWN_WEB_PACKAGE_FIRST_PARTY_IMPORTS: frozenset[str] = frozenset(
    {
        "_artifact.formatters",
        "_artifact.payloads",
        "_backend",
        "_chat.stream_decode",
        "_chat.stream_request",
        "_deadline",
        "_env",
        "_idempotency",
        "_logging",
        "_mind_map",
        "_note_service",
        "_notebook_payloads",
        "_operations",
        "_projectors",
        "_records",
        "_research_neutral",
        "_reqid_counter",
        "_request_types",
        "_row_adapters.artifacts",
        "_row_adapters.chat",
        "_row_adapters.documents",
        "_row_adapters.labels",
        "_row_adapters.notebooks",
        "_row_adapters.notes",
        "_row_adapters.research",
        "_row_adapters.sources",
        "_runtime.config",
        "_runtime.transport",
        "_rpc_executor",
        "_source.add",
        "_source.batch",
        "_source.content",
        "_source.listing",
        "_source.polling",
        "_source.upload_payloads",
        "_types.documents",
        "_types.sources",
        "_transport_errors",
        "_url_utils",
        "_web.backend",
        "_web.codec",
        "_web.chat",
        "_web.chat_transport",
        "_web.codec.artifacts",
        "_web.codec.chat",
        "_web.codec.chat_saved_note",
        "_web.codec.chat_stream",
        "_web.codec.collections",
        "_web.codec.documents",
        "_web.codec.labels",
        "_web.codec.mind_maps",
        "_web.codec.notebooks",
        "_web.codec.notes",
        "_web.codec.research",
        "_web.codec.settings",
        "_web.codec.sharing",
        "_web.codec.sources",
        "_web.codec.studio_documents",
        "_web.codec.suggestions",
        "_web.error_policy",
        "_web.labels",
        "_web.policy",
        "_web.registry",
        "_web.research",
        "_web.sharing",
        "_web.settings_suggestions",
        "_web.source_variants",
        "_web.studio_data",
        "_web.studio_documents",
        "_web.studio_facade",
        "_web.studio_media",
        "exceptions",
        "rpc",
        "rpc._safe_index",
        "rpc.decoder",
        "rpc.types",
        "types",
    }
)

#: Import prefixes that would mean the backend acquires or persists credentials
#: itself. P8's first acceptance criterion is that none of these ever appear
#: under ``src/notebooklm/_web/``; the provider is injected, not imported.
FORBIDDEN_WEB_IMPORT_PREFIXES: tuple[str, ...] = (
    "_app",
    "_atomic_io",
    "_auth",
    "_cookie_persistence",
    "_kernel",
    "auth",
    "cli",
    "io",
    "paths",
)

#: Identifiers that would mean the backend names credential material directly.
#: Checked at AST identifier granularity, not by substring, so prose like
#: "the only timeout authority" does not false-fire.
CREDENTIAL_IDENTIFIERS: frozenset[str] = frozenset(
    {
        "account_email",
        "account_route",
        "authuser",
        "cookie_jar",
        "cookie_snapshot",
        "cookies",
        "csrf_token",
        "master_token",
        "session_id",
        "storage_path",
        "storage_state",
    }
)

# --- Ownership inventories P8 must adapt rather than duplicate ---------------

#: Modules that reach the persisted profile document (``storage_state.json``)
#: through ``ProfileStore``, the sealed credential-commit capability, or the
#: stored-auth loaders. P8 adapts these behind the provider; the backend package
#: must never join this list.
KNOWN_PROFILE_DOCUMENT_OWNERS: frozenset[str] = frozenset(
    {
        "_atomic_io.py",
        "_auth/account_email.py",
        "_auth/account_repair.py",
        "_auth/browser_capture.py",
        "_auth/cookies.py",
        "_auth/master_token.py",
        "_auth/master_token_bootstrap.py",
        "_auth/master_token_file.py",
        "_auth/paths.py",
        "_auth/profile_migration.py",
        "_auth/profile_store.py",
        "_auth/psidts_recovery.py",
        "_auth/refresh.py",
        "_auth/storage.py",
        "_auth/tokens.py",
        "_cookie_persistence.py",
        "_runtime/init.py",
        "_runtime/lifecycle.py",
        "auth.py",
        "client.py",
    }
)

#: Modules that can drive a browser (interactive login, browser-cookie capture,
#: headless re-mint, doctor). P8 keeps every one of them OUTSIDE the backend.
KNOWN_INTERACTIVE_AUTH_OWNERS: frozenset[str] = frozenset(
    {
        "_app/profile.py",
        "_auth/_browser_cookie_filter.py",
        "_auth/account.py",
        "_auth/account_types.py",
        "_auth/browser_capture.py",
        "_auth/browser_launch_errors.py",
        "_auth/cookies.py",
        "_auth/headless_reauth.py",
        "_auth/session.py",
        "auth.py",
        "cli/_cookie_import.py",
        "cli/doctor_cmd.py",
        "cli/playwright_login_io.py",
        "cli/services/auth_refresh.py",
        "cli/services/login/io_seam.py",
        "cli/services/login/master_token.py",
        "cli/services/playwright_login.py",
        "cli/services/playwright_redaction.py",
        "cli/session_cmd.py",
    }
)

#: The four credential lock siblings, all derived from one helper
#: (``_auth.paths._lock_sibling``). They must stay four DISTINCT files: the
#: bootstrap lock is held across the storage lock's acquire, and ``flock``
#: conflicts between two open file descriptions inside one process. P8 keeps
#: this derivation unchanged.
EXPECTED_LOCK_SIBLING_KINDS: frozenset[str] = frozenset(
    {"lock", "rotate.lock", "refresh.lock", "lock.bootstrap"}
)

#: ``AuthTokens`` fields suppressed from the dataclass-generated ``repr``
#: because they are credential-equivalent. The immutable generation P8 returns
#: from the provider inherits this obligation.
EXPECTED_AUTH_TOKENS_REPR_SUPPRESSED: frozenset[str] = frozenset(
    {
        "cookies",
        "csrf_token",
        "session_id",
        "cookie_jar",
        "cookie_snapshot",
        "_profile_session_generation",
    }
)

#: Whole audited master-token TRANSACTIONS the CLI/app layer invokes through the
#: ``notebooklm.auth`` facade. P8 reuses these; it does not re-derive minting.
EXPECTED_MASTER_TOKEN_TRANSACTIONS: frozenset[str] = frozenset(
    {
        "assert_account_writable",
        "bootstrap_missing_storage_from_master_token",
        "master_token_bootstrap",
        "master_token_remint",
    }
)


@dataclass(frozen=True, slots=True)
class P8EntryReport:
    """Structured view of the P8 boundary as it stands today."""

    provider_defined: bool
    backend_first_party_imports: list[str]
    backend_credential_imports: list[str]
    backend_credential_identifiers: list[str]
    profile_document_owners: list[str]
    interactive_auth_owners: list[str]
    notes: list[str]


# --- Detectors ---------------------------------------------------------------


def _resolve_import(node: ast.ImportFrom | ast.Import, *, package_parts: list[str]) -> list[str]:
    """Return dotted module names relative to ``notebooklm`` for one import node."""
    if isinstance(node, ast.Import):
        return [
            alias.name.removeprefix("notebooklm.")
            for alias in node.names
            if alias.name == "notebooklm" or alias.name.startswith("notebooklm.")
        ]
    if node.level == 0:
        module = node.module or ""
        if module == "notebooklm" or module.startswith("notebooklm."):
            return [module.removeprefix("notebooklm.")]
        return []
    # ``level`` counts up from the importing module's own package.
    base = package_parts[: len(package_parts) - (node.level - 1)]
    tail = (node.module or "").split(".") if node.module else []
    return [".".join([*base, *tail])] if (base or tail) else []


def collect_first_party_imports(package_root: Path, src_root: Path = SRC_ROOT) -> set[str]:
    """Collect first-party ``notebooklm.*`` imports made by one package."""
    imports: set[str] = set()
    for path in sorted(package_root.rglob("*.py")):
        package_parts = list(path.relative_to(src_root).parent.parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imports.update(
                    name for name in _resolve_import(node, package_parts=package_parts) if name
                )
    return imports


def credential_imports(imports: set[str]) -> set[str]:
    """Select imports that would give a module credential acquisition powers."""
    return {
        name
        for name in imports
        if any(
            name == prefix or name.startswith(f"{prefix}.")
            for prefix in FORBIDDEN_WEB_IMPORT_PREFIXES
        )
    }


def collect_named_identifiers(package_root: Path) -> set[str]:
    """Collect attribute and bare-name identifiers used anywhere in a package."""
    names: set[str] = set()
    for path in sorted(package_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, (ast.arg, ast.keyword)) and node.arg is not None:
                names.add(node.arg)
    return names


def collect_modules_matching(
    needles: frozenset[str],
    src_root: Path = SRC_ROOT,
) -> set[str]:
    """Return repo-relative module paths whose source mentions any needle."""
    found: set[str] = set()
    for path in sorted(src_root.rglob("*.py")):
        content = path.read_text(encoding="utf-8")
        if any(needle in content for needle in needles):
            found.add(path.relative_to(src_root).as_posix())
    return found


def collect_symbol_definitions(symbols: frozenset[str], src_root: Path = SRC_ROOT) -> set[str]:
    """Return ``module::symbol`` for every class/function definition named in ``symbols``."""
    defined: set[str] = set()
    for path in sorted(src_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in symbols
            ):
                defined.add(f"{path.relative_to(src_root).as_posix()}::{node.name}")
    return defined


def evaluate_p8_boundary(
    src_root: Path = SRC_ROOT,
    web_root: Path = WEB_ROOT,
) -> P8EntryReport:
    """Inventory the P8 provider boundary as it stands today."""
    backend_imports = collect_first_party_imports(web_root, src_root=src_root)
    forbidden = credential_imports(backend_imports)
    identifiers = collect_named_identifiers(web_root) & CREDENTIAL_IDENTIFIERS
    provider = collect_symbol_definitions(P8_PROVIDER_SYMBOLS, src_root=src_root)

    notes: list[str] = []
    if not provider:
        notes.append(
            "WebCookieProvider is not defined yet: P8 has not started, and the "
            "backend still borrows the client-owned RpcExecutor/Kernel session"
        )
    if forbidden:
        notes.append(
            "web backend package imports credential-acquisition modules: "
            + ", ".join(sorted(forbidden))
        )
    if identifiers:
        notes.append(
            "web backend package names credential identifiers: " + ", ".join(sorted(identifiers))
        )

    return P8EntryReport(
        provider_defined=bool(provider),
        backend_first_party_imports=sorted(backend_imports),
        backend_credential_imports=sorted(forbidden),
        backend_credential_identifiers=sorted(identifiers),
        profile_document_owners=sorted(
            collect_modules_matching(
                frozenset(
                    {"ProfileStore", "credential_io", "_load_stored_auth", "_load_storage_state"}
                ),
                src_root=src_root,
            )
        ),
        interactive_auth_owners=sorted(
            collect_modules_matching(frozenset({"playwright"}), src_root=src_root)
        ),
        notes=notes,
    )


# --- Test suite --------------------------------------------------------------


def test_p8_provider_is_not_defined_yet() -> None:
    """P8 has not started; the provider symbol must not exist in production yet.

    When P8 lands this fails on purpose. Updating it is the checkpoint that
    forces every baseline in this module to be re-derived deliberately rather
    than inherited stale.
    """
    report = evaluate_p8_boundary()
    assert not report.provider_defined, (
        "WebCookieProvider now exists — re-derive every inventory in this module "
        "against the new provider boundary before relaxing this assertion."
    )
    assert any("not defined yet" in note for note in report.notes)


def test_web_backend_first_party_imports_are_exact_and_fail_closed() -> None:
    """The backend package's first-party dependency set is baselined."""
    actual = collect_first_party_imports(WEB_ROOT)
    added = actual - KNOWN_WEB_PACKAGE_FIRST_PARTY_IMPORTS
    removed = KNOWN_WEB_PACKAGE_FIRST_PARTY_IMPORTS - actual

    assert not added, (
        "New first-party imports in src/notebooklm/_web/ — classify them before P8:\n  "
        + "\n  ".join(sorted(added))
    )
    assert not removed, (
        "Imports disappeared from src/notebooklm/_web/; update the baseline:\n  "
        + "\n  ".join(sorted(removed))
    )


def test_web_backend_does_not_reach_credential_acquisition() -> None:
    """P8 criterion: the backend reads no profile file and starts no login."""
    report = evaluate_p8_boundary()
    assert not report.backend_credential_imports, (
        "src/notebooklm/_web/ must receive an injected provider, never import "
        "credential acquisition/persistence itself:\n  "
        + "\n  ".join(report.backend_credential_imports)
    )


def test_web_backend_names_no_credential_identifiers() -> None:
    """The backend never names cookie, token, route, or profile-path material."""
    report = evaluate_p8_boundary()
    assert not report.backend_credential_identifiers, (
        "src/notebooklm/_web/ names credential identifiers directly:\n  "
        + "\n  ".join(report.backend_credential_identifiers)
    )


def test_profile_document_owner_inventory_is_exact_and_excludes_the_backend() -> None:
    """Profile-document owners are baselined; P8 adapts them, never duplicates."""
    actual = collect_modules_matching(
        frozenset({"ProfileStore", "credential_io", "_load_stored_auth", "_load_storage_state"})
    )
    added = actual - KNOWN_PROFILE_DOCUMENT_OWNERS
    removed = KNOWN_PROFILE_DOCUMENT_OWNERS - actual

    assert not added, (
        "New profile-document readers/writers — P8 must adapt the existing owners, "
        "not add new ones:\n  " + "\n  ".join(sorted(added))
    )
    assert not removed, (
        "Profile-document owners disappeared; update the baseline:\n  "
        + "\n  ".join(sorted(removed))
    )
    assert not {name for name in actual if name.startswith("_web/")}, (
        "The web backend package must not reach the profile document"
    )


def test_interactive_auth_stays_outside_the_backend() -> None:
    """Interactive login, browser capture, headless re-mint, and doctor stay out."""
    actual = collect_modules_matching(frozenset({"playwright"}))
    added = actual - KNOWN_INTERACTIVE_AUTH_OWNERS
    removed = KNOWN_INTERACTIVE_AUTH_OWNERS - actual

    assert not added, (
        "New browser-driving modules — P8 keeps interactive auth outside the "
        "backend:\n  " + "\n  ".join(sorted(added))
    )
    assert not removed, (
        "Browser-driving modules disappeared; update the baseline:\n  "
        + "\n  ".join(sorted(removed))
    )
    assert not {name for name in actual if name.startswith("_web/")}, (
        "The web backend package must not launch interactive authentication"
    )


def test_credential_lock_siblings_share_one_derivation_and_stay_distinct() -> None:
    """The four credential locks derive from one helper and remain four files."""
    from notebooklm._auth import paths as auth_paths

    base = Path("/tmp/profile/storage_state.json")
    derived = {
        auth_paths._storage_state_lock_path(base),
        auth_paths._rotation_lock_path(base),
        auth_paths._refresh_lock_path(base),
        auth_paths._bootstrap_lock_path(base),
    }
    assert len(derived) == 4, f"credential locks collapsed onto the same file: {derived}"

    kinds = {
        path.name.removeprefix(f".{base.name}.")
        for path in derived
        # the bootstrap lock canonicalizes its base, so match on suffix only
    }
    assert kinds == EXPECTED_LOCK_SIBLING_KINDS, f"lock kinds drifted: {sorted(kinds)}"

    # One derivation, so a new lock kind cannot invent its own spelling.
    source = (SRC_ROOT / "_auth" / "paths.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    sibling_callers = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_lock_sibling" in sibling_callers


def test_bare_atomic_write_still_refuses_the_profile_document() -> None:
    """CAS/lock discipline: the unlocked atomic write refuses storage_state.json."""
    from notebooklm._atomic_io import atomic_write_json

    with pytest.raises(ValueError, match="storage_state.json"):
        atomic_write_json(Path("/tmp/does-not-matter/Storage_State.JSON"), {})


def test_credential_commit_capability_stays_sealed_behind_credential_io() -> None:
    """The unchecked atomic primitive keeps exactly one importer."""
    importers = {
        path.relative_to(SRC_ROOT).as_posix()
        for path in SRC_ROOT.rglob("*.py")
        if path.name != "_atomic_io.py"
        and "_atomic_write_json_unchecked" in path.read_text(encoding="utf-8")
    }
    assert importers == {"_auth/credential_io.py"}, (
        f"credential commit capability leaked to: {sorted(importers)}"
    )


def test_auth_tokens_credential_fields_are_repr_suppressed() -> None:
    """Secret redaction: credential fields never reach the generated repr.

    The immutable cookie/account-route generation P8 returns from the provider
    inherits this obligation; pinning the field set here makes a new credential
    field that forgets ``repr=False`` fail closed.
    """
    from notebooklm.auth import AuthTokens

    suppressed = {field.name for field in fields(AuthTokens) if not field.repr}
    assert suppressed == EXPECTED_AUTH_TOKENS_REPR_SUPPRESSED, (
        f"AuthTokens repr-suppressed field set drifted: {sorted(suppressed)}"
    )
    assert "__repr__" in vars(AuthTokens), "AuthTokens must keep its redacting __repr__"


def test_master_token_transactions_are_reused_not_reimplemented() -> None:
    """P8 adapts the audited master-token transactions behind the provider."""
    from notebooklm import auth as auth_facade

    missing = {
        name for name in EXPECTED_MASTER_TOKEN_TRANSACTIONS if not hasattr(auth_facade, name)
    }
    assert not missing, f"master-token transactions disappeared from the facade: {sorted(missing)}"

    # ``MintService`` is the one wire implementation; ``mint_cookies`` is its
    # v0.x composition adapter. Two definitions, one implementation — a second
    # of either would mean P8 re-derived minting instead of adapting it.
    minting_owners = collect_symbol_definitions(frozenset({"MintService", "mint_cookies"}))
    assert minting_owners == {
        "_auth/master_token.py::mint_cookies",
        "_auth/mint_service.py::MintService",
    }, f"cookie minting gained a second implementation: {sorted(minting_owners)}"


# --- Detector self-tests (fail-closed mutation tests) ------------------------


def test_detector_flags_a_backend_that_imports_credentials(tmp_path: Path) -> None:
    """A backend package importing ``.._auth.storage`` is reported as a blocker."""
    src = tmp_path / "notebooklm"
    web = src / "_web"
    web.mkdir(parents=True)
    (web / "backend.py").write_text(
        "from .._auth.storage import read_account_metadata\n", encoding="utf-8"
    )
    report = evaluate_p8_boundary(src_root=src, web_root=web)
    assert report.backend_credential_imports == ["_auth.storage"]
    assert any("credential-acquisition modules" in note for note in report.notes)


def test_detector_flags_a_backend_that_names_credentials(tmp_path: Path) -> None:
    """A backend naming ``csrf_token``/``cookie_jar`` is reported as a blocker."""
    src = tmp_path / "notebooklm"
    web = src / "_web"
    web.mkdir(parents=True)
    (web / "backend.py").write_text(
        "def build(session):\n    return session.cookie_jar, session.csrf_token\n",
        encoding="utf-8",
    )
    report = evaluate_p8_boundary(src_root=src, web_root=web)
    assert report.backend_credential_identifiers == ["cookie_jar", "csrf_token"]
    assert any("names credential identifiers" in note for note in report.notes)


def test_detector_reports_provider_once_defined(tmp_path: Path) -> None:
    """Defining ``WebCookieProvider`` flips the report's readiness note."""
    src = tmp_path / "notebooklm"
    web = src / "_web"
    web.mkdir(parents=True)
    (web / "provider.py").write_text("class WebCookieProvider:\n    pass\n", encoding="utf-8")
    report = evaluate_p8_boundary(src_root=src, web_root=web)
    assert report.provider_defined is True
    assert not any("not defined yet" in note for note in report.notes)
