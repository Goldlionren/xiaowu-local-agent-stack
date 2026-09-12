"""Hermes tool adapter for deterministic Xiaowu image generation."""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path


SKILL_ROOT = Path(
    os.environ.get(
        "XIAOWU_AVATAR_ROOT",
        str(Path.home() / ".hermes" / "skills" / "roleplay" / "xiaowu-avatar"),
    )
).expanduser().resolve()
LIB_DIR = SKILL_ROOT / "lib"
_TURN_TTL_SECONDS = 900
_TURN_LOCK = threading.Lock()
_ACTIVE_TURNS: dict[str, dict] = {}


logger = logging.getLogger(__name__)

_MCP_COMMAND_RE = re.compile(
    r"^/(?:xiaowu-avatar|xiaowu_avatar)(?:@[A-Za-z0-9_]+)?\s+"
    r"mcp(?:\s+(\S+))?\s*$",
    re.IGNORECASE,
)
_WARDROBE_COMMAND_RE = re.compile(
    r"^/(?:xiaowu-avatar|xiaowu_avatar)(?:@[A-Za-z0-9_]+)?\s+"
    r"穿着(?:\s+(设置)(?:\s+(.+))?)?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _is_xiaowu_skill_turn(message: object) -> bool:
    text = str(message or "")
    return (
        '[IMPORTANT: The user has invoked the "xiaowu-avatar" skill' in text
        or '[Loaded as part of the stacked skill invocation "xiaowu-avatar"' in text
    )


def _is_xiaowu_command(text: object) -> bool:
    first = str(text or "").lstrip().split(maxsplit=1)[0].lower()
    first = first.split("@", 1)[0]
    return first in {"/xiaowu-avatar", "/xiaowu_avatar"}


def _parse_management_command(*texts: object) -> dict[str, str] | None:
    for value in texts:
        text = str(value or "").strip()
        mcp_match = _MCP_COMMAND_RE.fullmatch(text)
        if mcp_match:
            target = str(mcp_match.group(1) or "").strip()
            return {
                "kind": "mcp",
                "operation": "set" if target else "show",
                "value": target,
            }
        wardrobe_match = _WARDROBE_COMMAND_RE.fullmatch(text)
        if wardrobe_match:
            setting = bool(wardrobe_match.group(1))
            return {
                "kind": "wardrobe",
                "operation": "set" if setting else "show",
                "value": str(wardrobe_match.group(2) or "").strip(),
            }
    return None


def _avatarctl_core():
    if str(LIB_DIR) not in sys.path:
        sys.path.insert(0, str(LIB_DIR))
    return importlib.import_module("avatarctl")


def _format_mcp_target(result: dict) -> str:
    current = str(result.get("default_target", "")).removeprefix("comfy_")
    lines = [f"小舞图片 MCP 当前选择：{current}", "", "节点状态："]
    readiness_labels = {
        "verified": "已验证",
        "runtime_preflight": "运行前预检",
        "unavailable": "不可用",
    }
    for option in result.get("options", []):
        marker = "→" if option.get("selected") else " "
        readiness = readiness_labels.get(option.get("readiness"), "未知")
        selectable = "可选择" if option.get("selectable") else "不可选择"
        lines.append(
            f"{marker} {option.get('label')}（{readiness}，{selectable}）"
        )
    if result.get("updated"):
        previous = str(result.get("previous_target", "")).removeprefix("comfy_")
        lines.insert(1, f"已从 {previous} 切换并保存；只影响之后的新图片事务。")
    else:
        lines.extend(
            [
                "",
                "切换格式：/xiaowu-avatar mcp <节点>",
                "切换不会改变或重提已有图片事务。",
            ]
        )
    return "\n".join(lines)


def _format_wardrobe(result: dict) -> str:
    snapshot = {
        "revision": int(result.get("revision", 0)),
        "clothing": result.get("clothing", {}),
    }
    if "changed" not in result:
        lead = "小舞当前结构化穿着状态："
    elif result.get("changed"):
        lead = "小舞穿着状态已保存；本命令没有生成图片。"
    else:
        lead = "设置内容与当前穿着一致；状态未改变，也没有生成图片。"
    return lead + "\n" + json.dumps(snapshot, ensure_ascii=False, indent=2)


def _run_management_command(command: dict[str, str]) -> str:
    core = _avatarctl_core()
    config = core.load_config()
    if command["kind"] == "mcp":
        target = command["value"] if command["operation"] == "set" else ""
        return _format_mcp_target(core.cmd_mcp_target(config, target))
    if command["operation"] == "show":
        return _format_wardrobe(core.cmd_wardrobe_status(config))
    if not command["value"]:
        raise ValueError("穿着设置后必须提供 JSON 对象")
    try:
        changes = json.loads(command["value"])
    except json.JSONDecodeError as exc:
        raise ValueError(f"穿着设置 JSON 无效：{exc}") from exc
    return _format_wardrobe(core.cmd_wardrobe_update(config, changes))


async def _send_management_reply(
    gateway: object, source: object, message: str
) -> None:
    try:
        adapter = gateway._adapter_for_source(source)
        if adapter is None:
            logger.warning("avatar management reply has no adapter")
            return
        metadata_fn = getattr(gateway, "_thread_metadata_for_source", None)
        metadata = metadata_fn(source) if callable(metadata_fn) else None
        try:
            await adapter.send(str(source.chat_id), message, metadata=metadata)
        except TypeError:
            await adapter.send(str(source.chat_id), message)
    except Exception:
        logger.warning("avatar management reply failed", exc_info=True)


async def _handle_management_command(
    gateway: object,
    source: object,
    session_key: str,
    command: dict[str, str],
) -> None:
    try:
        running = getattr(gateway, "_is_session_running", None)
        is_mutation = command["operation"] == "set"
        if is_mutation and callable(running) and running(session_key):
            message = "当前 Session 仍有任务在运行；请等待完成后再修改小舞状态。"
        else:
            message = await asyncio.to_thread(_run_management_command, command)
    except Exception as exc:
        logger.warning(
            "avatar management command failed: %s", exc, exc_info=True
        )
        message = f"小舞控制命令失败：{exc}"
    await _send_management_reply(gateway, source, message)


def _on_pre_gateway_dispatch(**context):
    event = context.get("event")
    source = getattr(event, "source", None)
    platform = getattr(getattr(source, "platform", None), "value", None)
    if platform != "telegram":
        return None
    current = getattr(event, "text", "")
    raw_message = getattr(event, "raw_message", None)
    raw = getattr(raw_message, "text", "") or getattr(raw_message, "caption", "")
    command = _parse_management_command(current, raw)
    if command is not None:
        gateway = context.get("gateway")
        authorized = getattr(gateway, "_is_user_authorized", None)
        if gateway is None or not callable(authorized):
            return None
        try:
            if authorized(source) is not True:
                return None
        except Exception:
            logger.warning(
                "avatar management authorization check failed", exc_info=True
            )
            return None

        normalize = getattr(gateway, "_normalize_source_for_session_key", None)
        normalized_source = normalize(source) if callable(normalize) else source
        try:
            session_key = str(
                gateway._session_key_for_source(normalized_source)
            )
            asyncio.get_running_loop().create_task(
                _handle_management_command(
                    gateway, normalized_source, session_key, command
                )
            )
        except Exception:
            logger.warning(
                "could not schedule avatar management command", exc_info=True
            )
        return {
            "action": "skip",
            "reason": f"xiaowu-{command['kind']}-management-command",
        }
    if _is_xiaowu_command(raw) and not _is_xiaowu_command(current):
        return {"action": "rewrite", "text": str(raw)}
    return None


def _prune_turns(now: float) -> None:
    stale = [
        key for key, value in _ACTIVE_TURNS.items()
        if now - float(value.get("created", 0)) > _TURN_TTL_SECONDS
    ]
    for key in stale:
        _ACTIVE_TURNS.pop(key, None)


def _on_pre_llm_call(**context):
    session_id = str(context.get("session_id") or "")
    if not session_id:
        return None
    now = time.monotonic()
    with _TURN_LOCK:
        _prune_turns(now)
        if not _is_xiaowu_skill_turn(context.get("user_message")):
            _ACTIVE_TURNS.pop(session_id, None)
            return None
        _ACTIVE_TURNS[session_id] = {
            "created": now,
            "task_id": str(context.get("task_id") or ""),
            "turn_id": str(context.get("turn_id") or ""),
            "called": False,
            "ok": None,
            "pending": False,
            "delivery_pending": False,
            "result_image": "",
            "error": "",
        }
    return {
        "context": (
            "Runtime boundary: xiaowu-avatar context is available, but that alone "
            "does not make image generation mandatory. If the current user request "
            "needs a new image, call xiaowu_avatar_generate exactly once. Otherwise "
            "answer normally. Never claim that a new image exists or output a new "
            "image URL unless the generation tool was actually called successfully. "
            "If xiaowu_avatar_generate returns terminal=true or retryable=false, "
            "the visual request is closed for this turn: do not call any more tools; "
            "return a final response to the user immediately. If generation succeeds "
            "but delivery_status is pending_delivery, generation succeeded: never "
            "regenerate; only retry delivery of the same existing result image."
        )
    }


def _matching_turn(context: dict) -> dict | None:
    session_id = str(context.get("session_id") or "")
    turn_id = str(context.get("turn_id") or "")
    state = _ACTIVE_TURNS.get(session_id)
    if state is None:
        return None
    expected_turn = str(state.get("turn_id") or "")
    if expected_turn and turn_id and expected_turn != turn_id:
        return None
    return state


def _on_pre_tool_call(**context):
    with _TURN_LOCK:
        state = _matching_turn(context)
        tool_name = str(context.get("tool_name") or "")
        if state is None:
            if tool_name != "xiaowu_avatar_generate":
                return None
            session_id = str(context.get("session_id") or "")
            if not session_id:
                return None
            state = {
                "created": time.monotonic(),
                "task_id": str(context.get("task_id") or ""),
                "turn_id": str(context.get("turn_id") or ""),
                "called": False,
                "ok": None,
                "error": "",
            }
            _ACTIVE_TURNS[session_id] = state
        # Skill discovery and unrelated tools remain available until an actual
        # image transaction begins. A marker is context, not a visual mandate.
        if tool_name != "xiaowu_avatar_generate":
            if not state.get("called"):
                return None
            return {
                "action": "block",
                "message": (
                    "VISUAL_TRANSACTION_TERMINAL: 本轮视觉事务已经开始并禁止切换工具。"
                    "不要调用任何其他工具，不要修复或重试；现在直接向用户返回最终结果。"
                ),
            }
        if state.get("called"):
            return {
                "action": "block",
                "message": (
                    "VISUAL_TRANSACTION_TERMINAL: 本轮已经使用唯一一次 "
                    "xiaowu_avatar_generate 权限。禁止重复生成或调用其他工具；"
                    "现在直接向用户返回最终结果。"
                ),
            }
        state["called"] = True
    return None


def _on_post_tool_call(**context):
    if str(context.get("tool_name") or "") != "xiaowu_avatar_generate":
        return None
    raw = context.get("result")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        payload = None
    with _TURN_LOCK:
        state = _matching_turn(context)
        if state is not None:
            state["ok"] = bool(isinstance(payload, dict) and payload.get("ok") is True)
            state["pending"] = bool(isinstance(payload, dict) and payload.get("generation_status") == "pending_completion")
            state["delivery_pending"] = bool(isinstance(payload, dict) and payload.get("delivery_status") == "pending_delivery")
            state["result_image"] = str(payload.get("result_image") or "") if isinstance(payload, dict) else ""
            if isinstance(payload, dict):
                state["error"] = str(payload.get("failure_reason") or payload.get("error") or "")[:300]
    return None


# identity-studio:v0.1.7 everyone-avatar api runtime
# identity-studio:v0.1.8 clean-state long-job runtime
# identity-studio:v0.1.8.2 telegram delivery compatibility runtime


def _terminal_failure_result(reason: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "terminal": True,
            "retryable": False,
            "generation_status": "terminal_failure",
            "failure_reason": str(reason or "visual transaction failed")[:1000],
            "instruction": (
                "This visual request is closed for this turn. Do not call "
                "xiaowu_avatar_generate again and do not call another tool to repair "
                "or retry it. Return a final failure response to the user now."
            ),
        },
        ensure_ascii=False,
    )



def _delivery_pending_result(result_image: str) -> str:
    return json.dumps(
        {
            "ok": True,
            "terminal": True,
            "retryable": False,
            "generation_status": "completed",
            "delivery_status": "pending_delivery",
            "result_image": result_image,
            "instruction": (
                "Image generation succeeded and the existing result_image is durable, "
                "but Telegram delivery failed. Do not call xiaowu_avatar_generate again "
                "and do not submit ComfyUI again. Tell the user generation succeeded; "
                "delivery may be retried using the same existing image only."
            ),
        },
        ensure_ascii=False,
    )

def _transform_tool_result(**context):
    session_id = str(context.get("session_id") or "")
    tool_name = str(context.get("tool_name") or "")
    with _TURN_LOCK:
        state = _ACTIVE_TURNS.get(session_id)
        if state is None or not state.get("called"):
            return None
        expected_turn = str(state.get("turn_id") or "")
        actual_turn = str(context.get("turn_id") or "")
        if expected_turn and actual_turn and expected_turn != actual_turn:
            return None
        ok = state.get("ok")
        delivery_pending = bool(state.get("delivery_pending"))
        result_image = str(state.get("result_image") or "")
        reason = str(state.get("error") or "")

    if tool_name == "xiaowu_avatar_generate":
        if ok is True:
            if delivery_pending:
                return _delivery_pending_result(result_image)
            return None
        raw = context.get("result")
        if not reason:
            try:
                payload = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                payload = None
            if isinstance(payload, dict):
                reason = str(payload.get("failure_reason") or payload.get("error") or "")
        return _terminal_failure_result(reason)

    # Any tool attempted after the single visual transaction is terminally blocked.
    return _terminal_failure_result(
        reason or f"additional tool {tool_name!r} attempted after visual transaction"
    )


def _transform_llm_output(**context):
    session_id = str(context.get("session_id") or "")
    with _TURN_LOCK:
        state = _ACTIVE_TURNS.get(session_id)
        if state is None:
            return None
        called = bool(state.get("called"))
        ok = state.get("ok")
        pending = bool(state.get("pending"))
        delivery_pending = bool(state.get("delivery_pending"))
    if not called:
        response = str(context.get("response_text") or "")
        false_image_claim = re.search(
            r"(?:file://\S+?\.(?:png|jpe?g|webp|gif)|https?://\S+?\.(?:png|jpe?g|webp|gif)(?:\?\S*)?|MEDIA:/\S+|(?:图片|照片|头像).{0,10}(?:已生成|生成好了|已发送|发给你)|(?:已生成|生成好了).{0,10}(?:图片|照片|头像))",
            response,
            re.IGNORECASE,
        )
        if false_image_claim:
            return "这次没有执行图片生成，因此没有新照片。请重新发送原命令。"
        return None
    if ok is True and pending:
        return "图片任务已经成功提交给 ComfyUI，目前仍在生成中。本轮不会重复提交；后续只会继续查询同一个 prompt_id。"
    if ok is True and delivery_pending:
        return "图片已经生成成功并保存在本地，但 Telegram 投递失败。本轮不会重新生成；应重试发送同一张已生成图片。"
    if ok is True:
        return "图片已由生成工具确认生成并发送。"
    return "这次图片生成没有成功，也没有发送新照片。本轮不会自动重试。"


def _on_post_llm_call(**context):
    session_id = str(context.get("session_id") or "")
    if session_id:
        with _TURN_LOCK:
            _ACTIVE_TURNS.pop(session_id, None)
    return None


def _executor():
    if str(LIB_DIR) not in sys.path:
        sys.path.insert(0, str(LIB_DIR))
    return importlib.import_module("mcp_executor")


def _handle_generate(args: dict, **_kwargs) -> str:
    try:
        from tools.registry import registry

        result = _executor().generate(
            args, lambda name, values: registry.dispatch(name, values)
        )
        transaction = result.get("transaction", {})
        return json.dumps(
            {
                "ok": True,
                "transaction_id": transaction.get("transaction_id", ""),
                "prompt_id": transaction.get("prompt_id", ""),
                "workflow_id": transaction.get("workflow_id", ""),
                "target": transaction.get("target", ""),
                "generation_status": transaction.get("generation_status", "completed"),
                "pending": bool(result.get("pending") or transaction.get("generation_status") == "pending_completion"),
                "retryable": False if (result.get("pending") or transaction.get("generation_status") == "pending_completion") else None,
                "delivery_status": transaction.get("delivery_status", ""),
                "result_image": transaction.get("result_image", ""),
                "agent_action": "stop_silently",
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "terminal": True,
                "retryable": False,
                "generation_status": "terminal_failure",
                "failure_reason": str(exc),
                "instruction": (
                    "This visual request is closed for this turn. Do not retry or "
                    "call another tool. Return a final failure response now."
                ),
            },
            ensure_ascii=False,
        )


VISUAL_PROPERTIES = {
    key: {"type": "string"}
    for key in (
        "outfit", "outerwear", "top", "bottom", "dress", "legwear",
        "footwear", "headwear", "accessories", "hair", "makeup",
        "expression", "pose", "action", "scene", "lighting", "camera",
    )
}

GENERATE_SCHEMA = {
    "name": "xiaowu_avatar_generate",
    "description": (
        "Generate and deliver one Yinyue avatar image as a single deterministic "
        "transaction. Pass the user's original request as intent and only explicit "
        "visual state changes in visual. This tool owns all MCP steps; never call "
        "avatarctl/MCP manually before or after it. For ordinary photos OMIT workflow "
        "and target: the runtime selects the workflow and uses the saved MCP target. "
        "Never invent workflow IDs. Pass intent verbatim."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "intent": {"type": "string"},
            "visual": {
                "type": "object",
                "properties": VISUAL_PROPERTIES,
                "additionalProperties": False,
            },
            "say": {"type": "string"},
            "channel": {"type": "string"},
            "workflow": {
                "type": "string",
                "description": "Optional registered workflow ID. OMIT for ordinary photos; runtime routes automatically. Do not invent names.",
            },
            "target": {"type": "string"},
            "aspect_ratio": {"type": "string"},
            "megapixels": {"type": "number"},
            "width": {"type": "integer"},
            "height": {"type": "integer"},
            "no_send": {"type": "boolean", "default": False},
        },
        "required": ["intent"],
        "additionalProperties": False,
    },
}


def _generate_schema() -> dict:
    schema = json.loads(json.dumps(GENERATE_SCHEMA))
    core = _avatarctl_core()
    registry = core.workflow_registry(core.load_config())
    schema["parameters"]["properties"]["workflow"]["enum"] = [
        "", *sorted(registry["workflows"])
    ]
    return schema


def register(ctx) -> None:
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("transform_tool_result", _transform_tool_result)
    ctx.register_hook("transform_llm_output", _transform_llm_output)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
    ctx.register_tool(
        name="xiaowu_avatar_generate",
        toolset="xiaowu-avatar",
        schema=_generate_schema(),
        handler=_handle_generate,
        emoji="🌙",
    )
