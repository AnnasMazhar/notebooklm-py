"""Artifact representation catalog shape handling after P5.8 retirement."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from notebooklm._backend import BackendError, BackendErrorReason
from notebooklm._records import ARTIFACT_DOWNLOAD_DEF, ArtifactDownloadInput
from tests._fixtures.web_backend import build_web_backend


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, [], False])
async def test_falsy_catalog_payload_is_empty(payload: object) -> None:
    core = MagicMock(rpc_call=AsyncMock(return_value=payload))

    result = await build_web_backend(core).invoke(
        ARTIFACT_DOWNLOAD_DEF,
        ArtifactDownloadInput("nb", "catalog"),
        deadline=None,
    )

    assert result.representations == ()


@pytest.mark.asyncio
async def test_truthy_non_list_catalog_payload_fails_loud() -> None:
    core = MagicMock(rpc_call=AsyncMock(return_value={"moved": True}))

    with pytest.raises(BackendError, match="Unrecognized LIST_ARTIFACTS") as caught:
        await build_web_backend(core).invoke(
            ARTIFACT_DOWNLOAD_DEF,
            ArtifactDownloadInput("nb", "catalog"),
            deadline=None,
        )

    assert caught.value.reason is BackendErrorReason.DECODING
