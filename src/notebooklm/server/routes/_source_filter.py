"""REST request model for status/type source selection."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..._source.filter_labels import SourceStatusLabel, SourceTypeLabel
from ...exceptions import ValidationError
from ...types import SourceFilter


class SourceFilterBody(BaseModel):
    """JSON ``source_filter`` object shared by chat and generation routes."""

    model_config = ConfigDict(extra="forbid")

    statuses: list[SourceStatusLabel] = Field(default_factory=list)
    types: list[SourceTypeLabel] = Field(default_factory=list)

    def to_source_filter(self) -> SourceFilter:
        """Convert validated JSON lists into the public typed selector."""
        try:
            return SourceFilter.from_values(statuses=self.statuses, types=self.types)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc


__all__ = ["SourceFilterBody"]
