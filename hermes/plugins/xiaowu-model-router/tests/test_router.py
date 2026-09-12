from __future__ import annotations

import importlib
from pathlib import Path

import pytest


router = importlib.import_module("yinyue_router_testpkg.router")
state_mod = importlib.import_module("yinyue_router_testpkg.state")


def test_registry_contains_only_phase_one_route():
    registry = router.RouteRegistry.load(Path(__file__).resolve().parents[1] / "routes.yaml")
    assert list(registry.as_dict()) == ["xiaowu-avatar"]
    route = registry.require("xiaowu-avatar")
    assert route.model == "yinyue2"
    assert route.runtime_provider == "custom:yinyue2-local"
    assert route.reasoning_effort == "none"


@pytest.mark.parametrize("verb", ["启动", "结束", "状态", "重置"])
def test_exact_control_commands(verb):
    assert router.parse_control_command(f"/xiaowu-avatar {verb}") == verb
    assert router.parse_control_command(f"/yinyue_avatar@my_bot {verb}") == verb


@pytest.mark.parametrize(
    "text",
    [
        "/xiaowu-avatar",
        "/xiaowu-avatar 启动 now",
        "请启动 xiaowu-avatar",
        "帮我结束",
        "/other 启动",
    ],
)
def test_natural_language_never_controls_router(text):
    assert router.parse_control_command(text) is None


def test_session_state_round_trip_and_route_validation():
    registry = router.RouteRegistry.load(Path(__file__).resolve().parents[1] / "routes.yaml")
    route = registry.require("xiaowu-avatar")
    state = state_mod.RouterState.activate("telegram:a", route, route.runtime_provider)
    restored = state_mod.RouterState.from_metadata(state.to_metadata())
    assert restored == state
    restored.validate_route(route)
    assert state_mod.override_matches(
        {
            "model": route.model,
            "provider": route.runtime_provider,
            "base_url": route.base_url,
        },
        state,
    )


def test_corrupt_state_fails_closed():
    with pytest.raises(state_mod.RouterStateError):
        state_mod.RouterState.from_metadata({"mode": "skill-worker"})


def test_state_is_session_scoped():
    registry = router.RouteRegistry.load(Path(__file__).resolve().parents[1] / "routes.yaml")
    route = registry.require("xiaowu-avatar")
    states = {
        "telegram:A": state_mod.RouterState.activate("session-A", route, route.runtime_provider),
    }
    assert states.get("telegram:A").mode == "xiaowu-worker"
    assert states.get("telegram:B") is None


def test_legacy_skill_worker_metadata_migrates_without_visual_lock():
    registry = router.RouteRegistry.load(Path(__file__).resolve().parents[1] / "routes.yaml")
    route = registry.require("xiaowu-avatar")
    raw = state_mod.RouterState.activate(
        "session-A", route, route.runtime_provider
    ).to_metadata()
    raw["mode"] = "skill-worker"
    for key in (
        "persona_enabled", "visual_skill_sticky", "worker_turns",
        "qwen_api_calls", "luna_api_calls", "routing_violations",
        "last_actual_provider", "last_actual_model", "last_actual_endpoint",
        "last_actual_api_mode", "last_api_request_id", "last_request_at",
    ):
        raw.pop(key)
    state = state_mod.RouterState.from_metadata(raw)
    assert state.mode == "xiaowu-worker"
    assert state.persona_enabled is True
    assert state.visual_skill_sticky is False
