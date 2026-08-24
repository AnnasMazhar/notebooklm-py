"""Compatibility import for the web-owned streamed-chat transport adapter."""

from .._web.chat_transport import chat_aware_authed_post

__all__ = ["chat_aware_authed_post"]
