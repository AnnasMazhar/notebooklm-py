"""Materialize web RPC envelopes from an acquired cookie generation.

This is the narrow credential-to-wire adapter outside ``notebooklm._web``.
The semantic backend passes an opaque immutable generation here and therefore
does not import account routing or name credential fields itself.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from ._auth.account import format_authuser_value
from ._env import get_default_language
from ._web_cookie_provider import WebCookieGeneration
from .rpc import RPCMethod, build_request_body, get_batchexecute_url


def build_web_rpc_url(
    rpc_method: RPCMethod,
    generation: WebCookieGeneration,
    source_path: str = "/",
    rpc_id_override: str | None = None,
) -> str:
    """Build one override-aware batchexecute URL from a frozen generation."""
    rpc_id = rpc_id_override if rpc_id_override is not None else rpc_method.value
    params: dict[str, str] = {
        "rpcids": rpc_id,
        "source-path": source_path,
        "f.sid": generation.session_id,
        "hl": get_default_language(),
        "rt": "c",
    }
    if generation.account_email or generation.authuser:
        params["authuser"] = format_authuser_value(
            generation.authuser,
            generation.account_email,
        )
    return f"{get_batchexecute_url()}?{urlencode(params)}"


def build_web_rpc_request(
    *,
    rpc_method: RPCMethod,
    generation: WebCookieGeneration,
    source_path: str,
    rpc_id_override: str,
    encoded_request: Any,
) -> tuple[str, str, dict[str, str]]:
    """Materialize URL/body together from the same immutable generation."""
    return (
        build_web_rpc_url(
            rpc_method,
            generation,
            source_path,
            rpc_id_override=rpc_id_override,
        ),
        build_request_body(encoded_request, generation.csrf_token),
        {},
    )


__all__ = ["build_web_rpc_request", "build_web_rpc_url"]
