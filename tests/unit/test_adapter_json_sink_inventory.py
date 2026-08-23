"""Tests for the fail-closed adapter JSON sink inventory."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.audit_adapter_json_sinks import (
    assert_exact_sink_dispositions,
    discover_adapter_json_sinks,
    fingerprint_adapter_helpers,
)
from scripts.audit_adapter_projection_paths import (
    discover_private_dataclass_projection_paths,
)

import notebooklm


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
        for channel in ("cli --json", "mcp tool result", "rest response")
    }
    assert {channel: len(rows) for channel, rows in by_channel.items()} == {
        "cli --json": 157,
        "mcp tool result": 61,
        "rest response": 47,
    }
    assert {
        role: sum(sink.site_role == role for sink in by_channel["cli --json"])
        for role in ("projection", "error-projection", "forwarding-infrastructure")
    } == {
        "projection": 105,
        "error-projection": 49,
        "forwarding-infrastructure": 3,
    }
    assert len({sink.id for sink in sinks}) == len(sinks)
    assert all(sink.expression_fingerprint.startswith("sha256:") for sink in sinks)
    assert all(sink.owner_fingerprint.startswith("sha256:") for sink in sinks)


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
