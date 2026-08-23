"""Tests for the fail-closed adapter JSON sink inventory."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
from scripts.audit_adapter_json_sinks import (
    assert_exact_sink_dispositions,
    assert_no_unreviewed_direct_json_emissions,
    assert_supported_adapter_registrations,
    discover_adapter_json_sinks,
    fingerprint_adapter_helpers,
)
from scripts.audit_adapter_projection_paths import (
    PrivateDataclassProjectionPath,
    discover_private_dataclass_projection_paths,
)

import notebooklm
from tests._baselines import adapter_sink_reachability as reachability


def _source_root() -> Path:
    return Path(notebooklm.__file__).resolve().parents[1]


def _write_adapter_source(root: Path, relative_path: str, source: str) -> None:
    path = root / "notebooklm" / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_inventory_covers_every_current_terminal_adapter_site() -> None:
    sinks = discover_adapter_json_sinks(_source_root())

    by_channel = {
        channel: [sink for sink in sinks if sink.channel == channel]
        for channel in (
            "cli --json",
            "mcp tool result",
            "mcp auxiliary response",
            "rest response",
        )
    }
    assert {channel: len(rows) for channel, rows in by_channel.items()} == {
        "cli --json": 160,
        "mcp tool result": 95,
        "mcp auxiliary response": 33,
        "rest response": 61,
    }
    assert {
        role: sum(sink.site_role == role for sink in by_channel["cli --json"])
        for role in ("projection", "error-projection", "forwarding-infrastructure")
    } == {
        "projection": 105,
        "error-projection": 49,
        "forwarding-infrastructure": 6,
    }
    assert len({sink.id for sink in sinks}) == len(sinks)
    assert all(sink.expression_fingerprint.startswith("sha256:") for sink in sinks)
    assert all(sink.owner_fingerprint.startswith("sha256:") for sink in sinks)
    assert_no_unreviewed_direct_json_emissions(sinks)
    assert_supported_adapter_registrations(_source_root())


def test_private_dto_catalog_covers_every_annotation_proven_public_model_path() -> None:
    rows = discover_private_dataclass_projection_paths()

    assert len(rows) == 19
    triples = {(row.private_model, row.field_path, row.public_model) for row in rows}
    assert (
        "notebooklm._app.source_mutations.SourceRenameResult",
        "source",
        "notebooklm.types.Source",
    ) in triples
    assert (
        "notebooklm._app.source_mutations.SourceRefreshResult",
        "result",
        "notebooklm.types.Source",
    ) in triples
    assert (
        "notebooklm._app.source_add.SourceAddResult",
        "source",
        "notebooklm.types.Source",
    ) in triples
    assert (
        "notebooklm._app.labels.LabelGenerateResult",
        "labels[]",
        "notebooklm.types.Label",
    ) in triples


def test_private_dto_catalog_follows_nested_container_paths_and_mutations(
    tmp_path: Path,
) -> None:
    aliases = {"notebooklm.types.Public": "notebooklm.types.Public"}
    _write_adapter_source(
        tmp_path,
        "_app/results.py",
        """
from dataclasses import InitVar, dataclass
from typing import ClassVar
from notebooklm.types import Public

@dataclass
class Inner:
    public: list[Public]
    class_only: ClassVar[Public]
    init_only: InitVar[Public]

@dataclass
class Outer:
    inner: Inner | None
""",
    )
    before = discover_private_dataclass_projection_paths(
        tmp_path,
        relative_roots=("notebooklm/_app",),
        public_model_aliases=aliases,
    )

    assert {(row.private_model, row.field_path, row.public_model) for row in before} == {
        ("notebooklm._app.results.Inner", "public[]", "notebooklm.types.Public"),
        (
            "notebooklm._app.results.Outer",
            "inner.public[]",
            "notebooklm.types.Public",
        ),
    }

    _write_adapter_source(
        tmp_path,
        "_app/results.py",
        """
from dataclasses import dataclass
from notebooklm.types import Public

@dataclass
class Inner:
    public: list[Public]
    secondary: Public | None

@dataclass
class Outer:
    inner: Inner | None
""",
    )
    after = discover_private_dataclass_projection_paths(
        tmp_path,
        relative_roots=("notebooklm/_app",),
        public_model_aliases=aliases,
    )

    assert len(after) == len(before) + 2
    assert {row.field_path for row in after} - {row.field_path for row in before} == {
        "secondary",
        "inner.secondary",
    }


def test_private_dto_catalog_rejects_unresolved_annotations(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "_app/results.py",
        """
from dataclasses import dataclass

@dataclass
class Result:
    missing: MissingType
""",
    )

    with pytest.raises(ValueError, match="unresolved annotation name 'MissingType'"):
        discover_private_dataclass_projection_paths(
            tmp_path,
            relative_roots=("notebooklm/_app",),
            public_model_aliases={"notebooklm.types.Public": "notebooklm.types.Public"},
        )


def test_private_dto_catalog_does_not_import_optional_adapter_modules(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "mcp/optional_results.py",
        """
import dependency_that_is_intentionally_not_installed
from dataclasses import dataclass
from notebooklm.types import Public

@dataclass
class Result:
    value: Public
""",
    )

    rows = discover_private_dataclass_projection_paths(
        tmp_path,
        relative_roots=("notebooklm/mcp",),
        public_model_aliases={"notebooklm.types.Public": "notebooklm.types.Public"},
    )

    assert [row.to_dict() for row in rows] == [
        {
            "private_model": "notebooklm.mcp.optional_results.Result",
            "field_path": "value",
            "public_model": "notebooklm.types.Public",
        }
    ]


def test_dead_private_path_evidence_rejects_new_source_valued_return(
    tmp_path: Path,
) -> None:
    copied_source_root = tmp_path / "src"
    shutil.copytree(_source_root(), copied_source_root)
    mutations_path = copied_source_root / "notebooklm" / "_app" / "source_mutations.py"
    source = mutations_path.read_text(encoding="utf-8")
    old = (
        "return SourceRefreshResult(source_id=resolved_id, notebook_id=plan.notebook_id, "
        "result=None)"
    )
    new = (
        "return SourceRefreshResult(source_id=resolved_id, notebook_id=plan.notebook_id, "
        "result=Source(id=resolved_id))"
    )
    assert old in source
    mutations_path.write_text(source.replace(old, new, 1), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="missing required adapter evidence AST fragments|source evidence changed",
    ):
        reachability.derive_adapter_sink_reachability_contract(
            copied_source_root,
            known_projection_ids=_allocation_projection_ids(),
        )


def test_sink_identity_changes_with_exact_wrapper_shape(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "cli/example.py",
        """
def command():
    json_output_response({"source": value})
""",
    )
    before = discover_adapter_json_sinks(tmp_path)

    _write_adapter_source(
        tmp_path,
        "cli/example.py",
        """
def command():
    json_output_response({"result": {"source": value}})
""",
    )
    after = discover_adapter_json_sinks(tmp_path)

    assert len(before) == len(after) == 1
    assert before[0].id != after[0].id
    assert before[0].expression_fingerprint != after[0].expression_fingerprint
    assert before[0].owner_fingerprint != after[0].owner_fingerprint


def test_delegated_helper_fingerprint_changes_with_projection_body(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "cli/project.py",
        """
def source_payload(source):
    return {"id": source.id}
""",
    )
    symbol = "notebooklm.cli.project.source_payload"
    before = fingerprint_adapter_helpers(tmp_path, [symbol])

    _write_adapter_source(
        tmp_path,
        "cli/project.py",
        """
def source_payload(source):
    return {"id": source.id, "title": source.title}
""",
    )
    after = fingerprint_adapter_helpers(tmp_path, [symbol])

    assert before[symbol] != after[symbol]
    with pytest.raises(ValueError, match="unresolved delegated adapter projection helpers"):
        fingerprint_adapter_helpers(tmp_path, ["notebooklm.cli.project.missing"])


def test_nonpublic_delegated_helper_mutation_changes_reachability_contract(
    tmp_path: Path,
) -> None:
    known_projection_ids = _allocation_projection_ids()
    before = reachability.derive_adapter_sink_reachability_contract(
        _source_root(), known_projection_ids=known_projection_ids
    )
    copied_source_root = tmp_path / "src"
    shutil.copytree(_source_root(), copied_source_root)
    serializer_path = copied_source_root / "notebooklm" / "_app" / "serialize.py"
    source = serializer_path.read_text(encoding="utf-8")
    old = "return to_jsonable(obj.value)"
    new = "return str(obj.value)"
    assert old in source
    serializer_path.write_text(source.replace(old, new, 1), encoding="utf-8")

    after = reachability.derive_adapter_sink_reachability_contract(
        copied_source_root, known_projection_ids=known_projection_ids
    )
    symbol = "notebooklm._app.serialize.to_jsonable"
    assert symbol in before["delegated_helper_fingerprints"]
    assert (
        before["delegated_helper_fingerprints"][symbol]
        != after["delegated_helper_fingerprints"][symbol]
    )


def test_duplicate_sink_expression_keeps_multiplicity(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "mcp/tools/example.py",
        """
def register(mcp):
    @mcp.tool
    def example(flag):
        if flag:
            return payload
        return payload
""",
    )

    sinks = discover_adapter_json_sinks(tmp_path)

    assert len(sinks) == 2
    assert sinks[0].expression_fingerprint == sinks[1].expression_fingerprint
    assert sinks[0].id.endswith(":1")
    assert sinks[1].id.endswith(":2")


def test_router_return_is_discovered_but_ordinary_helper_return_is_not(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "server/routes/example.py",
        """
def helper():
    return {"ignored": True}

@router.get("/example")
def example():
    return helper()
""",
    )

    sinks = discover_adapter_json_sinks(tmp_path)

    assert len(sinks) == 1
    assert sinks[0].owner == "example"


def test_only_mcp_and_router_decorator_owners_define_result_sinks(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "mcp/tools/example.py",
        """
@registry.tool
def false_tool():
    return {"ignored": True}

@mcp.tool
def real_tool():
    return {"included": True}
""",
    )
    _write_adapter_source(
        tmp_path,
        "server/routes/example.py",
        """
@cache.get("key")
def false_route():
    return {"ignored": True}

@router.get("/example")
def real_route():
    return {"included": True}
""",
    )

    sinks = discover_adapter_json_sinks(tmp_path)

    assert [(sink.channel, sink.owner) for sink in sinks] == [
        ("mcp tool result", "real_tool"),
        ("rest response", "real_route"),
    ]


def test_direct_cli_json_bypass_is_discovered_and_rejected(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "cli/example.py",
        """
import json
import click

def command(payload):
    click.echo(json.dumps(payload))
""",
    )

    sinks = discover_adapter_json_sinks(tmp_path)

    assert len(sinks) == 1
    assert sinks[0].kind == "direct-json-emission"
    with pytest.raises(ValueError, match="unreviewed direct CLI JSON emissions"):
        assert_no_unreviewed_direct_json_emissions(sinks)


@pytest.mark.parametrize(
    ("relative_path", "source"),
    [
        (
            "mcp/dynamic.py",
            """
def register(mcp):
    def hidden_tool():
        return {"hidden": True}
    mcp.add_tool(hidden_tool)
""",
        ),
        (
            "server/routes/dynamic.py",
            """
def hidden_route():
    return {"hidden": True}
router.add_api_route("/hidden", hidden_route)
""",
        ),
    ],
)
def test_dynamic_adapter_registration_is_rejected(
    tmp_path: Path, relative_path: str, source: str
) -> None:
    _write_adapter_source(tmp_path, relative_path, source)

    with pytest.raises(ValueError, match="unsupported dynamic adapter registrations"):
        assert_supported_adapter_registrations(tmp_path)


def test_decorated_mcp_tool_registration_is_supported(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "mcp/tools/example.py",
        """
def register(mcp):
    @mcp.tool(annotations={"readOnlyHint": True})
    def visible_tool():
        return {"visible": True}
""",
    )

    assert_supported_adapter_registrations(tmp_path)
    assert len(discover_adapter_json_sinks(tmp_path)) == 1


def test_mcp_tool_error_boundary_is_a_separate_terminal(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "mcp/tools/example.py",
        """
def register(mcp):
    @mcp.tool
    def visible_tool():
        with mcp_errors():
            return {"visible": True}
""",
    )

    sinks = discover_adapter_json_sinks(tmp_path)
    assert [(sink.kind, sink.site_role) for sink in sinks] == [
        ("return", "projection"),
        ("tool-error-funnel", "error-projection"),
    ]


def test_qualified_owner_includes_class_scope(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "cli/example.py",
        """
class First:
    def render(self):
        json_output_response({"first": True})

class Second:
    def render(self):
        json_output_response({"second": True})
""",
    )

    sinks = discover_adapter_json_sinks(tmp_path)

    assert [sink.owner for sink in sinks] == ["First.render", "Second.render"]


def test_error_projection_tracks_extra_shape_and_marks_facade_forwarding(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "cli/example.py",
        """
def command(result):
    output_error("failed", "FAILED", True, 1, extra={"status": result})
""",
    )
    before = discover_adapter_json_sinks(tmp_path)
    assert before[0].site_role == "error-projection"

    _write_adapter_source(
        tmp_path,
        "cli/example.py",
        """
def command(result):
    output_error("failed", "FAILED", True, 1, extra={"transition": result})
""",
    )
    after = discover_adapter_json_sinks(tmp_path)
    assert before[0].id != after[0].id


def test_dispositions_fail_closed_for_missing_and_stale_sites(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "cli/example.py",
        """
def command():
    json_output_response(payload)
""",
    )
    sinks = discover_adapter_json_sinks(tmp_path)

    with pytest.raises(ValueError, match="missing="):
        assert_exact_sink_dispositions(sinks, {})
    with pytest.raises(ValueError, match="stale="):
        assert_exact_sink_dispositions(sinks, {sinks[0].id: {}, "removed": {}})

    assert_exact_sink_dispositions(
        sinks,
        {sinks[0].id: {"projection_ids": ["cli-source-row"]}},
        known_projection_ids={"cli-source-row"},
    )


def test_dispositions_require_one_reviewed_reachability_case(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "cli/example.py",
        """
def command():
    json_output_response(payload)
""",
    )
    sink = discover_adapter_json_sinks(tmp_path)[0]

    with pytest.raises(ValueError, match="expected one discriminator"):
        assert_exact_sink_dispositions(
            [sink],
            {
                sink.id: {
                    "projection_ids": ["row"],
                    "non_public_model_reason": "plain scalars",
                }
            },
        )
    with pytest.raises(ValueError, match="unknown compatibility projection ids"):
        assert_exact_sink_dispositions(
            [sink],
            {sink.id: {"projection_ids": ["missing"]}},
            known_projection_ids={"row"},
        )


@pytest.mark.parametrize(
    "import_line,dump_call",
    [
        ("import json as j", "j.dumps(payload)"),
        ("from json import dumps as encode_json", "encode_json(payload)"),
    ],
)
def test_direct_cli_json_bypass_resolves_stdlib_import_aliases(
    tmp_path: Path, import_line: str, dump_call: str
) -> None:
    _write_adapter_source(
        tmp_path,
        "cli/example.py",
        f"""
{import_line}
import click

def command(payload):
    click.echo({dump_call})
""",
    )

    sinks = discover_adapter_json_sinks(tmp_path)
    assert [sink.kind for sink in sinks] == ["direct-json-emission"]
    with pytest.raises(ValueError, match="unreviewed direct CLI JSON emissions"):
        assert_no_unreviewed_direct_json_emissions(sinks)


def test_mcp_custom_route_inventories_json_and_non_json_returns(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "mcp/routes.py",
        """
@mcp.custom_route("/example", methods=["GET"])
def example(flag):
    if flag:
        return JSONResponse(status_code=200, content={"source_id": source.id})
    return PlainTextResponse("not json")
""",
    )

    sinks = discover_adapter_json_sinks(tmp_path)
    assert len(sinks) == 2
    assert {sink.channel for sink in sinks} == {"mcp auxiliary response"}
    assert {sink.kind for sink in sinks} == {"return"}
    assert_supported_adapter_registrations(tmp_path)


def test_rest_app_routes_and_exception_handlers_are_discovered(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "server/app.py",
        """
@app.get("/healthz")
def health():
    return {"ok": True}

@app.exception_handler(Exception)
def errors(request, exc):
    return error_response(exc)
""",
    )

    sinks = discover_adapter_json_sinks(tmp_path)
    assert [(sink.channel, sink.owner) for sink in sinks] == [
        ("rest response", "errors"),
        ("rest response", "health"),
    ]


def test_rest_central_json_response_extracts_keyword_content(tmp_path: Path) -> None:
    _write_adapter_source(
        tmp_path,
        "server/_errors.py",
        """
def error_response(code):
    return JSONResponse(status_code=400, content={"error": {"code": code}})
""",
    )
    before = discover_adapter_json_sinks(tmp_path)

    _write_adapter_source(
        tmp_path,
        "server/_errors.py",
        """
def error_response(code):
    return JSONResponse(status_code=400, content={"problem": {"code": code}})
""",
    )
    after = discover_adapter_json_sinks(tmp_path)

    assert len(before) == len(after) == 1
    assert before[0].kind == "json-response-return"
    assert before[0].site_role == "forwarding-infrastructure"
    assert before[0].id != after[0].id


@pytest.mark.parametrize(
    ("relative_path", "source"),
    [
        (
            "mcp/dynamic_custom.py",
            """
def handler():
    return {"hidden": True}
mcp.custom_route("/hidden", methods=["GET"])(handler)
""",
        ),
        (
            "server/routes/dynamic_verb.py",
            """
def handler():
    return {"hidden": True}
router.get("/hidden")(handler)
""",
        ),
        (
            "server/dynamic_exception.py",
            """
def handler(request, exc):
    return {"hidden": True}
app.add_exception_handler(Exception, handler)
""",
        ),
    ],
)
def test_non_decorator_registration_forms_are_rejected(
    tmp_path: Path, relative_path: str, source: str
) -> None:
    _write_adapter_source(tmp_path, relative_path, source)
    with pytest.raises(ValueError, match="unsupported dynamic adapter registrations"):
        assert_supported_adapter_registrations(tmp_path)


def test_reviewed_oauth_route_exception_rejects_same_path_different_handler(
    tmp_path: Path,
) -> None:
    _write_adapter_source(
        tmp_path,
        "mcp/_oauth.py",
        """
def routes(self):
    return [Route("/login", self._json_login, methods=["GET", "POST"])]
""",
    )
    with pytest.raises(ValueError, match="unsupported dynamic adapter registrations"):
        assert_supported_adapter_registrations(tmp_path)


def _allocation_projection_ids() -> set[str]:
    rows = reachability._load_reviewed_allocations()
    return {
        projection_id
        for allocation in rows.values()
        for projection_id in allocation.get("projection_ids", [])
    }


def test_checked_in_reachability_allocations_are_exact() -> None:
    contract = reachability.derive_adapter_sink_reachability_contract(
        _source_root(), known_projection_ids=_allocation_projection_ids()
    )
    assert contract["site_count"] == 349
    assert len(contract["private_dataclass_projection_paths"]) == 19


def test_mcp_mind_map_union_projections_are_on_value_carrying_branches() -> None:
    allocations = reachability._load_reviewed_allocations()
    rename_id = "mcp.MindMap.transitive-resolver-rename-final-wrapper"
    delete_id = "mcp.MindMap.transitive-resolver-delete-final-wrapper"

    def projection_ids(owner: str, ordinal: int) -> set[str]:
        locator = (
            f"mcp tool result|notebooklm/mcp/tools/studio.py|register.{owner}|return|{ordinal}"
        )
        return set(allocations[locator].get("projection_ids", []))

    assert rename_id in projection_ids("studio_rename", 1)
    assert rename_id in projection_ids("studio_rename", 3)
    assert delete_id in projection_ids("studio_delete", 2)
    assert delete_id not in projection_ids("studio_delete", 3)
    assert delete_id in projection_ids("studio_delete", 5)


def test_status_derived_contributions_are_on_exact_terminal_or_error_funnel() -> None:
    allocations = reachability._load_reviewed_allocations()
    expected = {
        (
            "rest response|notebooklm/server/routes/sources.py|get_source_content|return|2",
            "rest.Source.transitive-source-content-readiness-contribution",
        ),
        (
            "mcp tool result|notebooklm/mcp/tools/studio.py|"
            "register.studio_download|tool-error-funnel|1",
            "mcp.Artifact.transitive-download-incomplete-status-error-text-contribution",
        ),
        (
            "mcp tool result|notebooklm/mcp/tools/studio.py|"
            "register.studio_retry|tool-error-funnel|1",
            "mcp.Artifact.transitive-retry-wrong-state-status-error-text-contribution",
        ),
    }
    for locator, projection_id in expected:
        assert projection_id in allocations[locator]["projection_ids"]


def test_reachability_rejects_unallocated_known_projection_id() -> None:
    with pytest.raises(ValueError, match="no adapter terminal allocation"):
        reachability.derive_adapter_sink_reachability_contract(
            _source_root(),
            known_projection_ids={*_allocation_projection_ids(), "cli.Source.new-unallocated"},
        )


def test_private_path_dispositions_fail_when_new_path_is_unallocated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = discover_private_dataclass_projection_paths(_source_root())
    added = PrivateDataclassProjectionPath(
        private_model="notebooklm._app.future.Result",
        field_path="source",
        public_model="notebooklm.types.Source",
    )
    monkeypatch.setattr(
        reachability,
        "discover_private_dataclass_projection_paths",
        lambda _root: [*original, added],
    )
    with pytest.raises(ValueError, match="private-path allocations are not exact"):
        reachability.derive_adapter_sink_reachability_contract(
            _source_root(), known_projection_ids=_allocation_projection_ids()
        )


def _write_allocation_copy(tmp_path: Path, name: str, payload: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_sink_allocations_reject_duplicate_and_generic_review_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.loads(reachability._ALLOCATION_PATH.read_text(encoding="utf-8"))
    duplicated = copy.deepcopy(raw)
    duplicated["allocations"].append(copy.deepcopy(duplicated["allocations"][0]))
    monkeypatch.setattr(
        reachability,
        "_ALLOCATION_PATH",
        _write_allocation_copy(tmp_path, "duplicate.json", duplicated),
    )
    with pytest.raises(ValueError, match="duplicate reviewed adapter sink allocation"):
        reachability.derive_adapter_sink_reachability_contract(
            _source_root(), known_projection_ids=_allocation_projection_ids()
        )

    generic = copy.deepcopy(raw)
    reviewed = next(row for row in generic["allocations"] if "non_public_category" in row)
    reviewed["review_note"] = "no public dataclass"
    monkeypatch.setattr(
        reachability,
        "_ALLOCATION_PATH",
        _write_allocation_copy(tmp_path, "generic.json", generic),
    )
    with pytest.raises(ValueError, match="generic review_note"):
        reachability.derive_adapter_sink_reachability_contract(
            _source_root(), known_projection_ids=_allocation_projection_ids()
        )


def test_sink_allocations_reject_known_cross_channel_projection_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.loads(reachability._ALLOCATION_PATH.read_text(encoding="utf-8"))
    mutated = copy.deepcopy(raw)
    row = next(
        item
        for item in mutated["allocations"]
        if item["locator"].startswith("cli --json|") and "projection_ids" in item
    )
    row["projection_ids"].append("rest.Note.dataclass-full")
    monkeypatch.setattr(
        reachability,
        "_ALLOCATION_PATH",
        _write_allocation_copy(tmp_path, "cross-channel.json", mutated),
    )
    with pytest.raises(ValueError, match="cross-channel projection ids"):
        reachability.derive_adapter_sink_reachability_contract(
            _source_root(),
            known_projection_ids={*_allocation_projection_ids(), "rest.Note.dataclass-full"},
        )


def test_private_path_allocations_reject_wrong_model_and_unrelated_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.loads(reachability._PRIVATE_PATH_ALLOCATION_PATH.read_text(encoding="utf-8"))
    wrong_model = copy.deepcopy(raw)
    wrong_model["allocations"][0]["projection_ids"].append("cli.Label.manual-list-projection")
    monkeypatch.setattr(
        reachability,
        "_PRIVATE_PATH_ALLOCATION_PATH",
        _write_allocation_copy(tmp_path, "wrong-model.json", wrong_model),
    )
    with pytest.raises(ValueError, match="wrong-model private-path projection ids"):
        reachability.derive_adapter_sink_reachability_contract(
            _source_root(), known_projection_ids=_allocation_projection_ids()
        )

    unrelated = copy.deepcopy(raw)
    non_public_locator = next(
        locator
        for locator, allocation in reachability._load_reviewed_allocations().items()
        if "non_public_category" in allocation
    )
    unrelated["allocations"][0]["terminal_locators"].append(non_public_locator)
    monkeypatch.setattr(
        reachability,
        "_PRIVATE_PATH_ALLOCATION_PATH",
        _write_allocation_copy(tmp_path, "unrelated.json", unrelated),
    )
    with pytest.raises(ValueError, match="do not intersect projection ids"):
        reachability.derive_adapter_sink_reachability_contract(
            _source_root(), known_projection_ids=_allocation_projection_ids()
        )
