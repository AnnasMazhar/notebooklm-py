"""Parse the transport-neutral source-filter object used by MCP tools."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..._source.filter_labels import SourceStatusLabel, SourceTypeLabel
from ...exceptions import ValidationError
from ...types import SourceFilter


class SourceFilterInput(BaseModel):
    """Typed MCP input so tool schemas advertise the supported selectors."""

    model_config = ConfigDict(extra="forbid")

    statuses: list[SourceStatusLabel] = Field(default_factory=list)
    types: list[SourceTypeLabel] = Field(default_factory=list)

    def to_source_filter(self) -> SourceFilter:
        """Convert the schema-validated input into the public selector."""
        try:
            return SourceFilter.from_values(statuses=self.statuses, types=self.types)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc


def parse_source_filter(value: SourceFilterInput | None) -> SourceFilter | None:
    """Convert an optional schema-validated MCP input into ``SourceFilter``."""
    if value is None:
        return None
    return value.to_source_filter()


__all__ = ["SourceFilterInput", "parse_source_filter"]
