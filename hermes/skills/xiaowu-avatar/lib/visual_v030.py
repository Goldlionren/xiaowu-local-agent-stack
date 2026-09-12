from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


class VisualSystemError(ValueError):
    pass


EDIT_MARKERS = (
    "脱掉", "脱了", "去掉", "移除", "换成", "改成", "改为", "换发型", "换鞋",
    "换帽", "换个", "换一身", "换衣", "戴上", "摘掉", "坐在", "站到", "躺在",
    "动作", "姿势", "表情", "手里的",
)
FULL_REGEN_MARKERS = (
    "完整", "整套", "全身", "新造型", "重新拍", "重新生成", "去海边",
    "换场景", "新场景", "重建",
)
SHOW_MARKERS = ("看看", "拍张", "照片", "现在的样子", "今天穿什么", "自拍")

VISUAL_PROMPT_FIELDS = (
    ("outfit", "服装"),
    ("outerwear", "外套"),
    ("top", "上装"),
    ("bottom", "下装"),
    ("dress", "连衣裙"),
    ("legwear", "腿部穿着"),
    ("footwear", "鞋子"),
    ("headwear", "头饰"),
    ("accessories", "配饰"),
    ("hair", "发型"),
    ("makeup", "妆容"),
    ("expression", "表情"),
    ("pose", "姿势"),
    ("action", "动作"),
    ("scene", "场景"),
    ("lighting", "光线"),
    ("camera", "镜头"),
)

CLOTHING_FIELDS = {
    "outfit", "outerwear", "top", "bottom", "dress", "legwear",
    "footwear", "headwear", "accessories",
}
POSE_FIELDS = {"pose", "action"}

INTENT_FIELD_GROUPS = (
    (
        CLOTHING_FIELDS,
        (
            "衣服", "衣着", "穿着", "穿搭", "装扮", "换装", "换衣", "造型",
            "脱掉", "脱光", "脱了", "赤裸", "裸体", "裸身", "裸露", "不穿",
            "裙", "裤", "上衣", "外套", "大衣", "开衫", "鞋", "袜", "内衣",
            "胸罩", "帽", "围巾", "首饰", "配饰",
        ),
    ),
    (
        POSE_FIELDS,
        (
            "姿势", "动作", "摆出", "摆个", "坐下", "坐在", "站起", "站在",
            "躺下", "躺在", "跪", "蹲", "趴", "叉腰", "抬手", "举手", "伸手",
            "抱住", "靠在", "转身", "回头", "跳", "奔跑",
        ),
    ),
    (
        {"scene"},
        (
            "场景", "背景", "地点", "换地方", "去海边", "海边", "沙滩", "街头",
            "大街", "商业街", "卧室", "房间", "客厅", "浴室", "公园", "森林",
            "学校", "办公室", "酒吧", "餐厅", "室内", "室外",
        ),
    ),
    (
        {"hair"},
        ("发型", "头发", "长发", "短发", "卷发", "直发", "染发", "刘海", "马尾", "辫子"),
    ),
    ({"makeup"}, ("妆容", "化妆", "卸妆", "口红", "眼影", "素颜")),
    (
        {"expression"},
        ("表情", "微笑", "大笑", "哭", "生气", "害羞", "脸红", "眼神", "闭眼", "眨眼"),
    ),
    ({"lighting"}, ("光线", "灯光", "阳光", "月光", "逆光", "清晨", "黄昏", "夜晚", "夜景")),
    (
        {"camera"},
        ("镜头", "视角", "构图", "特写", "近景", "远景", "全身", "半身", "自拍", "俯拍", "仰拍", "POV", "pov"),
    ),
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_registry(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisualSystemError(f"workflow registry 无法读取：{exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise VisualSystemError("workflow registry schema_version 必须为 1")
    targets = value.get("targets")
    workflows = value.get("workflows")
    if not isinstance(targets, dict) or not targets:
        raise VisualSystemError("workflow registry.targets 必须为非空对象")
    if not isinstance(workflows, dict) or not workflows:
        raise VisualSystemError("workflow registry.workflows 必须为非空对象")
    required = {
        "id", "purpose", "description", "allowed_targets", "remote_paths",
        "capabilities", "input_roles", "output_role", "default_parameters",
        "parameter_bindings", "target_status",
    }
    for workflow_id, workflow in workflows.items():
        if not isinstance(workflow, dict):
            raise VisualSystemError(f"workflow {workflow_id} 必须为对象")
        missing = required - set(workflow)
        if missing:
            raise VisualSystemError(f"workflow {workflow_id} 缺少字段：{sorted(missing)}")
        if workflow.get("id") != workflow_id:
            raise VisualSystemError(f"workflow key/id 不一致：{workflow_id}")
        allowed = workflow.get("allowed_targets")
        if not isinstance(allowed, list) or not allowed:
            raise VisualSystemError(f"workflow {workflow_id}.allowed_targets 必须非空")
        for target in allowed:
            if target not in targets:
                raise VisualSystemError(f"workflow {workflow_id} 引用了未知 target：{target}")
            if target not in workflow["remote_paths"]:
                raise VisualSystemError(f"workflow {workflow_id} 缺少 {target} remote path")
            status = workflow["target_status"].get(target)
            if not isinstance(status, dict):
                raise VisualSystemError(f"workflow {workflow_id} 缺少 {target} status")
        for role, binding in workflow["parameter_bindings"].items():
            if not isinstance(binding, dict):
                raise VisualSystemError(
                    f"workflow {workflow_id} parameter binding {role} 必须为对象"
                )
            kind = binding.get("kind")
            if kind == "workflow_slot":
                addresses = [binding.get("address")]
            elif kind == "workflow_slots":
                addresses = binding.get("addresses")
                if not isinstance(addresses, list) or not addresses:
                    raise VisualSystemError(
                        f"workflow {workflow_id} parameter binding {role}.addresses 必须为非空数组"
                    )
            else:
                raise VisualSystemError(
                    f"workflow {workflow_id} parameter binding {role} kind 不受支持：{kind}"
                )
            if not all(isinstance(address, str) and address.strip() for address in addresses):
                raise VisualSystemError(
                    f"workflow {workflow_id} parameter binding {role} 包含无效 address"
                )
            if len(set(addresses)) != len(addresses):
                raise VisualSystemError(
                    f"workflow {workflow_id} parameter binding {role} 包含重复 address"
                )
    return value


def route_intent(intent: str, requested_workflow: str = "") -> tuple[str, str]:
    text = (intent or "").strip()
    if requested_workflow:
        return requested_workflow, "explicit_workflow"
    full = any(marker in text for marker in FULL_REGEN_MARKERS)
    edit = any(marker in text for marker in EDIT_MARKERS)
    if full:
        return "yinyue_cosplay01", "full_regeneration"
    if edit:
        return "yinyue_cosplay01", "full_regeneration"
    if any(marker in text for marker in SHOW_MARKERS) or not text:
        return "yinyue_cosplay01", "show_current"
    return "yinyue_cosplay01", "show_current"


def _first_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip("，。,. ")
    return ""


def transform_edit_prompt(intent: str) -> str:
    text = (intent or "").strip()
    if ("外套" in text or "大衣" in text or "开衫" in text) and any(
        marker in text for marker in ("脱", "去掉", "移除")
    ):
        return (
            "去掉图中人物的外套，其他一切保持不变。保留同一个人物身份、脸部、"
            "发型、内搭、腿部穿着、鞋子、背景、光线和整体构图。"
        )
    if "帽" in text:
        replacement = _first_match(
            [r"(?:换成|改成|改为|换为)([^，。]+帽[^，。]*)", r"(?:换成|改成|改为)([^，。]+)"],
            text,
        ) or "用户指定的帽子"
        return (
            f"将图中人物的帽子改为{replacement}，其他一切保持不变。保留人物身份、"
            "脸部、发型、服装主体、背景、光线和整体构图。"
        )
    if "发" in text:
        replacement = _first_match(
            [r"(?:换成|改成|改为|换为)([^，。]+发[^，。]*)", r"换([^，。]+发)"], text
        ) or text
        return (
            f"将图中人物的发型改为{replacement}，其他一切保持不变。保留人物身份、"
            "脸部、服装、背景、光线和整体构图。"
        )
    if any(marker in text for marker in ("动作", "姿势", "坐在", "站在", "躺在")):
        pose = _first_match(
            [r"(?:动作|姿势)?(?:改成|改为)([^，。]+)", r"((?:坐|站|躺)在[^，。]+)"], text
        ) or text
        return (
            f"将图中人物动作修改为{pose}，其他一切保持不变。保留人物身份、脸部、"
            "发型、服装、背景、光线和整体风格。"
        )
    if any(marker in text for marker in ("去掉", "移除")):
        obj = _first_match([r"(?:去掉|移除)(?:图中|手里|手中的)?([^，。]+)"], text) or "指定物体"
        return (
            f"去掉图中人物的{obj}，其他一切保持不变。保留人物身份、脸部、发型、"
            "服装、背景、光线和整体构图。"
        )
    return (
        f"按以下要求局部编辑图中人物：{text}。其他一切保持不变。保留同一个人物身份、"
        "脸部、未被点名的服装、背景、光线和整体构图。"
    )


def infer_intent_visual_fields(intent: str) -> set[str]:
    text = intent or ""
    result: set[str] = set()
    for fields, markers in INTENT_FIELD_GROUPS:
        if any(marker in text for marker in markers):
            result.update(fields)
    return result


def visual_prompt_facts(
    state: dict[str, Any], *, include_fields: set[str] | None = None
) -> str:
    visual = state["visual"]
    return "；".join(
        f"{label}为{visual.get(field, '')}"
        for field, label in VISUAL_PROMPT_FIELDS
        if (include_fields is None or field in include_fields)
        and str(visual.get(field, "")).strip()
    )


def serialize_visual_prompt(state: dict[str, Any]) -> str:
    return f"小舞当前视觉状态：{visual_prompt_facts(state)}。"


def build_generation_prompt(
    state: dict[str, Any],
    *,
    intent: str,
    intent_class: str,
    changed_fields: set[str] | None = None,
) -> str:
    """Place the latest request first and retain only non-conflicting old facts."""
    explicit_changes = set(changed_fields or set())
    current = serialize_visual_prompt(state)
    if intent_class == "show_current" and not explicit_changes:
        return current
    request = (intent or "").strip()
    if not request:
        return current
    overridden = infer_intent_visual_fields(request) | explicit_changes
    all_fields = {field for field, _label in VISUAL_PROMPT_FIELDS}
    preserved = visual_prompt_facts(state, include_fields=all_fields - overridden)
    parsed = visual_prompt_facts(state, include_fields=explicit_changes)
    sections = [f"【最高优先级：本次最新要求】{request}。"]
    if parsed:
        sections.append(f"【已解析的新状态】{parsed}。")
    if preserved:
        sections.append(f"【仅保留未被本次要求修改的信息】{preserved}。")
    if overridden:
        labels = "、".join(
            label for field, label in VISUAL_PROMPT_FIELDS if field in overridden
        )
        sections.append(
            f"本次要求已覆盖旧的{labels}信息；不得恢复或参考这些字段的旧值。"
        )
    sections.append("任何新旧冲突都必须以第一段最新要求为准。")
    return "".join(sections)


def parse_resolution(
    text: str,
    *,
    aspect_ratio: str = "",
    megapixels: float | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    source = text or ""
    size_match = re.search(r"(?<!\d)(\d{3,5})\s*[x×X]\s*(\d{3,5})(?!\d)", source)
    if width is not None or height is not None:
        if not width or not height:
            raise VisualSystemError("width 和 height 必须一起提供")
        result.update(width=int(width), height=int(height))
    elif size_match:
        result.update(width=int(size_match.group(1)), height=int(size_match.group(2)))
    ratio = aspect_ratio.strip()
    if not ratio:
        ratio_match = re.search(r"(?<!\d)(\d{1,2})\s*:\s*(\d{1,2})(?!\d)", source)
        if ratio_match:
            ratio = f"{int(ratio_match.group(1))}:{int(ratio_match.group(2))}"
        elif "竖版" in source or "竖图" in source:
            ratio = "3:4"
    if ratio:
        result["aspect_ratio"] = ratio
    mp = megapixels
    if mp is None:
        mp_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:MP|mp|兆像素)", source)
        if mp_match:
            mp = float(mp_match.group(1))
    if mp is not None:
        if mp <= 0 or mp > 64:
            raise VisualSystemError("megapixels 必须在 0 到 64 之间")
        result["megapixels"] = int(mp) if float(mp).is_integer() else float(mp)
    return result


def select_target(
    registry: dict[str, Any],
    workflow: dict[str, Any],
    *,
    requested_target: str,
    default_target: str,
) -> str:
    allowed = list(workflow["allowed_targets"])
    candidates: list[str] = []
    if requested_target:
        if requested_target not in registry["targets"]:
            raise VisualSystemError(f"未知 MCP target：{requested_target}")
        if requested_target not in allowed:
            raise VisualSystemError(
                f"workflow {workflow['id']} 不允许 target {requested_target}"
            )
        candidates.append(requested_target)
    else:
        preferred = workflow.get("preferred_target", "")
        candidates.extend([default_target, preferred, *allowed])
    seen: set[str] = set()
    reasons: list[str] = []
    for target in candidates:
        if not target or target in seen or target not in allowed:
            continue
        seen.add(target)
        target_cfg = registry["targets"].get(target, {})
        status = workflow["target_status"].get(target, {})
        ready = bool(target_cfg.get("enabled", True)) and bool(status.get("workflow_exists"))
        ready = ready and bool(status.get("interface_verified"))
        path = workflow["remote_paths"].get(target, "")
        if ready and path:
            return target
        reasons.append(f"{target}: {status.get('reason', 'not verified')}")
    raise VisualSystemError(
        f"workflow {workflow['id']} 没有已验证可用的 MCP target；" + "；".join(reasons)
    )


def normalize_aspect_for_binding(value: str, binding: dict[str, Any]) -> str:
    mapping = binding.get("values", {})
    return str(mapping.get(value, value))


def build_parameters(
    workflow: dict[str, Any],
    *,
    prompt: str,
    resolution: dict[str, Any],
    source_image: str,
    seed: int | None = None,
) -> dict[str, Any]:
    parameters = copy.deepcopy(workflow.get("default_parameters", {}))
    parameters["prompt"] = prompt
    bindings = workflow.get("parameter_bindings", {})
    normalized_resolution = copy.deepcopy(resolution)
    if (
        "width" in normalized_resolution
        and "height" in normalized_resolution
        and "width" not in bindings
        and "height" not in bindings
        and "aspect_ratio" in bindings
        and "megapixels" in bindings
    ):
        width = int(normalized_resolution.pop("width"))
        height = int(normalized_resolution.pop("height"))
        divisor = math.gcd(width, height)
        ratio = f"{width // divisor}:{height // divisor}"
        supported = set(bindings["aspect_ratio"].get("values", {}))
        if supported and ratio not in supported:
            raise VisualSystemError(
                f"明确宽高对应比例 {ratio}，当前 Workflow 仅支持：{sorted(supported)}"
            )
        normalized_resolution["aspect_ratio"] = ratio
        normalized_resolution["megapixels"] = round(width * height / 1_000_000, 6)
    parameters.update(
        {key: value for key, value in normalized_resolution.items() if key in bindings}
    )
    if "seed" in bindings:
        if (
            not isinstance(seed, int)
            or isinstance(seed, bool)
            or not 10_000_000 <= seed <= 99_999_999
        ):
            raise VisualSystemError("seed 必须是 8 位整数")
        parameters["seed"] = seed
    if workflow.get("purpose") == "image_edit":
        if not source_image:
            raise VisualSystemError("image_edit 缺少可用 source image")
        parameters["input_image"] = source_image
    return parameters


def build_slot_overrides(workflow: dict[str, Any], parameters: dict[str, Any]) -> list[dict[str, Any]]:
    overrides: list[dict[str, Any]] = []
    bindings = workflow.get("parameter_bindings", {})
    for role, value in parameters.items():
        binding = bindings.get(role)
        if not binding:
            continue
        kind = binding.get("kind")
        if kind == "workflow_slot":
            addresses = [binding.get("address")]
        elif kind == "workflow_slots":
            addresses = binding.get("addresses")
        else:
            raise VisualSystemError(f"不支持的 parameter binding kind：{binding.get('kind')}")
        if not isinstance(addresses, list) or not addresses or not all(
            isinstance(address, str) and address.strip() for address in addresses
        ):
            raise VisualSystemError(f"parameter binding {role} 缺少有效 slot address")
        if role == "aspect_ratio":
            value = normalize_aspect_for_binding(str(value), binding)
        overrides.extend({"address": address, "value": value} for address in addresses)
    missing = [
        role for role, binding in bindings.items()
        if binding.get("required") and role not in parameters
    ]
    if missing:
        raise VisualSystemError(f"缺少 workflow 必需参数：{missing}")
    return overrides


def build_variant_slots(slot_overrides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert one transaction's exact values to a one-row vary_workflow zip."""
    if not slot_overrides:
        raise VisualSystemError("variant 至少需要一个 slot override")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in slot_overrides:
        address = item.get("address") if isinstance(item, dict) else None
        if not isinstance(address, str) or not address.strip() or "value" not in item:
            raise VisualSystemError("variant slot override 格式无效")
        if address in seen:
            raise VisualSystemError(f"variant slot address 重复：{address}")
        seen.add(address)
        result.append({"address": address, "values": [copy.deepcopy(item["value"])]})
    return result


def transaction_fingerprint(value: dict[str, Any]) -> str:
    selected = {
        "base_revision": value["base_revision"],
        "intent": value["intent"],
        "workflow_id": value["workflow_id"],
        "target": value["target"],
        "parameters": {
            key: item for key, item in value["parameters"].items() if key != "seed"
        },
        "patch": value.get("patch", {}),
        "transition": value.get("transition"),
    }
    return hashlib.sha256(canonical_json(selected).encode("utf-8")).hexdigest()


def declared_parameter_addresses(workflow: dict[str, Any]) -> set[str]:
    """Return only registry-declared business slots for the current MCP workflow."""
    result: set[str] = set()
    for binding in workflow.get("parameter_bindings", {}).values():
        if binding.get("kind") == "workflow_slot":
            addresses = [binding.get("address")]
        elif binding.get("kind") == "workflow_slots":
            addresses = binding.get("addresses", [])
        else:
            addresses = []
        result.update(address for address in addresses if isinstance(address, str) and address)
    return result


def build_mcp_plan(transaction: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    target = transaction["target"]
    workflow = registry["workflows"][transaction["workflow_id"]]
    target_cfg = registry["targets"][target]
    prefix = f"mcp__{target}__"
    prompt_id = transaction.get("prompt_id") or "$PROMPT_ID"
    original_path = transaction.get("original_workflow_path") or transaction.get("workflow_path", "")
    variant_path = transaction.get("variant_workflow_path") or "$VARIANT_WORKFLOW_PATH"
    status = transaction.get("status", "planned")
    steps: list[dict[str, Any]] = []
    allowed_addresses = declared_parameter_addresses(workflow)
    overrides = [
        copy.deepcopy(item) for item in transaction["slot_overrides"]
        if item.get("address") in allowed_addresses
    ]
    for item in overrides:
        if item["address"] == workflow.get("parameter_bindings", {}).get("input_image", {}).get("address"):
            item["value"] = "$UPLOADED_FILENAME"
    if status == "planned":
        steps.append({"step": "preflight", "tool": prefix + "server_info", "arguments": {}})
        if transaction.get("source_remote_image"):
            steps.append(
                {
                    "step": "upload_source",
                    "tool": prefix + "upload_file",
                    "arguments": {"paths": [transaction["source_remote_image"]], "overwrite": False},
                    "capture": "UPLOADED_FILENAME",
                }
            )
        steps.extend([
            {
                "step": "create_transaction_variant",
                "tool": prefix + "vary_workflow",
                "arguments": {
                    "workflow_path": original_path,
                    "slots": build_variant_slots(overrides),
                    "out_dir": transaction["variant_dir"],
                },
                "capture": "VARIANT_WORKFLOW_PATH",
                "original_must_remain_unchanged": True,
            },
            {
                "step": "inspect_transaction_variant",
                "tool": prefix + "list_workflow_slots",
                "arguments": {"workflow_path": "$VARIANT_WORKFLOW_PATH"},
                "capture": "OBSERVED_VARIANT_SLOTS_RESULT",
            },
            {
                "step": "verify_transaction_variant",
                "command": (
                    f"avatarctl verify-variant --transaction {transaction['transaction_id']} "
                    "--variant-workflow-path $VARIANT_WORKFLOW_PATH "
                    "--observed-slots-base64 $OBSERVED_VARIANT_SLOTS_B64"
                    + (
                        " --uploaded-filename $UPLOADED_FILENAME"
                        if transaction.get("source_remote_image") else ""
                    )
                ),
                "verification_addresses": sorted(
                    item["address"] for item in overrides
                ),
                "serialization": "filtered_json_utf8_base64",
                "fail_closed": True,
            },
        ])
    if status in {"planned", "variant_verified"}:
        steps.extend([
            {
                "step": "claim_single_submit",
                "command": f"avatarctl claim-submit --transaction {transaction['transaction_id']}",
                "fail_closed": True,
            },
            {
                "step": "submit_variant_once",
                "tool": prefix + "run_workflow",
                "arguments": {
                    "workflow_path": variant_path,
                    "wait": False,
                    "confirm_spend": False,
                },
                "capture": "PROMPT_ID",
                "never_repeat": True,
            },
            {
                "step": "bind_immediately",
                "command": f"avatarctl bind --transaction {transaction['transaction_id']} --prompt-id {prompt_id}",
            },
        ])
    if status in {"planned", "variant_verified", "submitted", "waiting", "fetch_pending"}:
        steps.extend([
            {
                "step": "wait_bounded",
                "tool": prefix + "job",
                "arguments": {"action": "wait", "prompt_id": prompt_id, "timeout_seconds": 20},
                "repeat_until_terminal": True,
                "deadline_seconds": transaction["deadline_seconds"],
                "never_submit_on_timeout": True,
            },
            {
                "step": "record_job_completed",
                "command": (
                    f"avatarctl mark-completed --transaction {transaction['transaction_id']} "
                    f"--prompt-id {prompt_id}"
                ),
                "only_after_terminal_completed": True,
            },
        ])
    if status in {
        "planned", "variant_verified", "submitted", "waiting", "fetch_pending",
        "generation_completed",
    }:
        steps.extend([
            {
                "step": "fetch_outputs",
                "tool": prefix + "fetch_outputs",
                "arguments": {
                    "prompt_id": prompt_id,
                    "out_dir": transaction["remote_output_dir"],
                    "url_only": False,
                    "inline_images": True,
                },
                "capture": ["LOCAL_MEDIA_PATH", "REMOTE_RESULT_IMAGE"],
                "repeat_on_transient_failure": True,
                "never_submit_on_failure": True,
                "require_media_linux_path": True,
            },
            {
                "step": "commit_and_deliver",
                "command": (
                    f"avatarctl commit --transaction {transaction['transaction_id']} "
                    f"--prompt-id {prompt_id} --result-image $LOCAL_MEDIA_PATH "
                    "--remote-result-image $REMOTE_RESULT_IMAGE"
                ),
            },
        ])
    if status == "submit_claimed":
        steps = []
    return {
        "protocol": "xiaowu-avatar-mcp/2",
        "transaction_id": transaction["transaction_id"],
        "target": target,
        "mcp_server": target_cfg["mcp_server"],
        "workflow_id": transaction["workflow_id"],
        "original_workflow_path": original_path,
        "variant_workflow_path": transaction.get("variant_workflow_path", ""),
        "variant_dir": transaction.get("variant_dir", ""),
        "submit_count": int(transaction.get("submit_count", 0)),
        "deadline_seconds": transaction["deadline_seconds"],
        "steps": steps,
        "interaction": {
            "intermediate_assistant_messages": "roleplay_only_max_2",
            "technical_progress": False,
            "success_after_commit": "stop_silently",
            "failure": "one concise actionable summary",
        },
        "on_error": {
            "before_submit_claim": "stop; planned/verified transaction may be aborted",
            "after_submit_claim": "stop and inspect the same transaction; never run again",
            "after_prompt_id": "query the bound job only; never submit or switch backend/target",
            "variant_mismatch": "stop before run_workflow; never modify or run the original workflow",
            "missing_local_media": "stop without commit; retry fetch_outputs for the same prompt_id only",
            "telegram_failure": "generation is committed; use avatarctl resend-last",
        },
        "agent_action": (
            "stop_no_retry" if status == "submit_claimed"
            else "stop_silently" if status in {
                "committed", "delivered", "pending_delivery", "aborted", "state_conflict"
            }
            else "execute_mcp_plan"
        ),
    }
