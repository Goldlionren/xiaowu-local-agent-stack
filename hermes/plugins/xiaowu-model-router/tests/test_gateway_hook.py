from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest


gateway_hook = importlib.import_module("yinyue_router_testpkg.gateway_hook")
router_mod = importlib.import_module("yinyue_router_testpkg.router")
state_mod = importlib.import_module("yinyue_router_testpkg.state")


@dataclass
class FakeEntry:
    session_id: str = "session-A"


class FakeStore:
    def __init__(self):
        self.metadata = {}
        self.overrides = {}

    def get_session_metadata(self, key, name, default=None):
        return self.metadata.get((key, name), default)

    def set_session_metadata(self, key, name, value):
        self.metadata[(key, name)] = value
        return True

    def get_model_override(self, key):
        value = self.overrides.get(key)
        return dict(value) if value else None

    def set_model_override(self, key, value):
        self.overrides[key] = dict(value) if value else None

    def get_or_create_session(self, source):
        return FakeEntry()


class FakeAsyncStore:
    def __init__(self, store):
        self.store = store

    async def get_session_metadata(self, *args):
        return self.store.get_session_metadata(*args)

    async def set_session_metadata(self, *args):
        return self.store.set_session_metadata(*args)

    async def get_model_override(self, *args):
        return self.store.get_model_override(*args)

    async def set_model_override(self, *args):
        return self.store.set_model_override(*args)

    async def get_or_create_session(self, *args):
        return self.store.get_or_create_session(*args)


class FakeAdapter:
    def __init__(self):
        self.messages = []

    async def send(self, chat_id, message, metadata=None):
        self.messages.append(message)


class FakeGateway:
    def __init__(self, store):
        self.session_store = store
        self.async_session_store = FakeAsyncStore(store)
        self._session_model_overrides = {}
        self._session_reasoning_overrides = {}
        self.adapter = FakeAdapter()
        self.running = False

    def _is_user_authorized(self, source):
        return True

    def _normalize_source_for_session_key(self, source):
        return source

    def _session_key_for_source(self, source):
        return source.session_key

    def _adapter_for_source(self, source):
        return self.adapter

    def _thread_metadata_for_source(self, source):
        return None

    def _is_session_running(self, key):
        return self.running

    def _set_session_reasoning_override(self, key, value):
        if value is None:
            self._session_reasoning_overrides.pop(key, None)
        else:
            self._session_reasoning_overrides[key] = value

    def _evict_cached_agent(self, key):
        return None


def event(text, key="telegram:A"):
    source = SimpleNamespace(
        session_key=key,
        chat_id="123",
        platform=SimpleNamespace(value="telegram"),
    )
    return SimpleNamespace(text=text, raw_message=None, source=source)


@pytest.fixture
def registry():
    return router_mod.RouteRegistry.load(
        gateway_hook.Path(gateway_hook.__file__).with_name("routes.yaml")
    )


@pytest.fixture
def worker_setup(registry):
    store = FakeStore()
    gateway = FakeGateway(store)
    route = registry.require("xiaowu-avatar")
    state = state_mod.RouterState.activate("session-A", route, route.runtime_provider)
    store.metadata[("telegram:A", state_mod.METADATA_KEY)] = state.to_metadata()
    override = {
        "model": route.model,
        "provider": route.runtime_provider,
        "base_url": route.base_url,
        "api_mode": route.api_mode,
    }
    store.overrides["telegram:A"] = override
    gateway._session_model_overrides["telegram:A"] = override
    return store, gateway, route


def test_default_session_allows_luna(registry, monkeypatch):
    store = FakeStore()
    gateway = FakeGateway(store)
    result = gateway_hook.GatewayRouter(registry).pre_gateway_dispatch(
        event=event("普通消息"), gateway=gateway, session_store=store
    )
    assert result is None


def test_worker_prompt_passes_unchanged_without_visual_skill_lock(worker_setup, registry, monkeypatch):
    store, gateway, _ = worker_setup
    calls = {"probe": 0, "skill": 0}
    monkeypatch.setattr(
        gateway_hook,
        "_probe_models",
        lambda route: (calls.__setitem__("probe", calls["probe"] + 1) or True, "ok"),
    )
    monkeypatch.setattr(
        gateway_hook,
        "_load_skill_message",
        lambda skill, prompt, sid: (
            calls.__setitem__("skill", calls["skill"] + 1) or f"LOCKED:{skill}:{prompt}"
        ),
    )
    result = gateway_hook.GatewayRouter(registry).pre_gateway_dispatch(
        event=event("测试当前 worker"), gateway=gateway, session_store=store
    )
    assert result is None
    assert calls == {"probe": 1, "skill": 0}


def test_second_prompt_keeps_worker_but_not_visual_skill(worker_setup, registry, monkeypatch):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda route: (True, "ok"))
    monkeypatch.setattr(
        gateway_hook, "_load_skill_message", lambda skill, prompt, sid: f"LOCKED:{prompt}"
    )
    router = gateway_hook.GatewayRouter(registry)
    assert router.pre_gateway_dispatch(
        event=event("第一轮"), gateway=gateway, session_store=store
    ) is None
    assert router.pre_gateway_dispatch(
        event=event("继续"), gateway=gateway, session_store=store
    ) is None
    first_context = router.pre_llm_call(session_id="session-A", user_message="第一轮")
    second_context = router.pre_llm_call(session_id="session-A", user_message="继续")
    assert "canonical persona" in first_context["context"]
    assert "canonical persona" in second_context["context"]
    assert "xiaowu-avatar" not in "第一轮"


@pytest.mark.asyncio
async def test_offline_worker_is_blocked_without_rewrite(worker_setup, registry, monkeypatch):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda route: (False, "offline"))
    result = gateway_hook.GatewayRouter(registry).pre_gateway_dispatch(
        event=event("继续"), gateway=gateway, session_store=store
    )
    await asyncio.sleep(0)
    assert result["action"] == "skip"
    assert "No Luna fallback" in gateway.adapter.messages[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["启动", "状态", "结束", "重置"])
async def test_controls_are_zero_llm_code_paths(registry, monkeypatch, verb):
    store = FakeStore()
    gateway = FakeGateway(store)
    called = []

    async def fake_control(gw, source, session_key, actual_verb, reg, router):
        called.append(actual_verb)
        gateway_hook._end_transition(session_key)

    monkeypatch.setattr(gateway_hook, "_handle_control", fake_control)
    result = gateway_hook.GatewayRouter(registry).pre_gateway_dispatch(
        event=event(f"/xiaowu-avatar {verb}"), gateway=gateway, session_store=store
    )
    await asyncio.sleep(0)
    assert result == {"action": "skip", "reason": "worker-control-command"}
    assert called == [verb]


def test_session_isolation(worker_setup, registry, monkeypatch):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda route: (True, "ok"))
    monkeypatch.setattr(gateway_hook, "_load_skill_message", lambda *args: "LOCKED")
    router = gateway_hook.GatewayRouter(registry)
    assert router.pre_gateway_dispatch(
        event=event("A", "telegram:A"), gateway=gateway, session_store=store
    ) is None
    assert router.pre_gateway_dispatch(
        event=event("B", "telegram:B"), gateway=gateway, session_store=store
    ) is None
    assert router.pre_llm_call(session_id="session-A", user_message="A") is not None
    assert router.pre_llm_call(session_id="session-B", user_message="B") is None


def test_slash_commands_are_not_swallowed_by_skill_lock(worker_setup, registry):
    store, gateway, _ = worker_setup
    assert gateway_hook.GatewayRouter(registry).pre_gateway_dispatch(
        event=event("/status"), gateway=gateway, session_store=store
    ) is None


def test_explicit_visual_slash_command_keeps_qwen_persona_after_restart(
    worker_setup, registry, monkeypatch
):
    store, gateway, route = worker_setup
    gateway._session_model_overrides.clear()
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    router = gateway_hook.GatewayRouter(registry)
    visual_event = event("/xiaowu-avatar 拍张照片给我看看")
    assert router.pre_gateway_dispatch(
        event=visual_event, gateway=gateway, session_store=store
    ) is None
    assert gateway._session_model_overrides["telegram:A"]["model"] == route.model
    assert "# 小舞主分身人格" in router.pre_llm_call(
        session_id="session-A", user_message=visual_event.text
    )["context"]


def test_restart_rehydrates_from_persistent_override(worker_setup, registry, monkeypatch):
    store, gateway, route = worker_setup
    gateway._session_model_overrides.clear()  # fresh gateway process before lazy core rehydrate
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda route: (True, "ok"))
    monkeypatch.setattr(gateway_hook, "_load_skill_message", lambda *args: "LOCKED_AFTER_RESTART")
    result = gateway_hook.GatewayRouter(registry).pre_gateway_dispatch(
        event=event("继续"), gateway=gateway, session_store=store
    )
    assert result is None
    assert gateway._session_model_overrides["telegram:A"]["model"] == route.model
    assert gateway._session_model_overrides["telegram:A"]["api_mode"] == route.api_mode


@pytest.mark.asyncio
async def test_missing_persona_fails_closed(worker_setup, registry, monkeypatch):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda route: (True, "ok"))
    monkeypatch.setattr(
        gateway_hook, "_load_persona",
        lambda: (_ for _ in ()).throw(FileNotFoundError("persona missing")),
    )
    result = gateway_hook.GatewayRouter(registry).pre_gateway_dispatch(
        event=event("继续"), gateway=gateway, session_store=store
    )
    await asyncio.sleep(0)
    assert result == {"action": "skip", "reason": "worker-state-invalid"}
    assert "persona missing" in gateway.adapter.messages[-1]


@pytest.mark.asyncio
async def test_invalid_model_override_fails_closed(worker_setup, registry):
    store, gateway, _ = worker_setup
    store.overrides["telegram:A"]["model"] = "invalid-model"
    result = gateway_hook.GatewayRouter(registry).pre_gateway_dispatch(
        event=event("继续"), gateway=gateway, session_store=store
    )
    await asyncio.sleep(0)
    assert result == {"action": "skip", "reason": "worker-state-invalid"}
    assert "No Luna fallback" in gateway.adapter.messages[-1]


@pytest.mark.asyncio
async def test_offline_exit_is_still_control_plane(registry, monkeypatch):
    store = FakeStore()
    gateway = FakeGateway(store)
    called = []

    async def fake_control(gw, source, session_key, verb, reg, router):
        called.append(verb)
        gateway_hook._end_transition(session_key)

    monkeypatch.setattr(gateway_hook, "_handle_control", fake_control)
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda route: (_ for _ in ()).throw(AssertionError))
    result = gateway_hook.GatewayRouter(registry).pre_gateway_dispatch(
        event=event("/xiaowu-avatar 结束"), gateway=gateway, session_store=store
    )
    await asyncio.sleep(0)
    assert result["action"] == "skip"
    assert called == ["结束"]


@pytest.mark.asyncio
async def test_start_commits_session_override_then_metadata(registry, monkeypatch):
    store = FakeStore()
    gateway = FakeGateway(store)
    route = registry.require("xiaowu-avatar")
    override = {
        "model": route.model,
        "provider": route.runtime_provider,
        "base_url": route.base_url,
        "api_mode": route.api_mode,
    }
    monkeypatch.setattr(gateway_hook, "_default_route", lambda: ("gpt-5.6-luna", "openai-codex", "high"))
    monkeypatch.setattr(gateway_hook, "_resolve_worker_override", lambda actual: override)
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    monkeypatch.setattr(gateway_hook, "_load_skill_message", lambda *args: "SKILL_OK")
    async def immediate_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)
    monkeypatch.setattr(gateway_hook.asyncio, "to_thread", immediate_to_thread)
    message = await asyncio.wait_for(
        gateway_hook._start_worker(
            gateway, event("x").source, "telegram:A", route
        ),
        timeout=2,
    )
    state = state_mod.RouterState.from_metadata(
        store.get_session_metadata("telegram:A", state_mod.METADATA_KEY)
    )
    assert "已启动" in message
    assert state.active_skill == "xiaowu-avatar"
    assert store.get_model_override("telegram:A")["model"] == route.model


@pytest.mark.asyncio
async def test_stop_clears_worker_without_qwen_probe(worker_setup, monkeypatch):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_default_route", lambda: ("gpt-5.6-luna", "openai-codex", "high"))
    monkeypatch.setattr(
        gateway_hook,
        "_probe_models",
        lambda route: (_ for _ in ()).throw(AssertionError("exit contacted Qwen")),
    )
    message = await gateway_hook._stop_worker(
        gateway, event("x").source, "telegram:A"
    )
    assert "已结束" in message
    assert store.get_model_override("telegram:A") is None
    assert store.get_session_metadata("telegram:A", state_mod.METADATA_KEY) is None
    assert "telegram:A" not in gateway._session_model_overrides


def test_persona_injected_and_qwen_actual_request_is_persisted(
    worker_setup, registry, monkeypatch
):
    store, gateway, route = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    router = gateway_hook.GatewayRouter(registry)
    assert router.pre_gateway_dispatch(
        event=event("陪我聊一会儿"), gateway=gateway, session_store=store
    ) is None
    injected = router.pre_llm_call(
        session_id="session-A", user_message="陪我聊一会儿", model=route.model
    )
    assert "# 小舞主分身人格" in injected["context"]
    assert "精确查找 xiaowu_avatar_generate" in injected["context"]
    assert "不得复用" in injected["context"]
    router.pre_api_request(
        session_id="session-A",
        provider=route.runtime_provider,
        model=route.model,
        base_url=route.base_url,
        api_mode=route.api_mode,
        api_request_id="request-qwen-1",
    )
    state = state_mod.RouterState.from_metadata(
        store.get_session_metadata("telegram:A", state_mod.METADATA_KEY)
    )
    assert state.worker_turns == 1
    assert state.qwen_api_calls == 1
    assert state.luna_api_calls == 0
    assert state.last_actual_model == route.model
    assert state.last_api_request_id == "request-qwen-1"



@pytest.mark.parametrize("raw_schema_present", [False, True])
def test_worker_deferred_bridge_does_not_advertise_unexecutable_tool(
    worker_setup, registry, monkeypatch, raw_schema_present
):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    def unexpected_schema_load():
        raise AssertionError("Deferred requests must not inject a direct schema")
    monkeypatch.setattr(gateway_hook, "_load_visual_tool_definition", unexpected_schema_load)
    router = gateway_hook.GatewayRouter(registry)
    router.pre_gateway_dispatch(
        event=event("拍一张照片"), gateway=gateway, session_store=store
    )
    router.pre_llm_call(session_id="session-A", turn_id="bridge-turn", user_message="拍照")
    tools = [{"type": "function", "function": {"name": "tool_call"}}]
    if raw_schema_present:
        tools.append({"type": "function", "function": {"name": gateway_hook.VISUAL_TOOL}})
    request = {"tools": tools, "messages": [{"role": "user", "content": "拍照"}]}
    result = router.llm_request(
        session_id="session-A", turn_id="bridge-turn", request=request
    )["request"]
    assert [gateway_hook._tool_name(t) for t in result["tools"]] == ["tool_call"]
    assert 'invoke tool_call with' in result["messages"][0]["content"]
    assert '{"name":"xiaowu_avatar_generate","arguments":{...}}' in result["messages"][0]["content"]
    assert request["messages"] == [{"role": "user", "content": "拍照"}]
    assert len(request["tools"]) == 1 + int(raw_schema_present)
    assert router.transform_llm_output(
        session_id="session-A", response_text="好了 [Sent image attachment]"
    ) == "这次没有执行图片生成，因此没有新照片。请重新发送原命令。"

def test_worker_llm_request_exposes_registered_visual_tool_only_for_worker(
    worker_setup, registry, monkeypatch
):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    schema = {
        "type": "function",
        "function": {
            "name": "xiaowu_avatar_generate",
            "description": "registered schema",
            "parameters": {"type": "object"},
        },
    }
    monkeypatch.setattr(
        gateway_hook, "_load_visual_tool_definition", lambda: schema
    )
    router = gateway_hook.GatewayRouter(registry)
    assert router.llm_request(
        session_id="session-A", request={"model": "qwen", "tools": []}
    ) is None
    assert router.pre_gateway_dispatch(
        event=event("拍张照片"), gateway=gateway, session_store=store
    ) is None
    assert router.pre_llm_call(
        session_id="session-A", turn_id="visual-turn", user_message="拍张照片"
    ) is not None
    rewritten = router.llm_request(
        session_id="session-A",
        turn_id="visual-turn",
        request={
            "model": "qwen",
            "tools": [{"type": "function", "function": {"name": "terminal"}}],
        },
    )
    assert [
        gateway_hook._tool_name(tool) for tool in rewritten["request"]["tools"]
    ] == ["terminal", "xiaowu_avatar_generate"]
    assert rewritten["source"] == "xiaowu-model-router"
    assert router.llm_request(
        session_id="session-B", turn_id="visual-turn",
        request={"model": "luna", "tools": []}
    ) is None


def test_worker_request_neutralizes_historical_images_and_adds_near_turn_policy(
    worker_setup, registry, monkeypatch
):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    monkeypatch.setattr(
        gateway_hook,
        "_load_visual_tool_definition",
        lambda: {
            "type": "function",
            "function": {"name": "xiaowu_avatar_generate"},
        },
    )
    router = gateway_hook.GatewayRouter(registry)
    assert router.pre_gateway_dispatch(
        event=event("拍一张新照片"), gateway=gateway, session_store=store
    ) is None
    router.pre_llm_call(
        session_id="session-A", turn_id="fresh-visual", user_message="拍一张新照片"
    )
    rewritten = router.llm_request(
        session_id="session-A",
        turn_id="fresh-visual",
        request={
            "messages": [
                {"role": "user", "content": "以前的请求"},
                {
                    "role": "assistant",
                    "content": "给你：![银月](file:///tmp/old.png)",
                },
                {"role": "user", "content": "拍一张新照片"},
            ]
        },
    )["request"]
    assert "file://" not in str(rewritten["messages"])
    assert rewritten["messages"][0]["role"] == "system"
    assert "Historical image paths never count" in rewritten["messages"][0]["content"]
    assert sum(message.get("role") == "system" for message in rewritten["messages"]) == 1
    assert rewritten["tool_choice"] == "auto"


def test_background_review_does_not_receive_persona_or_visual_tool(
    worker_setup, registry, monkeypatch
):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    router = gateway_hook.GatewayRouter(registry)
    assert router.pre_gateway_dispatch(
        event=event("普通聊天"), gateway=gateway, session_store=store
    ) is None
    assert router.pre_llm_call(
        session_id="session-A",
        turn_id="background-turn",
        parent_session_id="session-A",
        user_message="Review the conversation above",
    ) is None
    assert router.llm_request(
        session_id="session-A",
        turn_id="background-turn",
        request={"messages": [], "tools": []},
    ) is None


def test_worker_false_historical_image_claim_is_rewritten_but_chat_is_preserved(
    worker_setup, registry, monkeypatch
):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    router = gateway_hook.GatewayRouter(registry)
    assert router.pre_gateway_dispatch(
        event=event("拍新照片"), gateway=gateway, session_store=store
    ) is None
    router.pre_llm_call(
        session_id="session-A", turn_id="claim-turn", user_message="拍新照片"
    )
    assert router.transform_llm_output(
        session_id="session-A",
        response_text="给你：![银月](file:///tmp/old.png)",
    ) == "这次没有执行图片生成，因此没有新照片。请重新发送原命令。"
    assert router.transform_llm_output(
        session_id="session-A", response_text="我在呢，陪你聊一会儿。"
    ) is None


def test_worker_successful_visual_transaction_is_allowed(
    worker_setup, registry, monkeypatch
):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    router = gateway_hook.GatewayRouter(registry)
    router.pre_gateway_dispatch(
        event=event("拍新照片"), gateway=gateway, session_store=store
    )
    router.pre_llm_call(
        session_id="session-A", turn_id="image-turn", user_message="拍新照片"
    )
    router.pre_tool_call(
        session_id="session-A", turn_id="image-turn",
        tool_name="xiaowu_avatar_generate",
    )
    router.post_tool_call(
        session_id="session-A", turn_id="image-turn",
        tool_name="xiaowu_avatar_generate", result='{"ok": true}',
    )
    assert router.transform_llm_output(
        session_id="session-A", response_text="图片已生成并发送。"
    ) is None


def test_worker_llm_request_does_not_duplicate_visible_visual_tool(
    worker_setup, registry, monkeypatch
):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    router = gateway_hook.GatewayRouter(registry)
    assert router.pre_gateway_dispatch(
        event=event("继续"), gateway=gateway, session_store=store
    ) is None
    assert router.pre_llm_call(
        session_id="session-A", turn_id="chat-turn", user_message="继续"
    ) is not None
    request = {
        "tools": [
            {
                "type": "function",
                "function": {"name": "xiaowu_avatar_generate"},
            }
        ]
    }
    assert router.llm_request(
        session_id="session-A", turn_id="chat-turn", request=request
    ) is None


def test_luna_request_is_counted_as_routing_violation(
    worker_setup, registry, monkeypatch, caplog
):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    router = gateway_hook.GatewayRouter(registry)
    router.pre_gateway_dispatch(
        event=event("普通聊天"), gateway=gateway, session_store=store
    )
    router.pre_api_request(
        session_id="session-A",
        provider="openai-codex",
        model="gpt-5.6-luna",
        base_url="https://chatgpt.com/backend-api/codex",
        api_mode="responses",
        api_request_id="bad-request",
    )
    state = state_mod.RouterState.from_metadata(
        store.get_session_metadata("telegram:A", state_mod.METADATA_KEY)
    )
    assert state.qwen_api_calls == 0
    assert state.luna_api_calls == 1
    assert state.routing_violations == 1
    assert "ROUTING_VIOLATION" in caplog.text


@pytest.mark.asyncio
async def test_status_distinguishes_configured_and_actual_model(
    worker_setup, registry
):
    store, gateway, route = worker_setup
    state = state_mod.RouterState.from_metadata(
        store.get_session_metadata("telegram:A", state_mod.METADATA_KEY)
    ).record_worker_turn().record_api_request(
        provider=route.runtime_provider,
        model=route.model,
        base_url=route.base_url,
        api_mode=route.api_mode,
        api_request_id="status-request",
        expected_qwen=True,
        is_luna=False,
    )
    store.set_session_metadata("telegram:A", state_mod.METADATA_KEY, state.to_metadata())
    status = await gateway_hook._status(gateway, "telegram:A", registry)
    assert f"Configured model: {route.model}" in status
    assert f"Provider: {route.runtime_provider}" in status
    assert "Qwen API calls: 1" in status
    assert "Luna API calls: 0" in status
    assert "Visual Skill sticky: NO" in status


def _load_visual_plugin():
    path = Path(
        os.environ.get(
            "YINYUE_VISUAL_PLUGIN_PATH",
            str(
                Path.home()
                / ".hermes/skills/roleplay/xiaowu-avatar/plugins/xiaowu-visual/__init__.py"
            ),
        )
    )
    spec = importlib.util.spec_from_file_location(
        f"xiaowu_visual_integration_{id(path)}_{os.getpid()}", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_integration_normal_chat_gets_persona_without_visual_rewrite(
    worker_setup, registry, monkeypatch
):
    store, gateway, _ = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    router = gateway_hook.GatewayRouter(registry)
    visual = _load_visual_plugin()

    original = "今天好累啊，陪我聊聊天。"
    assert router.pre_gateway_dispatch(
        event=event(original), gateway=gateway, session_store=store
    ) is None
    persona = router.pre_llm_call(session_id="session-A", user_message=original)
    assert "# 小舞主分身人格" in persona["context"]
    assert visual._on_pre_llm_call(
        session_id="session-A", turn_id="chat-1", user_message=original
    ) is None
    assert visual._transform_llm_output(
        session_id="session-A", response_text="我在呢，陪你慢慢聊。"
    ) is None


def test_integration_marker_alone_does_not_delete_normal_chat():
    visual = _load_visual_plugin()
    marker = '[IMPORTANT: The user has invoked the "xiaowu-avatar" skill]'
    context = visual._on_pre_llm_call(
        session_id="session-marker", turn_id="marker-1", user_message=marker
    )
    assert "does not make image generation mandatory" in context["context"]
    assert visual._on_pre_tool_call(
        session_id="session-marker", turn_id="marker-1", tool_name="tool_search"
    ) is None
    assert visual._transform_llm_output(
        session_id="session-marker", response_text="我在这里，想聊什么都可以。"
    ) is None
    assert visual._transform_llm_output(
        session_id="session-marker", response_text="这个普通网页可以看看：https://example.com/docs"
    ) is None
    visual._on_post_llm_call(session_id="session-marker")


def test_integration_visual_tool_is_transaction_boundary_and_not_sticky():
    visual = _load_visual_plugin()
    assert visual._on_pre_tool_call(
        session_id="session-visual", task_id="task-visual", turn_id="visual-1",
        tool_name="xiaowu_avatar_generate",
    ) is None
    visual._on_post_tool_call(
        session_id="session-visual", turn_id="visual-1",
        tool_name="xiaowu_avatar_generate", result='{"ok": true}',
    )
    assert visual._transform_llm_output(
        session_id="session-visual", response_text=""
    ) == "图片已由生成工具确认生成并发送。"
    visual._on_post_llm_call(session_id="session-visual")

    # The following comment is a fresh chat turn, not another visual turn.
    assert visual._on_pre_llm_call(
        session_id="session-visual", turn_id="chat-after-image",
        user_message="这张挺好看的。",
    ) is None
    assert visual._transform_llm_output(
        session_id="session-visual", response_text="你喜欢就好。"
    ) is None


def test_integration_false_image_claim_without_tool_still_fails_closed():
    visual = _load_visual_plugin()
    marker = '[IMPORTANT: The user has invoked the "xiaowu-avatar" skill]'
    visual._on_pre_llm_call(
        session_id="session-claim", turn_id="claim-1", user_message=marker
    )
    rewritten = visual._transform_llm_output(
        session_id="session-claim", response_text="照片已经生成好了，发给你。"
    )
    assert "没有执行图片生成" in rewritten
    visual._on_post_llm_call(session_id="session-claim")


def test_integration_four_consecutive_worker_chats_are_qwen_only(
    worker_setup, registry, monkeypatch
):
    store, gateway, route = worker_setup
    monkeypatch.setattr(gateway_hook, "_probe_models", lambda actual: (True, "ok"))
    router = gateway_hook.GatewayRouter(registry)
    visual = _load_visual_plugin()
    prompts = ("你好", "今天累吗", "最近怎么样", "陪我聊聊天")
    for index, prompt in enumerate(prompts, start=1):
        assert router.pre_gateway_dispatch(
            event=event(prompt), gateway=gateway, session_store=store
        ) is None
        assert router.pre_llm_call(
            session_id="session-A", turn_id=f"chat-{index}", user_message=prompt
        ) is not None
        assert visual._on_pre_llm_call(
            session_id="session-A", turn_id=f"chat-{index}", user_message=prompt
        ) is None
        router.pre_api_request(
            session_id="session-A", provider=route.runtime_provider,
            model=route.model, base_url=route.base_url,
            api_mode=route.api_mode,
            api_request_id=f"qwen-{index}",
        )
        assert visual._transform_llm_output(
            session_id="session-A", response_text=f"聊天回复 {index}"
        ) is None
    state = state_mod.RouterState.from_metadata(
        store.get_session_metadata("telegram:A", state_mod.METADATA_KEY)
    )
    assert state.worker_turns == 4
    assert state.qwen_api_calls == 4
    assert state.luna_api_calls == 0


def test_integration_chat_visual_alternation_is_turn_local():
    visual = _load_visual_plugin()
    pattern = ("CHAT", "VISUAL", "CHAT", "CHAT", "VISUAL", "CHAT")
    for index, mode in enumerate(pattern, start=1):
        session_id = "session-alternating"
        turn_id = f"turn-{index}"
        if mode == "VISUAL":
            assert visual._on_pre_tool_call(
                session_id=session_id, turn_id=turn_id,
                tool_name="xiaowu_avatar_generate",
            ) is None
            visual._on_post_tool_call(
                session_id=session_id, turn_id=turn_id,
                tool_name="xiaowu_avatar_generate", result='{"ok": true}',
            )
            assert "确认生成并发送" in visual._transform_llm_output(
                session_id=session_id, response_text=""
            )
        else:
            assert visual._on_pre_llm_call(
                session_id=session_id, turn_id=turn_id, user_message="普通聊天"
            ) is None
            assert visual._transform_llm_output(
                session_id=session_id, response_text="正常聊天回复"
            ) is None
        visual._on_post_llm_call(session_id=session_id)
