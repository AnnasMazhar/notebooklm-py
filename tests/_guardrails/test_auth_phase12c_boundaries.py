"""Fail-closed executable boundary for ADR-0034 Phase 12C owners."""

from __future__ import annotations

import ast
import hashlib
import inspect
from pathlib import Path

import pytest

import notebooklm.auth as auth_facade
from notebooklm._auth import (
    account,
    account_types,
    cookie_types,
    cookies,
    keepalive,
    master_token,
    master_token_types,
    psidts_recovery,
    recovery,
    single_flight,
    storage,
)

pytestmark = pytest.mark.repo_lint

REPO_ROOT = Path(__file__).resolve().parents[2]
AUTH_ROOT = REPO_ROOT / "src" / "notebooklm" / "_auth"

_MODULE_HASHES = {
    "account.py": "e1d16161d93551976528948e51e2b11e412169ba88a8b83bbc9acea433cd7714",
    "account_repair.py": "2f77334ad453883e2b0b8ee5c20a043d0d3ce6797ba37b4256cf35a4199bcbd7",
    "account_types.py": "8526f2b268c8bb56b138fab791b7e8a456f9ddea092c998b770caa31a94a7fa1",
    "cookie_types.py": "093932e373c737051a14f1f36358661ccfa12631e2fe49cf892bf3fda34f0ec1",
    "cookies.py": "7b4d86c01e54ef169d5348ed2f99eae62465b90c99697e138d548d752f8567f4",
    "keepalive.py": "2ad1f324336e9309aad7f994747541878f6480dbcaee0f1c63d3cf3c86a4844b",
    "master_token.py": "f92d4781bd23a5ab0454540ad066388647bde3890968741060af4b43919bedfd",
    "master_token_types.py": "a334509070a06f7fa2fd00ddf739a36d3bf6e324205fe8e5758012b56863c5ee",
    "psidts_recovery.py": "b8b6235966af52d3fbe89de5789942cffd21c68d280cac9bc68939ca8bc2101f",
    "recovery.py": "a18e29629b273a3a7f4ae9660d088a7717b1054659f4eed8cf9bb6a61441dd89",
    "refresh.py": "0942b3a21907e49a4d50c46aaba28e800a9a574a6b50cb4458f6ce59319a19be",
    "single_flight.py": "9c394dc83e3866b624965801d55e43e05c2b133540044021c7333970b5dbb1c3",
    "storage.py": "0dc7916fc7119b270a1ede481b4b8d95c3b0a051a2796853754bee9f18ecc5bb",
}

_NODE_HASHES = {
    (
        "single_flight.py",
        "Flight",
    ): "5cab0ff7f8fc8c43cdc4795c1fb17ad93c9b95faf2503fa4df9f4f62644fc40d",
    (
        "single_flight.py",
        "SingleFlight",
    ): "64b8b2b6dde362684e3a5a92036a7286daa758f276215f26df79cdd2dc5f8186",
    (
        "single_flight.py",
        "read_success_epoch",
    ): "038272f52e0a6224062161f76af2b4f61e8f86e567af393c28d72d2368c84e37",
    (
        "single_flight.py",
        "note_success",
    ): "ef8d42cd51eb204b133a01b6ed4bec5c18a7967e27b6f235879ae279eb082e06",
    (
        "single_flight.py",
        "claim",
    ): "64f17feb280cb1249570c9ec6910d01edcf184bc26377acc085a3af76e6d188b",
    (
        "single_flight.py",
        "claim_if_epoch_current",
    ): "fb2be386663fa24508e5e8ae9b62a6bdb5fcce1b8ecb94b29c1c9ecbacf30a66",
    (
        "single_flight.py",
        "await_flight",
    ): "008d8877893f899c6f70d1e45073670c5ce0f7d2a4f81d2f4c87d338d49f213e",
    (
        "single_flight.py",
        "_reset_for_tests",
    ): "40b255fd4acf84f022a68c36fa262677777dc164c708ad5daa36b56c01979e60",
    (
        "recovery.py",
        "ColdRecoveryResult",
    ): "b75fa89d9dba115b50a20c1aa66c3f3a3777701e41f86ccd233385ea6df8a0dd",
    (
        "recovery.py",
        "_ColdRecoveryExhaustion",
    ): "982629b8a180ed9c1998e3775915b90f6c5497176b2e9162fae956edb1bcee8a",
    (
        "recovery.py",
        "ColdRecoveryState",
    ): "ea85ae8e6132ddef70b458e5003cd16cc150ede5a3e6d392ec53c11e687ed278",
    (
        "recovery.py",
        "ColdRecoveryCoordinator",
    ): "f4a57d4c6259d02b91891de4164532ee25b2def6881fa52742d23e8440abf30c",
    (
        "recovery.py",
        "_run_cold_recovery",
    ): "8df2f7e244188d3ec2084ff510500f6264a3a920ce023fff0e34f9c81bc937b4",
    (
        "recovery.py",
        "coalesced_cold_recovery",
    ): "adb689f1db8ea0c635384c00e31addf103a5daea0b3ea19ca6d01e61be3089a9",
    (
        "keepalive.py",
        "RotationState",
    ): "60cf00b90fe5ddaf986772413b2391d05fb096e93409fc5950e3f8b8d180c395",
    (
        "keepalive.py",
        "_get_poke_lock",
    ): "dd9199ebafa219877b3ff1b70d8928db34f3e2574cc1a2112fd87e64a0ac7e2d",
    (
        "keepalive.py",
        "_try_claim_rotation",
    ): "2de33b5f33f76c174741ba0cbb5b39db8fd74f496cef5c37c72a0fc34f2b6c48",
    (
        "keepalive.py",
        "_reset_poke_state_for_tests",
    ): "69394e0a0f48750dd5627f09b82d1b9c8b6b6cf06d725aead5f454552b1b1176",
    (
        "account_types.py",
        "Account",
    ): "b5432ef27f32b0a8806d58e7800a41d1de9075c663278cddc398ce1c83004add",
    (
        "account_types.py",
        "PlaywrightAccountRepairResult",
    ): "da73c519639d3c875cc7fd15de80a00b04275e5f57a5292ae64b98c72ebda75e",
    (
        "account_repair.py",
        "AccountRepairService",
    ): "b156f016bfa90690241fd78a94874c950dcaa66dd38cdf2368d3493a78e337c1",
    (
        "account_repair.py",
        "_compose_account_repair_service",
    ): "5036e247df7fb497d6c7d415846e713512680f6256310eccc0b25c75701daef5",
    (
        "account.py",
        "_enumerate_accounts_for_repair",
    ): "71e8da56d0b219fbb4acf2fcfe49185b20c191be5179106e1f1febc595ee4ffa",
    (
        "account.py",
        "_select_account_for_repair",
    ): "fc75e8190a48cb33e24b26dacbf64be967817ec95ba8256153e50592ab25c20c",
    (
        "account.py",
        "_extract_active_email_for_repair",
    ): "943b88de148386886438adad727af84bfa64fcafbaff121d125f14838bcd9fca",
    (
        "account.py",
        "repair_account_metadata_from_playwright_storage",
    ): "f7d3f9a3877fb8fbb65ccf1b61ad47dfac6f0b1e6257196b0e2ae0e2b3c8e97e",
    (
        "cookie_types.py",
        "CookieJar",
    ): "af2ceb60c67033f58beae06dae09c61ccb681ba2d0eac9c4bf0d4d4d189adc79",
    (
        "cookies.py",
        "load_session_jar",
    ): "3cc9680e69e25e38b9b578ee423d67c855b4b23615064a51de1a5b4a93e46387",
    (
        "psidts_recovery.py",
        "_read_storage_for_recovery",
    ): "f1f8a85508adeaa484310a0703eff8f55a760289aa5c62dad68a48d0a0e6d98c",
    (
        "psidts_recovery.py",
        "_recovery_observation",
    ): "ccb52fd2a30d17aa6d1a5eaef3cc54ea3e208095cfd4cda2f0403616c2dd30fd",
    (
        "psidts_recovery.py",
        "_attempt_rotation",
    ): "3e82dbaeca4a91cf0929540123c082cf6c1a3e1f66c92aeef4f61fd66e3e2d33",
    (
        "psidts_recovery.py",
        "_psidts_is_live",
    ): "abf96e694de66eb6780c4bf3de91ca4138a44a272b1c2c416efb46a87be33b48",
    (
        "psidts_recovery.py",
        "_psidts_routes_to_rotate",
    ): "39b8f06e73ebfec4a5c56715bd2f3a2f98c0c13bc883e3b556a01bbc5ec24904",
    (
        "master_token_types.py",
        "MasterTokenError",
    ): "e509cda5bec8ebc4891c37c91a095e3edaed57699a76a8d2b666aa3c4aaf4b04",
    (
        "storage.py",
        "_cookie_jar_for_merge",
    ): "41ada49f3d99769430c7814a9077bf59791be7816fdc4051c6f2af6addfb0d9a",
    (
        "storage.py",
        "persist_minted_jar",
    ): "421b5517bc5fe0f1d1dfcb3333c2671c9703eb801e640b154d53f126835f3323",
}

_FORBIDDEN_DYNAMIC = {
    "__dict__",
    "__import__",
    "eval",
    "exec",
    "getattr",
    "globals",
    "import_module",
    "locals",
    "setattr",
    "vars",
}


def _tree(name: str) -> ast.Module:
    return ast.parse((AUTH_ROOT / name).read_text(encoding="utf-8"), filename=name)


def _hash(node: ast.AST) -> str:
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()


def _top_node(tree: ast.Module, name: str) -> ast.stmt:
    matches = [node for node in tree.body if getattr(node, "name", None) == name]
    assert len(matches) == 1
    return matches[0]


def _static_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left)
        right = _static_string(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        pieces = [_static_string(value) for value in node.values]
        return (
            "".join(piece for piece in pieces if piece is not None)
            if all(piece is not None for piece in pieces)
            else None
        )
    return None


def _qualified(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _qualified(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return ""


def _capability_violations(tree: ast.AST, forbidden_imports: set[str]) -> set[str]:
    violations: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name in forbidden_imports:
                    violations.add(f"import:{item.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in forbidden_imports or any(
                item.name in forbidden_imports for item in node.names
            ):
                violations.add(f"import:{module}")
        elif isinstance(node, ast.Call):
            name = _qualified(node.func).rsplit(".", 1)[-1]
            if name in _FORBIDDEN_DYNAMIC:
                violations.add(f"call:{name}")
            if name in {"__import__", "import_module"} and node.args:
                target = _static_string(node.args[0])
                if target:
                    violations.add(f"dynamic-import:{target}")
        elif isinstance(node, ast.Attribute) and node.attr == "__dict__":
            violations.add("attribute:__dict__")
        elif isinstance(node, ast.Name) and node.id in {"builtins", "sys", "inspect"}:
            violations.add(f"name:{node.id}")
    return violations


def _self_fields(owner: ast.ClassDef) -> set[str]:
    constructor = next(
        node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    fields: set[str] = set()
    for node in ast.walk(constructor):
        target = (
            node.targets[0]
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            else node.target
            if isinstance(node, ast.AnnAssign)
            else None
        )
        if (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            fields.add(target.attr)
    return fields


def test_all_touched_executable_modules_and_owner_nodes_are_exact() -> None:
    for name, expected in _MODULE_HASHES.items():
        assert _hash(_tree(name)) == expected, name
    for (name, node_name), expected in _NODE_HASHES.items():
        assert _hash(_top_node(_tree(name), node_name)) == expected, (name, node_name)


def test_owner_fields_process_adapters_and_compatibility_views_are_exact() -> None:
    assert _self_fields(_top_node(_tree("single_flight.py"), "SingleFlight")) == {
        "_lock",
        "_flights",
        "_leader_tasks",
        "_success_epochs",
    }
    assert _self_fields(_top_node(_tree("recovery.py"), "ColdRecoveryState")) == {
        "_lock",
        "_locks_by_loop",
        "_success_generations",
    }
    assert _self_fields(_top_node(_tree("keepalive.py"), "RotationState")) == {
        "_lock",
        "_locks_by_loop",
        "_last_attempt_monotonic",
    }
    assert (
        single_flight.SingleFlight.process_default() is single_flight.SingleFlight.process_default()
    )
    assert (
        recovery.ColdRecoveryState.process_default() is recovery.ColdRecoveryState.process_default()
    )
    rotation = keepalive.RotationState.process_default()
    assert rotation is keepalive.RotationState.process_default()
    assert keepalive._POKE_STATE_LOCK is rotation._lock
    assert keepalive._POKE_LOCKS_BY_LOOP is rotation._locks_by_loop
    assert keepalive._LAST_POKE_ATTEMPT_MONOTONIC is rotation._last_attempt_monotonic
    assert auth_facade._POKE_STATE_LOCK is rotation._lock
    assert auth_facade._POKE_LOCKS_BY_LOOP is rotation._locks_by_loop
    assert auth_facade._LAST_POKE_ATTEMPT_MONOTONIC is rotation._last_attempt_monotonic


@pytest.mark.parametrize(
    ("module_name", "owner_name"),
    [("recovery.py", "ColdRecoveryState"), ("keepalive.py", "RotationState")],
)
def test_loop_lock_owners_use_weak_inner_values(module_name: str, owner_name: str) -> None:
    owner = _top_node(_tree(module_name), owner_name)
    called_attributes = {
        node.func.attr
        for node in ast.walk(owner)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "WeakKeyDictionary" in called_attributes
    assert "WeakValueDictionary" in called_attributes


def test_relocated_values_errors_helpers_and_retired_upward_aliases_are_exact() -> None:
    assert account.Account is account_types.Account
    assert account.PlaywrightAccountRepairResult is account_types.PlaywrightAccountRepairResult
    assert account.Account.__module__ == "notebooklm._auth.account"
    assert account.PlaywrightAccountRepairResult.__module__ == "notebooklm._auth.account"
    assert master_token.MasterTokenError is master_token_types.MasterTokenError
    assert storage.MasterTokenError is master_token_types.MasterTokenError
    assert auth_facade.MasterTokenError is master_token_types.MasterTokenError
    assert master_token_types.MasterTokenError.__module__ == "notebooklm._auth.master_token"
    assert master_token_types.MasterTokenError.__qualname__ == "MasterTokenError"
    assert str(inspect.signature(cookie_types.CookieJar.from_live_httpx_for_merge)) == (
        "(jar: 'httpx.Cookies', *, include_none: 'bool') -> 'CookieJar'"
    )
    assert storage._cookie_jar_for_merge.__module__ == "notebooklm._auth.storage"
    assert cookies.load_session_jar.__module__ == "notebooklm._auth.cookies"
    for name in {
        "load_session_jar",
        "_load_storage_state",
        "_storage_entry_to_cookie",
        "_safe_to_cookie",
        "_validate_cookie_shape",
    }:
        assert not hasattr(psidts_recovery, name)


@pytest.mark.parametrize(
    ("module_name", "forbidden"),
    [
        ("single_flight.py", {"account", "cookies", "refresh", "recovery", "storage"}),
        ("account_types.py", {"account", "account_repair", "profile_store", "storage"}),
        ("account_repair.py", {"account", "storage"}),
        ("cookie_types.py", {"cookies", "profile_store", "storage"}),
        ("master_token_types.py", {"master_token", "profile_store", "storage"}),
        ("psidts_recovery.py", {"cookies", "storage"}),
    ],
)
def test_dependency_bottom_modules_have_no_upward_or_dynamic_capability(
    module_name: str, forbidden: set[str]
) -> None:
    assert _capability_violations(_tree(module_name), forbidden) == set()


@pytest.mark.parametrize(
    "source",
    [
        "import storage\n",
        "from . import storage\n",
        "from .storage import ProfileStore\n",
        "import importlib\nimportlib.import_module('notebooklm._auth.' + 'storage')\n",
        "__import__(f\"notebooklm._auth.{ 'storage' }\")\n",
        "getattr(owner, 'state')\n",
        "vars(owner)\n",
        "globals()['state']\n",
        "owner.__dict__['state']\n",
    ],
)
def test_import_reflection_alias_and_constant_assembly_bites(source: str) -> None:
    assert _capability_violations(ast.parse(source), {"storage"})


def test_alias_rebinding_and_hidden_peer_registry_bites() -> None:
    rebinding = ast.parse("_POKE_LOCKS_BY_LOOP = {}\n")
    assignments = {
        target.id
        for node in ast.walk(rebinding)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert assignments & {"_POKE_STATE_LOCK", "_POKE_LOCKS_BY_LOOP", "_LAST_POKE_ATTEMPT_MONOTONIC"}
    peer = ast.parse(
        "class Owner:\n"
        " def __init__(self):\n"
        "  self._lock = lock()\n"
        "  self._locks_by_loop = {}\n"
        "  self._second_registry = {}\n"
    )
    assert _self_fields(peer.body[0]) - {"_lock", "_locks_by_loop"} == {"_second_registry"}


def test_one_shot_services_have_no_callback_or_secret_fields_after_settlement_shape() -> None:
    coordinator_fields = _self_fields(_top_node(_tree("recovery.py"), "ColdRecoveryCoordinator"))
    assert coordinator_fields == {
        "_claim_lock",
        "_state",
        "_single_flight",
        "_should_try_refresh",
        "_resolve_refresh_path",
        "_run_refresh_attempt",
        "_load_cookie_pair",
        "_run_headless_attempt",
        "_run_master_token_attempt",
        "_validate_recovered",
        "_fetch_recovered",
        "_replace_cookie_jar",
        "_snapshot_cookie_jar",
        "_clone_cookie_jar",
        "_used",
    }
    repair_fields = _self_fields(_top_node(_tree("account_repair.py"), "AccountRepairService"))
    assert repair_fields == {
        "_claim_lock",
        "_claimed",
        "_writer_factory",
        "_load_cookie_jar",
        "_enumerate_accounts",
        "_select_account",
        "_extract_active_email",
        "_poke_session",
    }
    for file_name, class_name, method_name, deleted in [
        (
            "recovery.py",
            "ColdRecoveryCoordinator",
            "recover",
            coordinator_fields - {"_claim_lock", "_state", "_single_flight", "_used"},
        ),
        (
            "account_repair.py",
            "AccountRepairService",
            "repair",
            repair_fields - {"_claim_lock", "_claimed"},
        ),
    ]:
        owner = _top_node(_tree(file_name), class_name)
        method = next(node for node in owner.body if getattr(node, "name", None) == method_name)
        deleted_fields = {
            node.targets[0].attr
            for node in ast.walk(method)
            if isinstance(node, ast.Delete)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Attribute)
        }
        assert deleted_fields == deleted
