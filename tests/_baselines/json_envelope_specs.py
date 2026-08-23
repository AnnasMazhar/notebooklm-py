"""Reviewed exact JSON envelope projection declarations.

Channel declarations live in focused modules; this module composes them, adds
stable semantic IDs, and exposes the sink-allocation ID inventory. Runtime and
AST shape derivation lives in ``tests._baselines.json_envelope_contracts``.
"""

from __future__ import annotations

from collections.abc import Mapping

from tests._baselines.json_envelope_cli_specs import CLI_PROJECTION_SPECS
from tests._baselines.json_envelope_mcp_specs import MCP_PROJECTION_SPECS
from tests._baselines.json_envelope_rest_specs import REST_PROJECTION_SPECS

_CHANNEL_PROJECTION_SPECS: dict[str, tuple[dict[str, object], ...]] = {
    "cli --json": CLI_PROJECTION_SPECS,
    "mcp tool result": MCP_PROJECTION_SPECS,
    "rest response": REST_PROJECTION_SPECS,
}


# Every reviewed projection carries a source-backed drift signal. Rows with a deterministic
# helper or literal final dict use runtime/AST key derivation. The remaining key sets stay
# honestly labelled manual-reviewed and are coupled to the smallest semantic AST scope named
# by their evidence, whose fingerprint is serialized into the baseline.
def _projection_spec_id(channel: str, spec: Mapping[str, object]) -> str:
    """Return a stable, reviewable sink-allocation id for an explicit projection."""
    channel_id = {"cli --json": "cli", "mcp tool result": "mcp", "rest response": "rest"}[channel]
    model_id = str(spec["model"]).rsplit(".", 1)[-1]
    mode_id = "-".join(
        "".join(
            character.lower() if character.isalnum() else " " for character in str(spec["mode"])
        ).split()
    )
    return f"{channel_id}.{model_id}.{mode_id}"


_CHANNEL_PROJECTION_SPECS = {
    channel: tuple(
        {
            **spec,
            "id": spec.get("id", _projection_spec_id(channel, spec)),
            "derive": spec.get("derive", "manual-reviewed+fingerprint"),
        }
        for spec in specs
    )
    for channel, specs in _CHANNEL_PROJECTION_SPECS.items()
}


def projection_spec_ids() -> dict[str, tuple[str, ...]]:
    """Return the stable explicit IDs available to sink-allocation audits."""
    return {
        channel: tuple(sorted(str(spec["id"]) for spec in specs))
        for channel, specs in _CHANNEL_PROJECTION_SPECS.items()
    }


__all__ = ["projection_spec_ids"]
