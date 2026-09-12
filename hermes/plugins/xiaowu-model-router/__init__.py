"""Hermes plugin entry point."""
from __future__ import annotations

from pathlib import Path

try:  # Hermes loads this directory as a package.
    from .gateway_hook import GatewayRouter
    from .router import RouteRegistry
except ImportError:  # Standalone pytest/plugin-doctor collection.
    from gateway_hook import GatewayRouter
    from router import RouteRegistry


_REGISTRY = RouteRegistry.load(Path(__file__).with_name("routes.yaml"))
_ROUTER = GatewayRouter(_REGISTRY)


def pre_gateway_dispatch(**context):
    return _ROUTER.pre_gateway_dispatch(**context)


def pre_llm_call(**context):
    return _ROUTER.pre_llm_call(**context)


def pre_api_request(**context):
    return _ROUTER.pre_api_request(**context)


def llm_request(**context):
    return _ROUTER.llm_request(**context)


def post_llm_call(**context):
    return _ROUTER.post_llm_call(**context)


def pre_tool_call(**context):
    return _ROUTER.pre_tool_call(**context)


def post_tool_call(**context):
    return _ROUTER.post_tool_call(**context)


def transform_llm_output(**context):
    return _ROUTER.transform_llm_output(**context)


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("pre_api_request", pre_api_request)
    ctx.register_hook("post_llm_call", post_llm_call)
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_hook("post_tool_call", post_tool_call)
    ctx.register_hook("transform_llm_output", transform_llm_output)
    ctx.register_middleware("llm_request", llm_request)
