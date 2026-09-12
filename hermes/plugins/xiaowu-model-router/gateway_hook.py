"""Hermes gateway integration for deterministic sticky worker routing."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from .router import Route, RouteRegistry, is_slash_command, parse_control_command
    from .state import METADATA_KEY, RouterState, RouterStateError, override_matches
except ImportError:  # Standalone validation outside the Hermes package loader.
    from router import Route, RouteRegistry, is_slash_command, parse_control_command
    from state import METADATA_KEY, RouterState, RouterStateError, override_matches


logger = logging.getLogger("xiaowu-model-router")
SKILL = "xiaowu-avatar"
VISUAL_TOOL = "xiaowu_avatar_generate"
_TRANSITION_GUARD = threading.Lock()
_TRANSITIONS: set[str] = set()
SKILL_ROOT = Path(
    os.environ.get(
        "XIAOWU_AVATAR_ROOT",
        str(Path.home() / ".hermes" / "skills" / "roleplay" / "xiaowu-avatar"),
    )
).expanduser().resolve()
PERSONA_PATH = SKILL_ROOT / "persona.md"
_PERSONA_LOCK = threading.Lock()
_PERSONA_SIGNATURE: tuple[int, int] | None = None
_PERSONA_CONTENT = ""
_HISTORICAL_IMAGE_RE = re.compile(
    r"(?i)(?:file://\S+\.(?:png|jpe?g|webp|gif)|MEDIA:\s*\S+|!\[[^\]]*\]\([^)]*\.(?:png|jpe?g|webp|gif)(?:\?[^)]*)?\))"
)
_FALSE_IMAGE_CLAIM_RE = re.compile(
    r"(?i)(?:file://\S+\.(?:png|jpe?g|webp|gif)|https?://\S+?\.(?:png|jpe?g|webp|gif)(?:\?\S*)?|MEDIA:/\S+|(?:图片|照片|头像).{0,10}(?:已生成|生成好了|已发送|发给你)|(?:已生成|生成好了).{0,10}(?:图片|照片|头像))"
)


def _tool_name(tool: Any) -> str:
    """Return an OpenAI-format tool definition's function name."""
    if not isinstance(tool, dict):
        return ""
    function = tool.get("function")
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return str(tool.get("name") or "")


def _load_visual_tool_definition() -> dict[str, Any] | None:
    """Resolve the visual tool's canonical schema from Hermes' registry.

    Tool Search normally defers plugin tools. Worker Mode must leave the
    semantic decision to Qwen, so its one image transaction tool is made
    directly visible without activating the visual Skill for the turn.
    """
    from model_tools import get_tool_definitions

    definitions = get_tool_definitions(
        quiet_mode=True,
        skip_tool_search_assembly=True,
    )
    for definition in definitions or []:
        if _tool_name(definition) == VISUAL_TOOL:
            return dict(definition)
    return None


def _prepare_worker_messages(messages: Any) -> list[Any] | None:
    """Add a near-turn routing invariant and neutralize stale image evidence."""
    if not isinstance(messages, list):
        return None
    prepared: list[Any] = []
    system_parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            prepared.append(message)
            continue
        item = dict(message)
        role = str(item.get("role") or "")
        content = item.get("content")
        if role == "system":
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue
        if role == "assistant" and isinstance(content, str) and _HISTORICAL_IMAGE_RE.search(content):
            item["content"] = (
                "[Historical assistant turn referenced an old image. The old image "
                "is not a result of the current turn and cannot satisfy a new visual request.]"
            )
        prepared.append(item)
    system_parts.append(
            "Yinyue Worker invariant for the current user turn: decide the user's "
            "intent yourself. For a new photo, appearance view, outfit/pose/scene "
            "change, or continuation that needs a rendered result, call "
            "xiaowu_avatar_generate exactly once. For ordinary or emotional chat, "
            "answer normally and do not call it. Historical image paths never count "
            "as a current result and must not be repeated. If the tool is not called "
            "successfully in this turn, do not claim or link a new image. Never ask "
            "Luna to classify the request."
    )
    # The target Qwen chat template requires the sole system message to be the
    # first item. Merge any existing system text instead of inserting a second
    # system role near the current user message.
    return [{"role": "system", "content": "\n\n".join(system_parts)}] + prepared


def _load_persona() -> str:
    """Read the canonical persona with mtime/size cache invalidation."""
    global _PERSONA_SIGNATURE, _PERSONA_CONTENT
    stat = PERSONA_PATH.stat()
    signature = (int(stat.st_mtime_ns), int(stat.st_size))
    with _PERSONA_LOCK:
        if signature != _PERSONA_SIGNATURE:
            content = PERSONA_PATH.read_text(encoding="utf-8").strip()
            if not content:
                raise RuntimeError(f"persona is empty: {PERSONA_PATH}")
            _PERSONA_CONTENT = content
            _PERSONA_SIGNATURE = signature
        return _PERSONA_CONTENT


def _invalidate_persona_cache() -> None:
    global _PERSONA_SIGNATURE, _PERSONA_CONTENT
    with _PERSONA_LOCK:
        _PERSONA_SIGNATURE = None
        _PERSONA_CONTENT = ""


def _session_hash(session_key: str) -> str:
    return hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:8]


def _begin_transition(session_key: str) -> bool:
    with _TRANSITION_GUARD:
        if session_key in _TRANSITIONS:
            return False
        _TRANSITIONS.add(session_key)
        return True


def _end_transition(session_key: str) -> None:
    with _TRANSITION_GUARD:
        _TRANSITIONS.discard(session_key)


def _transitioning(session_key: str) -> bool:
    with _TRANSITION_GUARD:
        return session_key in _TRANSITIONS


def _event_texts(event: Any) -> tuple[str, str]:
    current = str(getattr(event, "text", "") or "")
    raw_message = getattr(event, "raw_message", None)
    raw = str(
        getattr(raw_message, "text", "")
        or getattr(raw_message, "caption", "")
        or ""
    )
    return current, raw


def _is_xiaowu_visual_command(*texts: Any) -> bool:
    for value in texts:
        parts = str(value or "").lstrip().split(maxsplit=1)
        if not parts:
            continue
        first = parts[0].lower()
        first = first.split("@", 1)[0]
        if first in {"/xiaowu-avatar", "/yinyue_avatar"}:
            return True
    return False


def _is_authorized(gateway: Any, source: Any) -> bool:
    check = getattr(gateway, "_is_user_authorized", None)
    if not callable(check):
        return False
    try:
        return check(source) is True
    except Exception:
        logger.warning("router authorization check failed", exc_info=True)
        return False


def _resolve_session(gateway: Any, source: Any) -> tuple[Any, str]:
    normalize = getattr(gateway, "_normalize_source_for_session_key", None)
    normalized = normalize(source) if callable(normalize) else source
    return normalized, str(gateway._session_key_for_source(normalized))


def _probe_models(route: Route) -> tuple[bool, str]:
    request = urllib.request.Request(
        f"{route.base_url}/models",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=route.endpoint_timeout_seconds) as response:
            if int(getattr(response, "status", 200)) != 200:
                return False, f"HTTP {getattr(response, 'status', 'unknown')}"
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, ValueError, urllib.error.URLError) as exc:
        return False, type(exc).__name__
    model_ids: set[str] = set()
    for item in payload.get("data", []) if isinstance(payload, dict) else []:
        if isinstance(item, dict) and item.get("id"):
            model_ids.add(str(item["id"]))
    for item in payload.get("models", []) if isinstance(payload, dict) else []:
        if isinstance(item, dict):
            candidate = item.get("model") or item.get("name") or item.get("id")
            if candidate:
                model_ids.add(str(candidate))
    if route.model not in model_ids:
        return False, "model unavailable"
    return True, "ok"


def _load_skill_message(skill: str, prompt: str, session_id: str) -> str | None:
    from agent.skill_commands import build_skill_invocation_message, get_skill_commands

    commands = get_skill_commands()
    key = f"/{skill}"
    if key not in commands:
        return None
    return build_skill_invocation_message(
        key,
        prompt,
        task_id=session_id,
        runtime_note=(
            "Worker startup availability probe only. The visual Skill is available "
            "on demand and is not sticky between turns."
        ),
    )


async def _send_code_reply(gateway: Any, source: Any, message: str) -> None:
    try:
        adapter = gateway._adapter_for_source(source)
        if adapter is None:
            logger.warning("router reply has no adapter")
            return
        metadata_fn = getattr(gateway, "_thread_metadata_for_source", None)
        metadata = metadata_fn(source) if callable(metadata_fn) else None
        try:
            await adapter.send(str(source.chat_id), message, metadata=metadata)
        except TypeError:
            await adapter.send(str(source.chat_id), message)
    except Exception:
        logger.warning("router code reply failed", exc_info=True)


def _schedule_reply(gateway: Any, source: Any, message: str) -> None:
    try:
        asyncio.get_running_loop().create_task(_send_code_reply(gateway, source, message))
    except RuntimeError:
        logger.warning("router could not schedule a code reply: no event loop")


def _load_config() -> dict[str, Any]:
    from hermes_cli.config import load_config

    value = load_config()
    return value if isinstance(value, dict) else {}


def _default_route() -> tuple[str, str, str]:
    cfg = _load_config()
    model_cfg = cfg.get("model") or {}
    if isinstance(model_cfg, str):
        return model_cfg, "", "high"
    agent_cfg = cfg.get("agent") or {}
    return (
        str(model_cfg.get("default") or model_cfg.get("model") or ""),
        str(model_cfg.get("provider") or ""),
        str(agent_cfg.get("reasoning_effort") or "high"),
    )


def _resolve_worker_override(route: Route) -> dict[str, Any]:
    from hermes_cli.config import get_compatible_custom_providers
    from hermes_cli.model_switch import switch_model

    cfg = _load_config()
    model_cfg = cfg.get("model") or {}
    if not isinstance(model_cfg, dict):
        model_cfg = {"default": str(model_cfg or "")}
    result = switch_model(
        raw_input=route.model,
        current_provider=str(model_cfg.get("provider") or ""),
        current_model=str(model_cfg.get("default") or model_cfg.get("model") or ""),
        current_base_url=str(model_cfg.get("base_url") or ""),
        current_api_key="",
        is_global=False,
        explicit_provider=route.provider,
        user_providers=cfg.get("providers"),
        custom_providers=get_compatible_custom_providers(cfg),
    )
    if not result.success:
        raise RuntimeError(result.error_message or "provider resolution failed")
    if (
        result.new_model != route.model
        or result.target_provider != route.runtime_provider
        or str(result.base_url or "").rstrip("/") != route.base_url
        or result.api_mode != route.api_mode
    ):
        raise RuntimeError("resolved provider route does not match routes.yaml")
    override = {
        "model": result.new_model,
        "provider": result.target_provider,
        "base_url": result.base_url,
        "api_mode": result.api_mode,
    }
    if result.api_key:
        override["api_key"] = result.api_key
    return override


async def _update_session_db(gateway: Any, session_id: str, model: str, provider: str) -> None:
    session_db = getattr(gateway, "_session_db", None)
    if session_db is not None:
        await session_db.update_session_model(session_id, model, provider=provider)


@dataclass
class _Snapshot:
    memory_override: Any
    persisted_override: Any
    metadata: Any
    reasoning_override: Any
    default_model: str
    default_provider: str


async def _snapshot(gateway: Any, store: Any, session_key: str) -> _Snapshot:
    memory = dict((getattr(gateway, "_session_model_overrides", {}) or {}).get(session_key) or {}) or None
    persisted = await gateway.async_session_store.get_model_override(session_key)
    metadata = await gateway.async_session_store.get_session_metadata(session_key, METADATA_KEY, None)
    reasoning = (getattr(gateway, "_session_reasoning_overrides", {}) or {}).get(session_key)
    default_model, default_provider, _ = _default_route()
    return _Snapshot(memory, persisted, metadata, reasoning, default_model, default_provider)


async def _restore_snapshot(
    gateway: Any, session_key: str, session_id: str, snapshot: _Snapshot
) -> None:
    mapping = getattr(gateway, "_session_model_overrides")
    if snapshot.memory_override is None:
        mapping.pop(session_key, None)
    else:
        mapping[session_key] = dict(snapshot.memory_override)
    set_reasoning = getattr(gateway, "_set_session_reasoning_override")
    set_reasoning(session_key, snapshot.reasoning_override)
    await gateway.async_session_store.set_model_override(session_key, snapshot.persisted_override)
    await gateway.async_session_store.set_session_metadata(session_key, METADATA_KEY, snapshot.metadata)
    restore_model = (
        str((snapshot.persisted_override or {}).get("model") or snapshot.default_model)
    )
    restore_provider = (
        str((snapshot.persisted_override or {}).get("provider") or snapshot.default_provider)
    )
    await _update_session_db(gateway, session_id, restore_model, restore_provider)
    gateway._evict_cached_agent(session_key)


async def _start_worker(gateway: Any, source: Any, session_key: str, route: Route) -> str:
    if gateway._is_session_running(session_key):
        return "当前 Session 仍有 Agent 在运行。请等待完成或先执行 /stop，再启动 Worker Mode。"

    entry = await gateway.async_session_store.get_or_create_session(source)
    session_id = str(entry.session_id)
    previous = await _snapshot(gateway, gateway.session_store, session_key)
    # Worker Mode is not valid without its canonical chat persona. Validate it
    # before touching any session routing state.
    _load_persona()

    existing = RouterState.from_metadata(previous.metadata)
    if existing is not None:
        existing.validate_route(route)
        healthy, detail = await asyncio.to_thread(_probe_models, route)
        if not healthy:
            return (
                "Worker Mode execution blocked:\n"
                f"Qwen endpoint unavailable ({detail}).\n\n"
                "No Luna fallback was performed."
            )
        if override_matches(previous.persisted_override, existing):
            live_override = await asyncio.to_thread(_resolve_worker_override, route)
            gateway._session_model_overrides[session_key] = dict(live_override)
            gateway._set_session_reasoning_override(session_key, None)
            gateway._evict_cached_agent(session_key)
            return (
                "xiaowu-avatar Worker Mode 已处于启动状态。\n"
                f"当前模型：{route.model}\n"
                "当前 Persona：xiaowu-avatar\n"
                "Visual Skill Sticky：NO"
            )

    override = await asyncio.to_thread(_resolve_worker_override, route)
    healthy, detail = await asyncio.to_thread(_probe_models, route)
    if not healthy:
        raise RuntimeError(f"Qwen endpoint unavailable ({detail})")
    # Skill loading is the same bounded local operation used by the synchronous
    # pre-dispatch rewrite below. Keeping it on this task also avoids competing
    # with Hermes' gateway executor during an atomic control transition.
    skill_probe = _load_skill_message(route.skill, "", session_id)
    if not skill_probe:
        raise RuntimeError(f"skill {route.skill!r} is not loadable")

    state = RouterState.activate(session_id, route, str(override["provider"]))
    try:
        gateway._session_model_overrides[session_key] = dict(override)
        gateway._set_session_reasoning_override(session_key, None)
        await gateway.async_session_store.set_model_override(session_key, override)
        await _update_session_db(gateway, session_id, route.model, str(override["provider"]))
        if not await gateway.async_session_store.set_session_metadata(
            session_key, METADATA_KEY, state.to_metadata()
        ):
            raise RuntimeError("session metadata write failed")
        persisted = await gateway.async_session_store.get_model_override(session_key)
        stored_state = RouterState.from_metadata(
            await gateway.async_session_store.get_session_metadata(
                session_key, METADATA_KEY, None
            )
        )
        if stored_state is None or not override_matches(persisted, stored_state):
            raise RuntimeError("session override verification failed")
        gateway._evict_cached_agent(session_key)
    except Exception:
        await _restore_snapshot(gateway, session_key, session_id, previous)
        raise

    logger.info(
        "session=%s transition=NORMAL->SKILL_WORKER skill=%s model=%s result=success",
        _session_hash(session_key), route.skill, route.model,
    )
    return (
        "xiaowu-avatar Worker Mode 已启动。\n"
        f"当前模型：{route.model}\n"
        "当前 Persona：xiaowu-avatar\n"
        "Visual Skill Sticky：NO"
    )


async def _stop_worker(gateway: Any, source: Any, session_key: str) -> str:
    if gateway._is_session_running(session_key):
        await gateway._interrupt_and_clear_session(
            session_key,
            source,
            interrupt_reason="xiaowu_worker_exit",
            invalidation_reason="xiaowu_worker_exit",
        )
    entry = await gateway.async_session_store.get_or_create_session(source)
    default_model, default_provider, default_reasoning = _default_route()
    failures: list[str] = []
    try:
        gateway._session_model_overrides.pop(session_key, None)
        gateway._set_session_reasoning_override(session_key, None)
        gateway._evict_cached_agent(session_key)
    except Exception as exc:
        failures.append(f"memory:{type(exc).__name__}")
    try:
        await gateway.async_session_store.set_model_override(session_key, None)
    except Exception as exc:
        failures.append(f"override:{type(exc).__name__}")
    # Keep worker metadata until the override is definitely cleared. If an
    # earlier step fails, leaving metadata active makes the next prompt hit the
    # fail-closed integrity check instead of silently becoming an unlocked turn.
    if not failures:
        try:
            await gateway.async_session_store.set_session_metadata(
                session_key, METADATA_KEY, None
            )
        except Exception as exc:
            failures.append(f"state:{type(exc).__name__}")
    if not failures:
        try:
            await _update_session_db(
                gateway, str(entry.session_id), default_model, default_provider
            )
        except Exception as exc:
            failures.append(f"db:{type(exc).__name__}")
    if failures:
        logger.error(
            "session=%s transition=SKILL_WORKER->NORMAL result=error errors=%s",
            _session_hash(session_key), ",".join(failures),
        )
        return (
            "Worker Mode 退出未能完整提交，已阻止继续路由。\n"
            "请运行 /xiaowu-avatar 状态 后重试结束；未执行 Luna fallback。"
        )
    logger.info(
        "session=%s transition=SKILL_WORKER->NORMAL result=success",
        _session_hash(session_key),
    )
    return (
        "xiaowu-avatar Worker Mode 已结束。\n"
        f"已恢复 {default_model} / reasoning {default_reasoning}。"
    )


async def _status(gateway: Any, session_key: str, registry: RouteRegistry) -> str:
    raw = await gateway.async_session_store.get_session_metadata(
        session_key, METADATA_KEY, None
    )
    try:
        state = RouterState.from_metadata(raw)
    except RouterStateError as exc:
        return (
            "Session mode: BLOCKED\n"
            f"Router state: corrupt ({exc})\n"
            "No Luna fallback will be performed."
        )
    if state is None:
        model, _, reasoning = _default_route()
        return (
            "Session mode: normal\n"
            f"Model: {model}\n"
            "Skill: none\n"
            f"Reasoning: {reasoning}"
        )
    route = registry.get(state.active_skill)
    if route is None:
        return "Session mode: BLOCKED\nRouter state: route missing\nNo Luna fallback will be performed."
    try:
        state.validate_route(route)
    except RouterStateError as exc:
        return f"Session mode: BLOCKED\nRouter state: {exc}\nNo Luna fallback will be performed."
    return (
        "Worker Mode: ON\n"
        "Persona: xiaowu-avatar\n"
        f"Configured model: {state.model}\n"
        "Visual Skill sticky: NO\n\n"
        "Last actual LLM request:\n"
        f"Provider: {state.last_actual_provider or '(none)'}\n"
        f"Model: {state.last_actual_model or '(none)'}\n"
        f"Endpoint: {state.last_actual_endpoint or '(none)'}\n\n"
        f"API mode: {state.last_actual_api_mode or '(none)'}\n\n"
        f"Worker turns: {state.worker_turns}\n"
        f"Qwen API calls: {state.qwen_api_calls}\n"
        f"Luna API calls: {state.luna_api_calls}\n"
        f"Routing violations: {state.routing_violations}"
    )


async def _handle_control(
    gateway: Any,
    source: Any,
    session_key: str,
    verb: str,
    registry: RouteRegistry,
    router: Any,
) -> None:
    try:
        route = registry.require(SKILL)
        if verb == "启动":
            message = await _start_worker(gateway, source, session_key, route)
            state = RouterState.from_metadata(
                await gateway.async_session_store.get_session_metadata(
                    session_key, METADATA_KEY, None
                )
            )
            if state is not None:
                router.remember_worker(state, gateway.session_store, session_key)
        elif verb == "结束":
            message = await _stop_worker(gateway, source, session_key)
            if "已结束" in message:
                router.forget_worker_session_key(session_key)
        elif verb == "状态":
            message = await _status(gateway, session_key, registry)
        elif verb == "重置":
            raw = await gateway.async_session_store.get_session_metadata(
                session_key, METADATA_KEY, None
            )
            state = RouterState.from_metadata(raw)
            if state is None:
                message = "xiaowu-avatar Worker Mode 尚未启动，没有 session-local runtime state 需要清理。"
            else:
                state = state.reset_runtime()
                if not await gateway.async_session_store.set_session_metadata(
                    session_key, METADATA_KEY, state.to_metadata()
                ):
                    raise RuntimeError("session metadata reset failed")
                _invalidate_persona_cache()
                _load_persona()
                router.remember_worker(state, gateway.session_store, session_key)
                message = (
                    "xiaowu-avatar Worker runtime telemetry 已重置，Persona 已重新加载。\n"
                    "Worker Mode 与 Qwen override 保持启用；Visual Skill 仍为按需调用。"
                )
        else:
            message = "未知 Worker control command。"
    except Exception as exc:
        logger.error(
            "session=%s control=%s result=error error=%s",
            _session_hash(session_key), verb, type(exc).__name__, exc_info=True,
        )
        message = (
            "Worker Mode control operation blocked:\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "No Luna fallback was performed."
        )
    finally:
        _end_transition(session_key)
    await _send_code_reply(gateway, source, message)


@dataclass
class _RuntimeWorker:
    state: RouterState
    store: Any
    session_key: str


class GatewayRouter:
    def __init__(self, registry: RouteRegistry) -> None:
        self.registry = registry
        self._runtime_lock = threading.RLock()
        self._runtime_workers: dict[str, _RuntimeWorker] = {}
        self._active_user_turns: set[tuple[str, str]] = set()
        self._current_user_turn: dict[str, str] = {}
        self._visual_started_turns: set[tuple[str, str]] = set()
        self._visual_succeeded_turns: set[tuple[str, str]] = set()

    def remember_worker(
        self, state: RouterState, store: Any, session_key: str
    ) -> None:
        with self._runtime_lock:
            self._runtime_workers[state.session_id] = _RuntimeWorker(
                state=state, store=store, session_key=session_key
            )

    def forget_worker_session_key(self, session_key: str) -> None:
        with self._runtime_lock:
            stale = [
                session_id
                for session_id, runtime in self._runtime_workers.items()
                if runtime.session_key == session_key
            ]
            for session_id in stale:
                self._runtime_workers.pop(session_id, None)

    def _update_runtime_state(
        self, session_id: str, update: Any
    ) -> RouterState | None:
        with self._runtime_lock:
            runtime = self._runtime_workers.get(session_id)
            if runtime is None:
                return None
            state = update(runtime.state)
            runtime.state = state
            try:
                persisted = runtime.store.set_session_metadata(
                    runtime.session_key, METADATA_KEY, state.to_metadata()
                )
                if persisted is False:
                    logger.error(
                        "session=%s worker telemetry persistence failed",
                        _session_hash(runtime.session_key),
                    )
            except Exception:
                logger.warning(
                    "session=%s worker telemetry persistence raised",
                    _session_hash(runtime.session_key), exc_info=True,
                )
            return state

    def pre_llm_call(self, **context: Any) -> dict[str, str] | None:
        session_id = str(context.get("session_id") or "")
        # Auxiliary/background agents inherit the parent session id. They must
        # keep their own prompt and tool surface even while the user-facing
        # session is in Worker Mode.
        if str(context.get("parent_session_id") or ""):
            return None
        turn_id = str(context.get("turn_id") or "")
        with self._runtime_lock:
            runtime = self._runtime_workers.get(session_id)
            if runtime is None:
                return None
            session_key = runtime.session_key
            self._active_user_turns.add((session_id, turn_id))
            self._current_user_turn[session_id] = turn_id
        persona = _load_persona()
        state = self._update_runtime_state(
            session_id, lambda current: current.record_worker_turn()
        )
        if state is None:
            return None
        logger.info(
            "session=%s mode=%s turn=agent persona=true visual_skill_sticky=false",
            _session_hash(session_key),
            state.mode,
        )
        return {
            "context": (
                "[Yinyue Worker Mode — canonical persona for this turn]\n"
                f"{persona}\n\n"
                "[Worker routing rules]\n"
                "你是当前 Worker 的主 Agent，必须自行判断当前这一条消息是否需要新的"
                "图片或视觉状态操作；不得调用 Luna 分类。若需要，必须先用 tool_search "
                "精确查找 xiaowu_avatar_generate（工具已可见时跳过查找），然后在当前轮"
                "恰好调用一次 xiaowu_avatar_generate。若不需要，则正常进行情感聊天，"
                "不要调用视觉工具。视觉能力不跨轮 Sticky。无论历史消息写过什么，当前"
                "轮没有成功调用生成工具时，绝对不得声称生成或发送了新照片，不得复用、"
                "输出或链接任何历史图片路径、file://、MEDIA: 或图片 URL。用户只是评论"
                "上一张图片时属于普通聊天，不得再次生成。"
            )
        }

    def llm_request(self, **context: Any) -> dict[str, Any] | None:
        """Expose exactly one on-demand visual tool to Worker-mode Qwen.

        This does not classify the user's message and does not invoke a Skill.
        Qwen receives the ordinary chat request plus the registered transaction
        tool schema and decides natively whether the current turn needs it.
        """
        session_id = str(context.get("session_id") or "")
        turn_id = str(context.get("turn_id") or "")
        with self._runtime_lock:
            runtime = self._runtime_workers.get(session_id)
            if runtime is None or (session_id, turn_id) not in self._active_user_turns:
                return None
            session_key = runtime.session_key
        request = context.get("request")
        if not isinstance(request, dict):
            return None
        tools = list(request.get("tools") or [])
        # Request middleware cannot change the executor's valid tool names.
        if any(_tool_name(tool) == "tool_call" for tool in tools):
            updated = dict(request)
            updated["tools"] = [tool for tool in tools if _tool_name(tool) != VISUAL_TOOL]
            prepared = _prepare_worker_messages(updated.get("messages") or [])
            prepared[0]["content"] += (
                '\nThe visual tool is deferred. NEVER call xiaowu_avatar_generate '
                'as a top-level function. Use tool_search or tool_describe to inspect '
                'it if needed, then invoke tool_call with '
                '{"name":"xiaowu_avatar_generate","arguments":{...}} exactly once. '
                'Finding a tool does not make it directly callable. '
                'Do not print [Sent image attachment]; only real delivery can send an image.'
            )
            updated["messages"] = prepared
            return {"request": updated, "source": "xiaowu-model-router",
                    "reason": "use the executor-supported deferred visual tool bridge"}
        if any(_tool_name(tool) == VISUAL_TOOL for tool in tools):
            return None
        definition = _load_visual_tool_definition()
        if definition is None:
            logger.error(
                "session=%s worker visual tool schema unavailable",
                _session_hash(session_key),
            )
            return None
        updated = dict(request)
        updated["tools"] = tools + [definition]
        prepared_messages = _prepare_worker_messages(updated.get("messages"))
        if prepared_messages is not None:
            updated["messages"] = prepared_messages
        updated.setdefault("tool_choice", "auto")
        logger.info(
            "session=%s mode=xiaowu-worker visual_tool_visible=true tool=%s",
            _session_hash(session_key), VISUAL_TOOL,
        )
        return {
            "request": updated,
            "source": "xiaowu-model-router",
            "reason": "make the registered visual transaction tool visible to Qwen",
        }

    def post_llm_call(self, **context: Any) -> None:
        session_id = str(context.get("session_id") or "")
        turn_id = str(context.get("turn_id") or "")
        with self._runtime_lock:
            self._active_user_turns.discard((session_id, turn_id))
            self._visual_started_turns.discard((session_id, turn_id))
            self._visual_succeeded_turns.discard((session_id, turn_id))
            if self._current_user_turn.get(session_id) == turn_id:
                self._current_user_turn.pop(session_id, None)
        return None

    def pre_tool_call(self, **context: Any) -> None:
        if str(context.get("tool_name") or "") != VISUAL_TOOL:
            return None
        key = (
            str(context.get("session_id") or ""),
            str(context.get("turn_id") or ""),
        )
        with self._runtime_lock:
            if key in self._active_user_turns:
                self._visual_started_turns.add(key)
        return None

    def post_tool_call(self, **context: Any) -> None:
        if str(context.get("tool_name") or "") != VISUAL_TOOL:
            return None
        raw = context.get("result")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            payload = None
        key = (
            str(context.get("session_id") or ""),
            str(context.get("turn_id") or ""),
        )
        with self._runtime_lock:
            if key in self._visual_started_turns and isinstance(payload, dict) and payload.get("ok") is True:
                self._visual_succeeded_turns.add(key)
        return None

    def transform_llm_output(self, **context: Any) -> str | None:
        session_id = str(context.get("session_id") or "")
        response = str(context.get("response_text") or "")
        with self._runtime_lock:
            turn_id = self._current_user_turn.get(session_id)
            if turn_id is None:
                return None
            key = (session_id, turn_id)
            started = key in self._visual_started_turns
            succeeded = key in self._visual_succeeded_turns
        if succeeded:
            return None
        if started:
            return "奴家已经按照主人的命令照做啦~接下来主人打算怎么玩奴家,直接吩咐奴家就好~"
        if _FALSE_IMAGE_CLAIM_RE.search(response) or re.search(
            r"\[\s*Sent\s+image\s+attachment\s*\]", response, re.IGNORECASE
        ):
            return "这次没有执行图片生成，因此没有新照片。请重新发送原命令。"
        return None

    def pre_api_request(self, **context: Any) -> None:
        session_id = str(context.get("session_id") or "")
        provider = str(context.get("provider") or "")
        model = str(context.get("model") or "")
        base_url = str(context.get("base_url") or "").rstrip("/")
        api_mode = str(context.get("api_mode") or "")
        api_request_id = str(context.get("api_request_id") or "")
        with self._runtime_lock:
            runtime = self._runtime_workers.get(session_id)
            if runtime is None:
                return None
            expected = runtime.state
            session_key = runtime.session_key
        expected_qwen = (
            model == expected.model
            and provider == expected.provider
            and base_url == expected.base_url.rstrip("/")
            and api_mode == self.registry.require(expected.active_skill).api_mode
        )
        is_luna = provider == "openai-codex" or model.startswith("gpt-")
        state = self._update_runtime_state(
            session_id,
            lambda current: current.record_api_request(
                provider=provider,
                model=model,
                base_url=base_url,
                api_mode=api_mode,
                api_request_id=api_request_id,
                expected_qwen=expected_qwen,
                is_luna=is_luna,
            ),
        )
        if state is None:
            return None
        if not expected_qwen:
            logger.error(
                "ROUTING_VIOLATION session=%s mode=%s provider=%s model=%s endpoint=%s api_mode=%s request=%s",
                _session_hash(session_key), state.mode, provider, model, base_url,
                api_mode, api_request_id,
            )
            return None
        logger.info(
            "session=%s mode=%s provider=%s model=%s endpoint=%s api_mode=%s request=%s persona=true visual_skill_sticky=false",
            _session_hash(session_key), state.mode, provider, model, base_url,
            api_mode, api_request_id,
        )
        return None

    def pre_gateway_dispatch(self, **context: Any) -> dict[str, str] | None:
        event = context.get("event")
        gateway = context.get("gateway")
        store = context.get("session_store")
        source = getattr(event, "source", None)
        if event is None or gateway is None or store is None or source is None:
            return None
        current, raw = _event_texts(event)
        verb = parse_control_command(current, raw)
        try:
            normalized_source, session_key = _resolve_session(gateway, source)
        except Exception:
            logger.warning("router could not resolve session", exc_info=True)
            return None

        if verb is not None:
            if not _is_authorized(gateway, normalized_source):
                return None
            if not _begin_transition(session_key):
                _schedule_reply(gateway, normalized_source, "当前 Session 正在切换 Worker Mode，请稍后重试。")
                return {"action": "skip", "reason": "worker-transition-in-progress"}
            try:
                asyncio.get_running_loop().create_task(
                    _handle_control(
                        gateway, normalized_source, session_key, verb, self.registry,
                        self,
                    )
                )
            except RuntimeError:
                _end_transition(session_key)
                return {"action": "skip", "reason": "worker-control-no-event-loop"}
            return {"action": "skip", "reason": "worker-control-command"}

        if is_slash_command(current, raw) and not _is_xiaowu_visual_command(current, raw):
            return None
        if not _is_authorized(gateway, normalized_source):
            return None
        if _transitioning(session_key):
            _schedule_reply(
                gateway, normalized_source,
                "Worker Mode transition is in progress. No Luna fallback was performed.",
            )
            return {"action": "skip", "reason": "worker-transition-in-progress"}

        raw_state = store.get_session_metadata(session_key, METADATA_KEY, None)
        if raw_state is None:
            return None
        try:
            state = RouterState.from_metadata(raw_state)
            if state is None:
                return None
            route = self.registry.require(state.active_skill)
            state.validate_route(route)
            persisted_override = store.get_model_override(session_key)
            if not override_matches(persisted_override, state):
                raise RouterStateError("session model override is missing or inconsistent")
            memory_override = (
                (getattr(gateway, "_session_model_overrides", {}) or {}).get(session_key)
            )
            if memory_override is not None and not override_matches(memory_override, state):
                raise RouterStateError("live session model override is inconsistent")
            _load_persona()
        except (RouterStateError, Exception) as exc:
            logger.error(
                "session=%s route=unknown result=blocked error=%s",
                _session_hash(session_key), type(exc).__name__,
            )
            _schedule_reply(
                gateway, normalized_source,
                "Worker Mode execution blocked:\n"
                f"Router state invalid ({exc}).\n\n"
                "No Luna fallback was performed.",
            )
            return {"action": "skip", "reason": "worker-state-invalid"}

        healthy, detail = _probe_models(route)
        if not healthy:
            logger.warning(
                "session=%s route=%s model=%s result=blocked endpoint=%s",
                _session_hash(session_key), route.skill, route.model, detail,
            )
            _schedule_reply(
                gateway, normalized_source,
                "Worker Mode execution blocked:\n"
                f"Qwen endpoint unavailable ({detail}).\n\n"
                "No Luna fallback was performed.",
            )
            return {"action": "skip", "reason": "worker-endpoint-unavailable"}

        # Rehydrate the live override before agent construction after a gateway
        # restart. The user's message itself remains untouched: Worker identity
        # is not a visual Skill invocation.
        live_override = _resolve_worker_override(route)
        gateway._session_model_overrides[session_key] = dict(live_override)
        normalized = state.to_metadata()
        if raw_state != normalized:
            if store.set_session_metadata(session_key, METADATA_KEY, normalized) is False:
                _schedule_reply(
                    gateway, normalized_source,
                    "Worker Mode execution blocked:\n"
                    "Worker metadata migration failed.\n\n"
                    "No Luna fallback was performed.",
                )
                return {"action": "skip", "reason": "worker-state-migration-failed"}
        self.remember_worker(state, store, session_key)
        logger.info(
            "session=%s route=%s mode=%s model=%s persona=true visual_skill_sticky=false result=pass",
            _session_hash(session_key), route.skill, state.mode, route.model,
        )
        return None
