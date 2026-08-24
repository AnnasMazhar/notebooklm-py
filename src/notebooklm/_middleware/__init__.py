"""Private HTTP-shaped middleware implementation package.

Cohesive cluster promoted from the former flat ``_middleware*.py`` modules (issue #1328).
The former ``_middleware.py`` envelope/chain primitive now lives in :mod:`._middleware.core`.
Re-exports the cluster's public names; importers may also reach submodules directly.
"""

from . import (
    auth_refresh,
    chain,
    chain_host,
    context,
    core,
    drain,
    metrics,
    retry,
    semaphore,
    tracing,
)
from .auth_refresh import AuthRefreshMiddleware
from .chain import MiddlewareChainBuilder
from .chain_host import MiddlewareChainHost
from .context import RpcCallState
from .core import (
    Middleware,
    NextCall,
    RpcRequest,
    RpcResponse,
    build_chain,
    materialize_rpc_request,
)
from .drain import DrainMiddleware
from .metrics import MetricsMiddleware
from .retry import RetryMiddleware
from .semaphore import SemaphoreMiddleware
from .tracing import TracingMiddleware

__all__ = [
    "auth_refresh",
    "chain",
    "chain_host",
    "context",
    "core",
    "drain",
    "metrics",
    "retry",
    "semaphore",
    "tracing",
    "AuthRefreshMiddleware",
    "MiddlewareChainBuilder",
    "MiddlewareChainHost",
    "RpcCallState",
    "Middleware",
    "NextCall",
    "RpcRequest",
    "RpcResponse",
    "build_chain",
    "materialize_rpc_request",
    "DrainMiddleware",
    "MetricsMiddleware",
    "RetryMiddleware",
    "SemaphoreMiddleware",
    "TracingMiddleware",
]
