"""Pre-P7 constructor-option characterization for the keepalive floor."""

from __future__ import annotations

import pytest

from notebooklm import NotebookLMClient
from notebooklm.auth import AuthTokens


def _auth() -> AuthTokens:
    return AuthTokens(cookies={"SID": "test"}, csrf_token="csrf", session_id="session")


@pytest.mark.parametrize(
    ("keepalive", "minimum", "expected"),
    [
        pytest.param(1.0, 60.0, 60.0, id="below-floor-is-clamped"),
        pytest.param(60.0, 60.0, 60.0, id="at-floor-is-preserved"),
        pytest.param(120.0, 60.0, 120.0, id="above-floor-is-preserved"),
        pytest.param(None, 60.0, None, id="disabled-remains-disabled"),
    ],
)
def test_keepalive_min_interval_reaches_runtime_as_effective_interval(
    keepalive: float | None,
    minimum: float,
    expected: float | None,
) -> None:
    client = NotebookLMClient(
        _auth(),
        keepalive=keepalive,
        keepalive_min_interval=minimum,
    )

    assert client._provider._lifecycle._keepalive_interval == expected
