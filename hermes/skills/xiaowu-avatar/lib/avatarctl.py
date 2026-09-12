#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import fcntl
import hashlib
import json
import ntpath
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

SKILL_ROOT = Path(__file__).resolve().parents[1]
LIB_ROOT = Path(__file__).resolve().parent
if str(LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(LIB_ROOT))
from state_v024 import (  # noqa: E402
    RELATIONSHIP_ALLOWED,
    SCHEMA_VERSION,
    StateValidationError,
    apply_transition,
    migrate_state_file,
    migrate_v1_to_v2,
    reset_runtime_state,
    reset_scene,
    sync_legacy_view,
    validate_relationship,
    validate_state,
)
import memory_v024 as memory_store  # noqa: E402
from recent_history_v024 import meaningful_recent_history  # noqa: E402
import visual_v030 as visual_system  # noqa: E402

CONFIG_PATH = SKILL_ROOT / "config.json"
LOCAL_CONFIG_PATH = SKILL_ROOT / "config.local.json"
WORKER_ACTION = "_worker"

WARDROBE_FIELDS = (
    "outfit",
    "outerwear",
    "top",
    "bottom",
    "dress",
    "legwear",
    "footwear",
    "headwear",
    "accessories",
)
WARDROBE_FIELD_ALIASES = {
    "服装": "outfit",
    "整套服装": "outfit",
    "外套": "outerwear",
    "上装": "top",
    "下装": "bottom",
    "连衣裙": "dress",
    "腿部穿着": "legwear",
    "袜子": "legwear",
    "鞋子": "footwear",
    "头饰": "headwear",
    "配饰": "accessories",
}


class AvatarError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config() -> dict[str, Any]:
    config = load_json(CONFIG_PATH)
    if LOCAL_CONFIG_PATH.is_file():
        config = deep_merge(config, load_json(LOCAL_CONFIG_PATH))
    return config


def resolve_skill_path(raw: str) -> Path:
    path = Path(os.path.expanduser(raw))
    return path if path.is_absolute() else SKILL_ROOT / path


def state_paths(config: dict[str, Any]) -> dict[str, Path]:
    root = Path(os.path.expanduser(config["runtime"]["state_dir"]))
    return {
        "root": root,
        "state": root / "state.json",
        "history": root / "history.jsonl",
        "lock": root / "avatar.lock",
        "output": root / "output",
        "pending": root / "pending",
        "jobs": root / "jobs",
        "logs": root / "logs",
        "transactions": root / "transactions",
        "baseline_backups": root / "baseline-backups",
        "relationship_backups": root / "relationship-backups",
        "memory_root": root / "memory",
        "memory_events": root / "memory/events.jsonl",
        "memory_summary": root / "memory/summary.json",
        "memory_backups": root / "memory/backups",
    }


def migrate_visual_v030_state_locked(
    paths: dict[str, Path], state: dict[str, Any]
) -> dict[str, Any]:
    """Idempotently extend v0.2.4 state without discarding legacy fields."""
    visual = state.setdefault("visual", {})
    continuity = state.setdefault("continuity", {})
    required_visual = {
        "outerwear": "，".join(state.get("appearance", {}).get("outfit", {}).get("outerwear", [])),
        "top": "",
        "bottom": "",
        "dress": "",
        "headwear": "",
        "accessories": "，".join(state.get("appearance", {}).get("accessories", [])),
    }
    required_continuity: dict[str, Any] = {
        "last_workflow_id": "",
        "last_target": "",
        "last_transaction_id": "",
        "remote_images": {},
        "delivery_status": "",
    }
    already_current = state.get("visual_system_version") == 1
    already_current = already_current and all(key in visual for key in required_visual)
    already_current = already_current and all(
        key in continuity for key in required_continuity
    )
    if already_current:
        return state

    revision = int(continuity.get("revision", 0))
    backup = paths["baseline_backups"] / (
        f"state-v0.2.4-pre-v0.3.0-r{revision:04d}.json"
    )
    if not backup.exists():
        shutil.copy2(paths["state"], backup)
    candidate = copy.deepcopy(state)
    candidate["visual_system_version"] = 1
    for key, value in required_visual.items():
        candidate["visual"].setdefault(key, value)
    for key, value in required_continuity.items():
        candidate["continuity"].setdefault(key, copy.deepcopy(value))
    validate_state(candidate)
    atomic_write_json(paths["state"], candidate)
    append_jsonl(
        paths["history"],
        {
            "at": utc_now(),
            "event": "visual_system_migration",
            "from_version": "0.2.4",
            "to_version": "0.3.0",
            "state_schema": candidate.get("schema_version"),
            "visual_system_version": 1,
            "revision": revision,
            "backup": str(backup),
        },
    )
    return candidate


def ensure_runtime(config: dict[str, Any]) -> dict[str, Path]:
    paths = state_paths(config)
    for key in (
        "root",
        "output",
        "pending",
        "jobs",
        "logs",
        "transactions",
        "baseline_backups",
        "relationship_backups",
        "memory_root",
        "memory_backups",
    ):
        paths[key].mkdir(parents=True, exist_ok=True)
    if not paths["state"].is_file():
        atomic_write_json(paths["state"], load_json(SKILL_ROOT / "defaults/state.default.json"))
    if not paths["memory_summary"].is_file():
        atomic_write_json(
            paths["memory_summary"],
            memory_store.empty_summary(
                utc_now(), int(config["memory"]["summary_max_chars"])
            ),
        )
    with exclusive_lock(paths["lock"]):
        state, _, _ = migrate_state_file(
            paths["state"],
            paths["history"],
            paths["baseline_backups"],
            atomic_writer=atomic_write_json,
            append_event=append_jsonl,
            now=utc_now,
        )
        state = migrate_visual_v030_state_locked(paths, state)
        bootstrap_legacy_memory_locked(paths, config, state)
    return paths


@contextmanager
def exclusive_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def validate_workflow(config: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Validate only the legacy direct-HTTP interface used for this run.

    The workflow is a user-owned production asset.  Do not hash, count, or
    otherwise pin its model, LoRA, topology, or unrelated inputs.
    """
    workflow_path = resolve_skill_path(config["workflow"]["path"])
    if not workflow_path.is_file():
        raise AvatarError(f"工作流不存在：{workflow_path}")
    workflow = load_json(workflow_path)
    wcfg = config["workflow"]
    seed_bindings = wcfg.get("seed_bindings")
    if not isinstance(seed_bindings, list) or not seed_bindings:
        raise AvatarError("legacy workflow 配置缺少 seed_bindings")
    required = [(wcfg["prompt_node_id"], wcfg["prompt_input_name"])]
    for binding in seed_bindings:
        if not isinstance(binding, dict):
            raise AvatarError("legacy workflow seed binding 必须为对象")
        required.append((binding.get("node_id"), binding.get("input_name")))
    for node_id, input_name in required:
        node = workflow.get(node_id)
        if not isinstance(node, dict) or input_name not in node.get("inputs", {}):
            raise AvatarError(
                f"legacy workflow 缺少本次执行所需接口：{node_id}.inputs.{input_name}"
            )
    if wcfg["save_node_id"] not in workflow:
        raise AvatarError(f"legacy workflow 缺少输出节点：{wcfg['save_node_id']}")
    return workflow_path, workflow


def apply_legacy_workflow_parameters(
    workflow: dict[str, Any],
    workflow_config: dict[str, Any],
    *,
    prompt: str,
    seed: int,
) -> None:
    """Write one transaction seed to every configured seed input."""
    workflow[workflow_config["prompt_node_id"]]["inputs"][
        workflow_config["prompt_input_name"]
    ] = prompt
    for binding in workflow_config["seed_bindings"]:
        workflow[binding["node_id"]]["inputs"][binding["input_name"]] = seed


def random_eight_digit_seed() -> int:
    return 10_000_000 + secrets.randbelow(90_000_000)


def parse_set_values(values: Iterable[str], config: dict[str, Any]) -> dict[str, Any]:
    allowed = set(config["state"]["allowed_set_fields"])
    patch: dict[str, Any] = {}
    expanded: list[str] = []
    for item in values:
        expanded.extend(
            part
            for part in re.split(
                r";(?=(?:visual|internal)\.[A-Za-z_][A-Za-z0-9_]*=)",
                item,
            )
            if part
        )
    for item in expanded:
        if "=" not in item:
            raise AvatarError(f"无效 --set 参数，必须为 path=value：{item}")
        path, raw_value = item.split("=", 1)
        path = path.strip()
        if path not in allowed:
            raise AvatarError(f"不允许修改状态字段：{path}")
        raw_value = raw_value.strip()
        if (
            len(raw_value) >= 2
            and raw_value[0] == raw_value[-1]
            and raw_value[0] in {'"', "'"}
        ):
            raw_value = raw_value[1:-1].strip()
        if re.search(
            r"(?:^|[;\s])(?:visual|internal)\.[A-Za-z_][A-Za-z0-9_]*=",
            raw_value,
        ):
            raise AvatarError(f"字段 {path} 的值包含未解析的状态赋值")
        if path in {
            "internal.relationship_stage",
            "internal.affection",
            "internal.trust",
        }:
            try:
                value: Any = int(raw_value)
            except ValueError as exc:
                raise AvatarError(f"字段 {path} 必须是整数") from exc
        else:
            value = raw_value
        patch[path] = value
    return patch


def get_path(obj: dict[str, Any], dotted: str) -> Any:
    current: Any = obj
    for part in dotted.split("."):
        current = current[part]
    return current


def set_path(obj: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    current: Any = obj
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value


def apply_patch(state: dict[str, Any], patch: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for path, value in patch.items():
        before = get_path(state, path)
        if before != value:
            set_path(state, path, value)
            changes.append({"path": path, "from": before, "to": value})
    return changes


def memory_limits(config: dict[str, Any]) -> tuple[int, int, int]:
    memory_config = config["memory"]
    return (
        int(memory_config["max_text_chars"]),
        int(memory_config["summary_per_category_limit"]),
        int(memory_config["summary_max_chars"]),
    )


def build_memory_summary_for(
    events: list[dict[str, Any]], config: dict[str, Any], timestamp: str
) -> dict[str, Any]:
    _, per_category, max_chars = memory_limits(config)
    return memory_store.build_summary(
        events,
        generated_at=timestamp,
        per_category_limit=per_category,
        max_chars=max_chars,
    )


def commit_memory_events_locked(
    paths: dict[str, Path],
    config: dict[str, Any],
    old_events: list[dict[str, Any]],
    candidate_events: list[dict[str, Any]],
    *,
    event_type: str,
    event_details: dict[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    summary = build_memory_summary_for(candidate_events, config, timestamp)
    backup_path = memory_store.commit_memory_transaction(
        events_path=paths["memory_events"],
        summary_path=paths["memory_summary"],
        backups_dir=paths["memory_backups"],
        old_events=old_events,
        candidate_events=candidate_events,
        candidate_summary=summary,
        atomic_text_writer=atomic_write_text,
        atomic_json_writer=atomic_write_json,
        now=timestamp,
    )
    history_event = {
        "at": timestamp,
        "timestamp": timestamp,
        "event": event_type,
        "event_type": event_type,
        **event_details,
        "summary_backup_path": backup_path,
    }
    append_jsonl(paths["history"], history_event)
    return history_event


def memory_add_locked(
    paths: dict[str, Path],
    config: dict[str, Any],
    *,
    memory_type: str,
    importance: int,
    text: str,
    source_event_id: str | None,
) -> dict[str, Any]:
    max_text, _, _ = memory_limits(config)
    old_events = memory_store.load_events(paths["memory_events"], max_text)
    timestamp = utc_now()
    candidate, event, duplicate = memory_store.candidate_add(
        old_events,
        memory_type=memory_type,
        importance=importance,
        text=text,
        source_event_id=source_event_id,
        created_at=timestamp,
        max_text_chars=max_text,
    )
    if duplicate:
        return {
            "ok": True,
            "duplicate": True,
            "memory_id": event["memory_id"],
            "dedupe_key": event["dedupe_key"],
        }
    commit_memory_events_locked(
        paths,
        config,
        old_events,
        candidate,
        event_type="memory_added",
        event_details={
            "event_id": event["memory_id"],
            "memory_id": event["memory_id"],
            "memory_type": event["type"],
            "importance": event["importance"],
            "summary": event["text"][:300],
        },
        timestamp=timestamp,
    )
    return {
        "ok": True,
        "duplicate": False,
        "memory_id": event["memory_id"],
        "dedupe_key": event["dedupe_key"],
    }


def bootstrap_legacy_memory_locked(
    paths: dict[str, Path], config: dict[str, Any], state: dict[str, Any]
) -> None:
    if paths["memory_events"].is_file() and paths["memory_events"].stat().st_size:
        return
    legacy = state.get("memory", {})
    raw_items: list[str] = []
    for item in legacy.get("recent_events", []):
        if isinstance(item, str):
            raw_items.append(item)
        elif isinstance(item, dict) and isinstance(item.get("content"), str):
            raw_items.append(item["content"])
    if not raw_items and isinstance(legacy.get("long_term_summary"), str):
        raw_items.extend(
            line.removeprefix("- ").strip()
            for line in legacy["long_term_summary"].splitlines()
            if line.removeprefix("- ").strip()
        )
    if not raw_items:
        return
    max_text, _, _ = memory_limits(config)
    old_events: list[dict[str, Any]] = []
    candidate: list[dict[str, Any]] = []
    timestamp = utc_now()
    imported = 0
    for text in raw_items:
        text = text[:max_text]
        candidate, _, duplicate = memory_store.candidate_add(
            candidate,
            memory_type="shared_experience",
            importance=3,
            text=text,
            source_event_id="legacy-state-memory",
            created_at=timestamp,
            max_text_chars=max_text,
        )
        if not duplicate:
            imported += 1
    if imported:
        commit_memory_events_locked(
            paths,
            config,
            old_events,
            candidate,
            event_type="memory_legacy_imported",
            event_details={"imported_count": imported},
            timestamp=timestamp,
        )


def visual_status(state: dict[str, Any]) -> str:
    v = state["visual"]
    i = state["internal"]
    return (
        "银月当前视觉状态："
        f"{v['outfit']}；{v['legwear']}；{v['footwear']}；{v['hair']}；"
        f"{v['expression']}；{v['pose']}；{v['action']}；{v['scene']}；"
        f"{v['lighting']}；{v['camera']}。"
        f"当前心情：{i['mood']}。当前活动：{i['current_activity']}。"
        f"关系阶段：{i['relationship_stage']}；亲密度：{i['affection']}；"
        f"信任度：{i['trust']}。"
    )


def wardrobe_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    visual = state["visual"]
    return {
        "revision": int(state["continuity"].get("revision", 0)),
        "clothing": {
            field: str(visual.get(field, "")) for field in WARDROBE_FIELDS
        },
    }


def normalize_wardrobe_changes(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise AvatarError("穿着设置必须是非空 JSON 对象")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = WARDROBE_FIELD_ALIASES.get(str(raw_key), str(raw_key))
        if key not in WARDROBE_FIELDS:
            raise AvatarError(
                f"不允许设置穿着字段：{raw_key}；可用字段："
                + ", ".join(WARDROBE_FIELDS)
            )
        if key in normalized:
            raise AvatarError(f"穿着字段重复：{key}")
        if not isinstance(raw_value, str):
            raise AvatarError(f"穿着字段 {raw_key} 的值必须是字符串")
        item = raw_value.strip()
        if len(item) > 1000:
            raise AvatarError(f"穿着字段 {raw_key} 最长 1000 字符")
        normalized[key] = item

    # Keep the three base-clothing representations mutually exclusive so a
    # future render cannot inherit stale, conflicting garments.
    if normalized.get("outfit"):
        for field in ("top", "bottom", "dress"):
            normalized.setdefault(field, "")
    elif normalized.get("dress"):
        for field in ("outfit", "top", "bottom"):
            normalized.setdefault(field, "")
    elif normalized.get("top") or normalized.get("bottom"):
        normalized.setdefault("outfit", "")
        normalized.setdefault("dress", "")
    return normalized


def cmd_wardrobe_status(config: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **wardrobe_snapshot(cmd_status(config))}


def cmd_wardrobe_update(config: dict[str, Any], value: Any) -> dict[str, Any]:
    normalized = normalize_wardrobe_changes(value)
    patch = {f"visual.{field}": item for field, item in normalized.items()}
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        state = load_json(paths["state"])
        candidate = copy.deepcopy(state)
        changes = apply_patch(candidate, patch)
        sync_v2_from_legacy_patch(candidate, patch)
        if not changes:
            return {
                "ok": True,
                "changed": False,
                "changes": [],
                **wardrobe_snapshot(state),
            }
        event = commit_candidate(
            paths,
            state,
            candidate,
            "wardrobe_update",
            {"changes": changes},
        )
    return {
        "ok": True,
        "changed": True,
        "changes": event["changes"],
        **wardrobe_snapshot(candidate),
    }


def serialize_visual_prompt(state: dict[str, Any]) -> str:
    v = state["visual"]
    return (
        "银月当前状态："
        f"服装为{v['outfit']}；腿部穿着为{v['legwear']}；鞋子为{v['footwear']}；"
        f"发型为{v['hair']}；妆容为{v['makeup']}；表情为{v['expression']}；"
        f"姿势为{v['pose']}；动作为{v['action']}；场景为{v['scene']}；"
        f"光线为{v['lighting']}；镜头为{v['camera']}。"
    )


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: Any | None = None,
    timeout: float = 30,
) -> Any:
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = canonical_json_bytes(payload)
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AvatarError(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AvatarError(f"HTTP 请求失败：{exc}") from exc
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise AvatarError(f"服务返回了非 JSON 响应：{body[:200]!r}") from exc


def submit_comfyui(
    workflow: dict[str, Any], config: dict[str, Any], client_id: str
) -> str:
    base = config["comfyui"]["base_url"].rstrip("/")
    timeout = float(config["comfyui"]["connect_timeout_seconds"])
    result = http_json(
        f"{base}/prompt",
        method="POST",
        payload={"prompt": workflow, "client_id": client_id},
        timeout=timeout,
    )
    if not isinstance(result, dict) or not result.get("prompt_id"):
        raise AvatarError(f"ComfyUI 未返回 prompt_id：{result!r}")
    return str(result["prompt_id"])


def poll_comfyui(prompt_id: str, config: dict[str, Any]) -> dict[str, str]:
    base = config["comfyui"]["base_url"].rstrip("/")
    deadline = time.monotonic() + float(config["comfyui"]["generation_timeout_seconds"])
    interval = float(config["comfyui"]["poll_interval_seconds"])
    save_node = config["workflow"]["save_node_id"]
    while time.monotonic() < deadline:
        history = http_json(
            f"{base}/history/{urllib.parse.quote(prompt_id)}",
            timeout=float(config["comfyui"]["connect_timeout_seconds"]),
        )
        entry = history.get(prompt_id) if isinstance(history, dict) else None
        if isinstance(entry, dict):
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise AvatarError(f"ComfyUI 任务失败：{status!r}")
            outputs = entry.get("outputs", {})
            node_output = outputs.get(save_node, {}) if isinstance(outputs, dict) else {}
            images = node_output.get("images", []) if isinstance(node_output, dict) else []
            if images:
                image = images[0]
                return {
                    "filename": str(image["filename"]),
                    "subfolder": str(image.get("subfolder", "")),
                    "type": str(image.get("type", "output")),
                }
        time.sleep(interval)
    raise AvatarError(
        f"ComfyUI 生成超时，超过 {config['comfyui']['generation_timeout_seconds']} 秒"
    )


def validate_image_bytes(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise AvatarError("ComfyUI 返回内容不是有效的 PNG、JPEG 或 WebP 图片")


def download_image(remote: dict[str, str], config: dict[str, Any]) -> tuple[bytes, str]:
    base = config["comfyui"]["base_url"].rstrip("/")
    query = urllib.parse.urlencode(remote)
    request = urllib.request.Request(f"{base}/view?{query}", method="GET")
    try:
        with urllib.request.urlopen(
            request, timeout=float(config["comfyui"]["connect_timeout_seconds"])
        ) as response:
            data = response.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise AvatarError(f"下载 ComfyUI 图片失败：{exc}") from exc
    return data, validate_image_bytes(data)


def write_image(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def resolve_hermes_cli(config: dict[str, Any]) -> str:
    raw = str(config["telegram"].get("hermes_cli", "hermes")).strip() or "hermes"
    expanded = os.path.expanduser(raw)
    if Path(expanded).is_absolute() or os.sep in expanded:
        return expanded
    resolved = shutil.which(expanded)
    if resolved:
        return resolved
    user_local = Path.home() / ".local" / "bin" / expanded
    if user_local.is_file() and os.access(user_local, os.X_OK):
        return str(user_local)
    return expanded



# identity-studio:v0.1.8.2 telegram delivery compatibility runtime

def normalize_delivery_target(channel: str) -> str:
    """Normalize bare Telegram chat ids/usernames to Hermes platform targets."""
    raw = str(channel or "").strip()
    if not raw:
        raise AvatarError("Telegram delivery channel is empty")
    if ":" in raw:
        platform, destination = raw.split(":", 1)
        if not platform.strip() or not destination.strip():
            raise AvatarError(f"invalid Hermes delivery target: {raw!r}")
        return raw
    return f"telegram:{raw}"


def cmd_retry_delivery(
    config: dict[str, Any], transaction_id: str, channel: str = ""
) -> dict[str, Any]:
    """Retry Telegram delivery of one already-committed image without regenerating."""
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        transaction = load_transaction(paths, transaction_id)
        if transaction.get("status") == "delivered":
            return {
                "ok": True,
                "idempotent": True,
                "generation_committed": True,
                "delivery_retried": False,
                "transaction": transaction,
                "agent_action": "stop_silently",
            }
        if transaction.get("status") != "pending_delivery":
            raise AvatarError(
                f"transaction 状态不允许 retry-delivery：{transaction.get('status')}"
            )
        if transaction.get("generation_status") != "completed":
            raise AvatarError("retry-delivery requires generation_status=completed")
        if transaction.get("commit_status") != "committed":
            raise AvatarError("retry-delivery requires commit_status=committed")
        if int(transaction.get("submit_count", 0) or 0) != 1:
            raise AvatarError("retry-delivery requires submit_count=1")
        result_image = str(transaction.get("result_image") or "").strip()
        if not result_image:
            raise AvatarError("retry-delivery transaction has no result_image")
        prompt_id = str(transaction.get("prompt_id") or "").strip()
        if not prompt_id:
            raise AvatarError("retry-delivery transaction has no prompt_id")
        delivery_channel = str(
            channel
            or transaction.get("channel")
            or config.get("telegram", {}).get("default_channel")
            or ""
        ).strip()
        retry_count = int(transaction.get("delivery_retry_count", 0) or 0) + 1

    source, _data, _suffix = verified_local_image(result_image)
    delivery = deliver_text_and_media(
        say=str(transaction.get("say") or ""),
        image=source,
        channel=delivery_channel,
        config=config,
    )
    if delivery.get("ok"):
        final_status = "delivered"
        delivery_status = "delivered"
    else:
        final_status = "pending_delivery"
        delivery_status = "pending_delivery"

    with exclusive_lock(paths["lock"]):
        current = load_transaction(paths, transaction_id)
        if current.get("prompt_id") != prompt_id or int(current.get("submit_count", 0) or 0) != 1:
            raise AvatarError("retry-delivery transaction binding changed during delivery")
        current.update(
            status=final_status,
            delivery=delivery,
            delivery_status=delivery_status,
            delivery_retry_count=retry_count,
            delivery_last_attempt_at=utc_now(),
        )
        atomic_write_json(transaction_path(paths, transaction_id), current)
        state = load_json(paths["state"])
        if state["continuity"].get("last_transaction_id") == transaction_id:
            state["continuity"]["delivery_status"] = delivery_status
            atomic_write_json(paths["state"], state)
        append_jsonl(
            paths["history"],
            {
                "at": utc_now(),
                "event": "transaction_delivery_retry",
                "transaction_id": transaction_id,
                "revision": current.get("committed_revision"),
                "workflow_id": current.get("workflow_id"),
                "target": current.get("target"),
                "prompt_id": prompt_id,
                "result_image": current.get("result_image"),
                "submit_count": current.get("submit_count"),
                "delivery_retry_count": retry_count,
                "generation_status": "completed",
                "delivery_status": delivery_status,
            },
        )
    return {
        "ok": bool(delivery.get("ok")),
        "idempotent": False,
        "generation_committed": True,
        "delivery_retried": True,
        "delivery": delivery,
        "transaction": current,
        "agent_action": "stop_silently",
    }

def run_send_once(
    hermes_cli: str, channel: str, message: str, timeout_seconds: int
) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [hermes_cli, "send", "--to", normalize_delivery_target(channel), message],
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": f"hermes send 超时（{timeout_seconds} 秒）",
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": f"无法启动 hermes send：{exc}",
            "stdout": "",
            "stderr": str(exc),
        }
    if result.returncode == 0:
        return {"ok": True, "stdout": result.stdout, "stderr": result.stderr}
    return {
        "ok": False,
        "error": f"hermes send 失败，退出码 {result.returncode}",
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def run_send_with_retry(
    hermes_cli: str,
    channel: str,
    message: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    tcfg = config["telegram"]
    retries = max(1, int(tcfg.get("send_retries", 1)))
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, retries + 1):
        result = run_send_once(
            hermes_cli,
            channel,
            message,
            int(tcfg["send_timeout_seconds"]),
        )
        result["attempt"] = attempt
        attempts.append(result)
        if result["ok"]:
            return {"ok": True, "attempts": attempts}
        if attempt < retries:
            time.sleep(float(tcfg.get("retry_delay_seconds", 2)))
    return {"ok": False, "attempts": attempts, "error": attempts[-1].get("error", "发送失败")}


def deliver_text_and_media(
    *,
    say: str,
    image: Path,
    channel: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    if not bool(config["telegram"].get("enabled", True)):
        return {
            "ok": False,
            "attempted": False,
            "blocked": True,
            "error": "Telegram delivery is disabled by configuration",
        }
    hermes_cli = resolve_hermes_cli(config)
    result: dict[str, Any] = {
        "ok": False,
        "attempted": True,
        "channel": channel,
        "delivery_target": normalize_delivery_target(channel),
        "text": {"ok": True, "skipped": True},
        "media": {"ok": False},
    }
    # Deliberately send text and MEDIA as two independent Hermes messages.
    if say.strip():
        result["text"] = run_send_with_retry(hermes_cli, channel, say.strip(), config)
    media_message = f"MEDIA:{image.resolve()}"
    result["media"] = run_send_with_retry(hermes_cli, channel, media_message, config)
    result["ok"] = bool(result["media"].get("ok"))
    if not result["ok"]:
        result["error"] = result["media"].get("error", "媒体发送失败")
    return result


def request_fingerprint(request: dict[str, Any]) -> str:
    relevant = {
        "action": request["action"],
        "base_revision": request.get("base_revision"),
        "patch": request.get("patch", {}),
        "say": request.get("say", ""),
        "remember": request.get("remember", ""),
        "channel": request.get("channel", ""),
        "no_send": bool(request.get("no_send", False)),
        "transition": request.get("transition"),
    }
    return hashlib.sha256(canonical_json_bytes(relevant)).hexdigest()


def read_job(path: Path) -> dict[str, Any] | None:
    try:
        return load_json(path)
    except (OSError, json.JSONDecodeError):
        return None


def find_duplicate_job(
    paths: dict[str, Path], fingerprint: str, config: dict[str, Any]
) -> dict[str, Any] | None:
    now = time.time()
    stale = int(config["runtime"].get("job_stale_seconds", 1200))
    completed_window = int(config["runtime"].get("completed_dedup_seconds", 15))
    for path in sorted(paths["jobs"].glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]:
        job = read_job(path)
        if not job or job.get("fingerprint") != fingerprint:
            continue
        age = now - float(job.get("created_epoch", 0))
        status = job.get("status")
        if status in {"queued", "running"} and age < stale:
            return job
        if status in {"completed", "pending_delivery"} and age < completed_window:
            return job
    return None


def launch_worker(job_path: Path, config: dict[str, Any], log_path: Path) -> dict[str, Any]:
    launcher = str(config["runtime"].get("launcher", "auto"))
    if launcher == "disabled":
        raise AvatarError("background worker launch is disabled by configuration")
    python = sys.executable
    worker_cmd = [python, str(Path(__file__).resolve()), WORKER_ACTION, "--job", str(job_path)]
    systemd_run = shutil.which("systemd-run")
    if launcher in {"auto", "systemd"} and systemd_run:
        unit = f"xiaowu-avatar-{job_path.stem[:20]}"
        result = subprocess.run(
            [
                systemd_run,
                "--user",
                "--collect",
                "--quiet",
                f"--unit={unit}",
                *worker_cmd,
            ],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            return {"launcher": "systemd", "unit": unit}
        if launcher == "systemd":
            raise AvatarError(
                f"systemd-run 启动后台任务失败：{result.stderr.strip() or result.stdout.strip()}"
            )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab", buffering=0)
    try:
        process = subprocess.Popen(
            worker_cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_handle.close()
    return {"launcher": "detached", "pid": process.pid}


def queue_job(request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    paths = ensure_runtime(config)
    state = load_json(paths["state"])
    request["base_revision"] = int(state["continuity"].get("revision", 0))
    request["created_at"] = utc_now()
    request["created_epoch"] = time.time()
    fingerprint = request_fingerprint(request)
    duplicate = find_duplicate_job(paths, fingerprint, config)
    if duplicate:
        return {
            "ok": True,
            "queued": duplicate.get("status") in {"queued", "running"},
            "deduplicated": True,
            "job_id": duplicate["job_id"],
            "job_status": duplicate.get("status"),
            "agent_action": "stop_silently",
        }
    job_id = uuid.uuid4().hex
    job_path = paths["jobs"] / f"{job_id}.json"
    log_path = paths["logs"] / f"{job_id}.log"
    job = {
        "job_id": job_id,
        "status": "queued",
        "fingerprint": fingerprint,
        **request,
        "job_file": str(job_path),
        "log_file": str(log_path),
    }
    atomic_write_json(job_path, job)
    try:
        launch = launch_worker(job_path, config, log_path)
    except Exception:
        job["status"] = "failed_to_launch"
        job["error"] = traceback.format_exc()
        atomic_write_json(job_path, job)
        raise
    # The worker may already have moved the job to running. Merge the launcher
    # metadata into the latest job file instead of overwriting its status.
    job_update(job_path, launch=launch)
    return {
        "ok": True,
        "queued": True,
        "deduplicated": False,
        "job_id": job_id,
        "launcher": launch,
        "status_command": f"{SKILL_ROOT / 'bin/avatarctl'} job-status {job_id}",
        "agent_action": "stop_silently",
    }


def job_update(job_path: Path, **updates: Any) -> dict[str, Any]:
    job = load_json(job_path)
    job.update(updates)
    atomic_write_json(job_path, job)
    return job


def save_pending(paths: dict[str, Path], job: dict[str, Any], delivery: dict[str, Any]) -> Path:
    pending_path = paths["pending"] / f"{job['job_id']}.json"
    atomic_write_json(
        pending_path,
        {
            "job_id": job["job_id"],
            "created_at": utc_now(),
            "image": job.get("result", {}).get("image", ""),
            "say": job.get("say", ""),
            "channel": job.get("channel", "telegram"),
            "delivery": delivery,
        },
    )
    return pending_path


def run_generation_job(job_path: Path, config: dict[str, Any]) -> None:
    paths = ensure_runtime(config)
    job = job_update(job_path, status="running", started_at=utc_now())
    with exclusive_lock(paths["lock"]):
        state = load_json(paths["state"])
        candidate = copy.deepcopy(state)
        if job.get("transition"):
            candidate = apply_transition(candidate, job["transition"])
            changes = [{"path": "transition", "to": job["transition"]}]
        else:
            changes = apply_patch(candidate, job.get("patch", {}))
            sync_v2_from_legacy_patch(candidate, job.get("patch", {}))
        _, workflow = validate_workflow(config)
        prompt_text = serialize_visual_prompt(candidate)
        seed = random_eight_digit_seed()
        wcfg = config["workflow"]
        apply_legacy_workflow_parameters(
            workflow,
            wcfg,
            prompt=prompt_text,
            seed=seed,
        )
        client_id = f"xiaowu-avatar-{job['job_id']}"
        prompt_id = submit_comfyui(workflow, config, client_id)
        job_update(job_path, prompt_id=prompt_id, seed=seed)
        remote = poll_comfyui(prompt_id, config)
        image_data, suffix = download_image(remote, config)
        revision = int(state["continuity"].get("revision", 0)) + 1
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        image_path = paths["output"] / f"yinyue-{timestamp}-r{revision:04d}{suffix}"
        write_image(image_path, image_data)
        prompt_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        candidate["continuity"].update(
            {
                "revision": revision,
                "updated_at": utc_now(),
                "last_image": str(image_path.resolve()),
                "last_prompt_sha256": prompt_sha,
                "last_prompt_id": prompt_id,
            }
        )
        event = {
            "at": utc_now(),
            "event": "transition_render" if job.get("transition") else job["action"],
            "revision": revision,
            "changes": changes,
            "memory": job.get("remember", ""),
            "prompt_id": prompt_id,
            "seed": seed,
            "remote_image": remote,
            "image": str(image_path.resolve()),
            "job_id": job["job_id"],
        }
        atomic_write_json(paths["state"], candidate)
        append_jsonl(paths["history"], event)
        if job.get("remember", "").strip():
            memory_add_locked(
                paths,
                config,
                memory_type="shared_experience",
                importance=3,
                text=job["remember"],
                source_event_id=job["job_id"],
            )
    result = {
        "generated": True,
        "event": event,
        "image": str(image_path.resolve()),
        "prompt_id": prompt_id,
        "seed": seed,
        "status": visual_status(candidate),
    }
    job = job_update(job_path, result=result, generated_at=utc_now())
    if job.get("no_send", False):
        delivery = {"ok": True, "attempted": False, "skipped": True}
    else:
        delivery = deliver_text_and_media(
            say=job.get("say", ""),
            image=image_path,
            channel=job.get("channel", config["telegram"]["default_channel"]),
            config=config,
        )
    result["delivery"] = delivery
    final_status = "completed" if delivery.get("ok") else "pending_delivery"
    pending_file = ""
    if not delivery.get("ok"):
        job["result"] = result
        pending_file = str(save_pending(paths, job, delivery))
        result["pending_file"] = pending_file
    job_update(
        job_path,
        status=final_status,
        completed_at=utc_now(),
        result=result,
        pending_file=pending_file,
        agent_action="stop_silently",
    )
    if final_status == "pending_delivery":
        with exclusive_lock(paths["lock"]):
            append_jsonl(
                paths["history"],
                {
                    "at": utc_now(),
                    "event": "delivery_pending",
                    "event_type": "delivery_pending",
                    "event_id": f"delivery-{job['job_id']}",
                    "job_id": job["job_id"],
                    "image": str(image_path.resolve()),
                },
            )


def run_resend_job(job_path: Path, config: dict[str, Any]) -> None:
    paths = ensure_runtime(config)
    job = job_update(job_path, status="running", started_at=utc_now())
    state = load_json(paths["state"])
    image_path = Path(state["continuity"].get("last_image", ""))
    if not image_path.is_file():
        raise AvatarError(f"没有可重新发送的上一张图片：{image_path}")
    delivery = deliver_text_and_media(
        say=job.get("say", ""),
        image=image_path,
        channel=job.get("channel", config["telegram"]["default_channel"]),
        config=config,
    )
    result = {"image": str(image_path), "delivery": delivery}
    final_status = "completed" if delivery.get("ok") else "pending_delivery"
    pending_file = ""
    if not delivery.get("ok"):
        job["result"] = result
        pending_file = str(save_pending(paths, job, delivery))
        result["pending_file"] = pending_file
    job_update(
        job_path,
        status=final_status,
        completed_at=utc_now(),
        result=result,
        pending_file=pending_file,
        agent_action="stop_silently",
    )


def worker_main(job_path: Path) -> int:
    config = load_config()
    try:
        job = load_json(job_path)
        if job["action"] in {"show", "render", "render-transition"}:
            run_generation_job(job_path, config)
        elif job["action"] == "resend-last":
            run_resend_job(job_path, config)
        else:
            raise AvatarError(f"未知后台任务：{job['action']}")
        return 0
    except Exception as exc:
        try:
            job_update(
                job_path,
                status="failed",
                completed_at=utc_now(),
                error=str(exc),
                traceback=traceback.format_exc(),
                agent_action="stop_silently",
            )
        except Exception:
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 2


def foreground_request(request: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    paths = ensure_runtime(config)
    job_id = uuid.uuid4().hex
    job_path = paths["jobs"] / f"{job_id}.json"
    state = load_json(paths["state"])
    job = {
        "job_id": job_id,
        "status": "queued",
        "fingerprint": "foreground",
        "base_revision": int(state["continuity"].get("revision", 0)),
        "created_at": utc_now(),
        "created_epoch": time.time(),
        **request,
        "job_file": str(job_path),
        "log_file": str(paths["logs"] / f"{job_id}.log"),
    }
    atomic_write_json(job_path, job)
    code = worker_main(job_path)
    final = load_json(job_path)
    if code != 0:
        raise AvatarError(final.get("error", "后台任务失败"))
    return {
        "ok": True,
        "queued": False,
        "job_id": job_id,
        "job_status": final.get("status"),
        "result": final.get("result"),
        "agent_action": "stop_silently",
    }


def workflow_registry(config: dict[str, Any]) -> dict[str, Any]:
    path = resolve_skill_path(config["execution"]["registry_path"])
    try:
        return visual_system.load_registry(path)
    except visual_system.VisualSystemError as exc:
        raise AvatarError(str(exc)) from exc


def transaction_path(paths: dict[str, Path], transaction_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise AvatarError("transaction_id 格式无效")
    return paths["transactions"] / f"{transaction_id}.json"


def load_transaction(paths: dict[str, Path], transaction_id: str) -> dict[str, Any]:
    path = transaction_path(paths, transaction_id)
    if not path.is_file():
        raise AvatarError(f"找不到 transaction：{transaction_id}")
    value = load_json(path)
    if value.get("transaction_id") != transaction_id:
        raise AvatarError("transaction 文件内容不一致")
    return normalize_transaction_v032(value)


def normalize_transaction_v032(value: dict[str, Any]) -> dict[str, Any]:
    """Read older 0.3.x transaction records without destructive migration."""
    transaction = copy.deepcopy(value)
    parameters = transaction.get("parameters", {})
    original = transaction.get("original_workflow_path") or transaction.get("workflow_path", "")
    transaction.setdefault("original_workflow_path", original)
    transaction.setdefault("variant_workflow_path", "")
    transaction.setdefault("variant_dir", "")
    transaction.setdefault("requested_parameters", copy.deepcopy(parameters))
    if "seed" not in transaction and "seed" in parameters:
        transaction["seed"] = parameters["seed"]
    transaction.setdefault("submit_count", 1 if transaction.get("prompt_id") else 0)
    transaction.setdefault(
        "job_status",
        "submitted" if transaction.get("prompt_id") else "not_submitted",
    )
    transaction.setdefault(
        "result",
        {
            "local_image": transaction.get("result_image", ""),
            "remote_image": transaction.get("remote_result_image", ""),
        },
    )
    transaction.setdefault(
        "commit_status",
        "committed" if transaction.get("committed_revision") else "not_committed",
    )
    delivery = transaction.get("delivery", {})
    transaction.setdefault(
        "delivery_status",
        delivery.get("status", "not_attempted") if isinstance(delivery, dict) else "not_attempted",
    )
    return transaction


def _remote_image_for_target(state: dict[str, Any], target: str) -> str:
    value = state.get("continuity", {}).get("remote_images", {}).get(target, "")
    return str(value.get("path", "")) if isinstance(value, dict) else str(value or "")


def _find_active_transaction(
    paths: dict[str, Path], fingerprint: str, target: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for path in sorted(
        paths["transactions"].glob("*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )[:200]:
        transaction = read_job(path)
        if not transaction or transaction.get("status") not in {
            "planned", "variant_verified", "submit_claimed", "submitted",
            "waiting", "fetch_pending", "generation_completed",
        }:
            continue
        legacy_seed_addresses = {
            item.get("address")
            for item in transaction.get("slot_overrides", [])
            if isinstance(item, dict)
        }
        legacy_parameters = transaction.get("parameters", {})
        legacy_seed_only = (
            transaction.get("status") in {"planned", "variant_verified"}
            and not transaction.get("prompt_id")
            and int(transaction.get("submit_count", 0) or 0) == 0
            and (
                "seed" in transaction
                or (isinstance(legacy_parameters, dict) and "seed" in legacy_parameters)
            )
            and "111.seed" not in legacy_seed_addresses
        )
        if legacy_seed_only:
            # Preserve the old record, but never return it to a new MCP prepare or
            # let its per-sampler overrides block the unified-seed protocol.
            continue
        if transaction.get("fingerprint") == fingerprint:
            return transaction, None
        if transaction.get("target") == target:
            return None, transaction
    return None, None



# identity-studio:v0.1.8 clean-state long-job runtime

def _generation_timeout_for_target(config: dict[str, Any], target: str) -> int:
    execution = config.get("execution", {})
    if not isinstance(execution, dict):
        execution = {}
    per_target = execution.get("generation_timeout_seconds_by_target", {})
    if isinstance(per_target, dict):
        raw = per_target.get(target)
        if raw is not None:
            try:
                value = int(raw)
            except (TypeError, ValueError) as exc:
                raise AvatarError(f"invalid generation timeout for {target}: {raw!r}") from exc
            if value < 30:
                raise AvatarError(f"generation timeout for {target} must be >= 30 seconds")
            return value
    value = int(execution.get("generation_timeout_seconds", 240))
    if value < 30:
        raise AvatarError("execution.generation_timeout_seconds must be >= 30")
    return value

def cmd_prepare(
    config: dict[str, Any],
    *,
    intent: str,
    patch: dict[str, Any],
    requested_workflow: str,
    requested_target: str,
    aspect_ratio: str,
    megapixels: float | None,
    width: int | None,
    height: int | None,
    say: str,
    channel: str,
    no_send: bool,
    transition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if config.get("execution", {}).get("mode") != "mcp":
        raise AvatarError("prepare 仅用于 execution.mode=mcp")
    paths = ensure_runtime(config)
    registry = workflow_registry(config)
    workflow_id, intent_class = visual_system.route_intent(intent, requested_workflow)
    if workflow_id not in registry["workflows"]:
        raise AvatarError(f"未知 workflow：{workflow_id}")
    with exclusive_lock(paths["lock"]):
        state = load_json(paths["state"])
        if transition:
            candidate = apply_transition(state, transition)
            changes = [{"path": "transition", "to": transition}]
        else:
            candidate = copy.deepcopy(state)
            changes = apply_patch(candidate, patch)
            sync_v2_from_legacy_patch(candidate, patch)
        workflow = registry["workflows"][workflow_id]
        fallback_reason = ""
        try:
            target = visual_system.select_target(
                registry,
                workflow,
                requested_target=requested_target,
                default_target=config["execution"]["default_target"],
            )
        except visual_system.VisualSystemError as exc:
            if workflow["purpose"] != "image_edit":
                raise AvatarError(str(exc)) from exc
            fallback_reason = (
                "image_edit 当前没有已验证可用的 target；"
                "安全回退到 yinyue_cosplay01"
            )
            workflow_id = "yinyue_cosplay01"
            intent_class = "full_regeneration_fallback"
            workflow = registry["workflows"][workflow_id]
            try:
                target = visual_system.select_target(
                    registry,
                    workflow,
                    requested_target=requested_target,
                    default_target=config["execution"]["default_target"],
                )
            except visual_system.VisualSystemError as fallback_exc:
                raise AvatarError(f"{fallback_reason}；{fallback_exc}") from fallback_exc
        source_remote_image = ""
        source_local_image = ""
        if workflow["purpose"] == "image_edit":
            source_local_image = str(state["continuity"].get("last_image", ""))
            source_remote_image = _remote_image_for_target(state, target)
            if not source_local_image or not Path(source_local_image).is_file() or not source_remote_image:
                fallback_reason = (
                    "image_edit 没有同一 target 可上传的 continuity source；"
                    "安全回退到 yinyue_cosplay01"
                )
                workflow_id = "yinyue_cosplay01"
                intent_class = "full_regeneration_fallback"
                workflow = registry["workflows"][workflow_id]
                try:
                    target = visual_system.select_target(
                        registry,
                        workflow,
                        requested_target=requested_target,
                        default_target=config["execution"]["default_target"],
                    )
                except visual_system.VisualSystemError as exc:
                    raise AvatarError(f"{fallback_reason}；{exc}") from exc
                source_remote_image = ""
                source_local_image = ""
        prompt = (
            visual_system.transform_edit_prompt(intent)
            if workflow["purpose"] == "image_edit"
            else visual_system.build_generation_prompt(
                candidate,
                intent=intent,
                intent_class=intent_class,
                changed_fields={
                    key
                    for key in candidate.get("visual", {})
                    if state.get("visual", {}).get(key) != candidate["visual"].get(key)
                },
            )
        )
        transaction_id = uuid.uuid4().hex
        try:
            resolution = visual_system.parse_resolution(
                intent,
                aspect_ratio=aspect_ratio,
                megapixels=megapixels,
                width=width,
                height=height,
            )
            parameters = visual_system.build_parameters(
                workflow,
                prompt=prompt,
                resolution=resolution,
                source_image=source_remote_image,
                seed=(
                    random_eight_digit_seed()
                    if "seed" in workflow.get("parameter_bindings", {})
                    else None
                ),
            )
            slot_overrides = visual_system.build_slot_overrides(workflow, parameters)
        except visual_system.VisualSystemError as exc:
            raise AvatarError(str(exc)) from exc
        target_config = registry["targets"][target]
        remote_output_root = target_config["remote_output_root"].rstrip("\\/")
        remote_variant_root = target_config.get(
            "remote_variant_root",
            target_config["remote_root"].rstrip("\\/") + "\\temp",
        ).rstrip("\\/")
        original_workflow_path = workflow["remote_paths"][target]
        transaction = {
            "transaction_id": transaction_id,
            "schema_version": 2,
            "status": "planned",
            "created_at": utc_now(),
            "created_epoch": time.time(),
            "base_revision": int(state["continuity"].get("revision", 0)),
            "intent": intent,
            "intent_class": intent_class,
            "workflow_id": workflow_id,
            "target": target,
            "workflow_path": original_workflow_path,
            "original_workflow_path": original_workflow_path,
            "variant_dir": remote_variant_root + "\\" + transaction_id,
            "variant_workflow_path": "",
            "parameters": parameters,
            "requested_parameters": copy.deepcopy(parameters),
            "parameter_summary": sorted(parameters),
            "slot_overrides": slot_overrides,
            "patch": patch,
            "transition": transition,
            "changes": changes,
            "candidate_state": candidate,
            "source_image": source_local_image,
            "source_remote_image": source_remote_image,
            "remote_output_dir": remote_output_root + "\\" + transaction_id,
            "deadline_seconds": _generation_timeout_for_target(config, target),
            "say": say,
            "channel": channel or config["telegram"]["default_channel"],
            "no_send": bool(no_send),
            "fallback_reason": fallback_reason,
            "prompt_id": "",
            "submit_count": 0,
            "job_status": "not_submitted",
            "result_image": "",
            "remote_result_image": "",
            "result": {"local_image": "", "remote_image": ""},
            "commit_status": "not_committed",
            "delivery_status": "not_attempted",
            "delivery": {"status": "not_attempted"},
        }
        transaction["fingerprint"] = visual_system.transaction_fingerprint(transaction)
        duplicate, blocking = _find_active_transaction(paths, transaction["fingerprint"], target)
        if duplicate:
            return {
                "ok": True,
                "deduplicated": True,
                "transaction": duplicate,
                "plan": visual_system.build_mcp_plan(duplicate, registry),
                "agent_action": "execute_mcp_plan",
            }
        if blocking:
            raise AvatarError(
                "同一 target 已有 active transaction；planned 状态先 abort，已提交状态只恢复原 job："
                f"{blocking['transaction_id']} prompt_id={blocking.get('prompt_id', '')}"
            )
        atomic_write_json(transaction_path(paths, transaction_id), transaction)
        append_jsonl(
            paths["history"],
            {
                "at": utc_now(),
                "event": "transaction_prepared",
                "transaction_id": transaction_id,
                "revision": transaction["base_revision"],
                "user_intent": intent,
                "workflow_id": workflow_id,
                "target": target,
                "parameter_summary": transaction["parameter_summary"],
                "original_workflow_path": original_workflow_path,
                "variant_dir": transaction["variant_dir"],
                "state_changes": changes,
                "source_image": source_local_image,
                "generation_status": "planned",
                "delivery_status": "not_attempted"
            },
        )
    return {
        "ok": True,
        "deduplicated": False,
        "transaction": transaction,
        "plan": visual_system.build_mcp_plan(transaction, registry),
        "agent_action": "execute_mcp_plan",
    }


def _windows_path_key(raw: str) -> str:
    value = str(raw or "").strip().replace("/", "\\")
    drive, _ = ntpath.splitdrive(value)
    if not drive or not ntpath.isabs(value):
        raise AvatarError(f"MCP variant path 必须是 Windows 绝对路径：{raw}")
    return ntpath.normcase(ntpath.normpath(value))


def _parse_observed_slots(raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AvatarError(f"variant slots JSON 无法解析：{exc}") from exc
    for _ in range(3):
        if isinstance(payload, dict) and "slots" in payload:
            break
        if not isinstance(payload, dict) or "result" not in payload:
            break
        payload = payload["result"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise AvatarError(f"variant slots result 无法解析：{exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("slots"), list):
        raise AvatarError("variant slots JSON 缺少 slots 数组")
    observed: dict[str, Any] = {}
    for item in payload["slots"]:
        if not isinstance(item, dict):
            continue
        address = item.get("address")
        if isinstance(address, str) and address:
            observed[address] = item.get("current_value")
    return payload, observed


def _slot_values_equal(expected: Any, observed: Any) -> bool:
    numeric = (int, float)
    if (
        isinstance(expected, numeric) and not isinstance(expected, bool)
        and isinstance(observed, numeric) and not isinstance(observed, bool)
    ):
        return float(expected) == float(observed)
    return type(expected) is type(observed) and expected == observed



# identity-studio:v0.1.7 everyone-avatar api runtime

def _everyone_api_contract(
    config: dict[str, Any], transaction: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    registry = workflow_registry(config)
    workflow = registry["workflows"].get(transaction.get("workflow_id"))
    if not isinstance(workflow, dict):
        raise AvatarError("transaction workflow registry entry missing")
    contract = workflow.get("identity_workflow_contract", {})
    if not isinstance(contract, dict):
        raise AvatarError("Everyone Avatar identity_workflow_contract missing")
    if contract.get("mode") != "everyone-avatar":
        raise AvatarError("workflow is not Everyone Avatar")
    if contract.get("workflow_format") != "api":
        raise AvatarError("Everyone Avatar workflow_format must be api")
    if contract.get("variant_strategy") != "local_json_scp_sha256":
        raise AvatarError("Everyone Avatar variant_strategy mismatch")
    return workflow, contract


def _build_everyone_api_variant(
    config: dict[str, Any], transaction: dict[str, Any]
) -> dict[str, Any]:
    workflow, contract = _everyone_api_contract(config, transaction)
    local_raw = str(workflow.get("local_path") or config.get("workflow", {}).get("path") or "")
    if not local_raw:
        raise AvatarError("Everyone Avatar local workflow path missing")
    local_path = resolve_skill_path(local_raw)
    if not local_path.is_file():
        raise AvatarError(f"Everyone Avatar local workflow missing: {local_path}")
    source = load_json(local_path)
    if not isinstance(source, dict) or not source:
        raise AvatarError("Everyone Avatar API workflow root must be an object")

    prompt_node = source.get("45")
    ref_node = source.get("17")
    if (
        not isinstance(prompt_node, dict)
        or prompt_node.get("class_type") != "PrimitiveStringMultiline"
        or not isinstance(prompt_node.get("inputs"), dict)
        or "value" not in prompt_node["inputs"]
    ):
        raise AvatarError("Everyone Avatar node 45 prompt contract mismatch")
    if (
        not isinstance(ref_node, dict)
        or ref_node.get("class_type") != "LoadImage"
        or not isinstance(ref_node.get("inputs"), dict)
        or "image" not in ref_node["inputs"]
    ):
        raise AvatarError("Everyone Avatar node 17 reference contract mismatch")

    reference_filename = str(contract.get("reference_filename") or "")
    if not reference_filename:
        raise AvatarError("Everyone Avatar reference_filename missing")
    if ref_node["inputs"]["image"] != reference_filename:
        raise AvatarError(
            "Everyone Avatar local node17 image mismatch: "
            f"expected {reference_filename!r}, got {ref_node['inputs']['image']!r}"
        )

    runtime_mutable = contract.get("runtime_mutable", ["45.value"])
    if runtime_mutable != ["45.value"]:
        raise AvatarError("Everyone Avatar runtime_mutable must be exactly ['45.value']")
    overrides = [
        item for item in transaction.get("slot_overrides", [])
        if isinstance(item, dict) and item.get("address") == "45.value"
    ]
    other = [
        item.get("address") for item in transaction.get("slot_overrides", [])
        if isinstance(item, dict) and item.get("address") != "45.value"
    ]
    if other:
        raise AvatarError(f"Everyone Avatar forbids runtime overrides outside 45.value: {other}")
    if len(overrides) != 1 or "value" not in overrides[0]:
        raise AvatarError("Everyone Avatar transaction must contain exactly one 45.value override")

    variant = copy.deepcopy(source)
    variant["45"]["inputs"]["value"] = overrides[0]["value"]

    # Prove that the deterministic patch changed only node 45.inputs.value.
    check = copy.deepcopy(variant)
    check["45"]["inputs"]["value"] = source["45"]["inputs"]["value"]
    if check != source:
        raise AvatarError("Everyone Avatar API variant mutation escaped 45.value")

    content = json.dumps(variant, ensure_ascii=False, indent=2) + "\n"
    content_bytes = content.encode("utf-8")
    digest = hashlib.sha256(content_bytes).hexdigest()
    basename = ntpath.basename(transaction["original_workflow_path"])
    if not basename.lower().endswith(".json"):
        raise AvatarError("Everyone Avatar original workflow basename is not JSON")
    variant_path = transaction["variant_dir"].rstrip("\\/") + "\\" + basename
    return {
        "content": content,
        "content_bytes": content_bytes,
        "sha256": digest,
        "variant_workflow_path": variant_path,
        "local_workflow_path": str(local_path),
        "reference_filename": reference_filename,
        "prompt": overrides[0]["value"],
    }


def build_everyone_api_variant(
    config: dict[str, Any], transaction_id: str
) -> dict[str, Any]:
    paths = ensure_runtime(config)
    transaction = load_transaction(paths, transaction_id)
    if transaction.get("prompt_id") or int(transaction.get("submit_count", 0)):
        raise AvatarError("transaction already entered submit stage; API variant cannot be rebuilt")
    if transaction.get("status") not in {"planned", "variant_verified"}:
        raise AvatarError(f"transaction status cannot build API variant: {transaction.get('status')}")
    return _build_everyone_api_variant(config, transaction)


def cmd_verify_variant(
    config: dict[str, Any],
    transaction_id: str,
    variant_workflow_path: str,
    observed_slots_json: str = "",
    observed_slots_base64: str = "",
    uploaded_filename: str = "",
) -> dict[str, Any]:
    if bool(observed_slots_json) == bool(observed_slots_base64):
        raise AvatarError(
            "必须且只能提供 observed slots JSON 或 Base64 其中一种"
        )
    if observed_slots_base64:
        try:
            observed_slots_json = base64.b64decode(
                observed_slots_base64, validate=True
            ).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise AvatarError("variant slots Base64 无法解码为 UTF-8 JSON") from exc
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        transaction = load_transaction(paths, transaction_id)
        if transaction.get("prompt_id") or int(transaction.get("submit_count", 0)):
            raise AvatarError("transaction 已进入 submit 阶段，禁止重新验证或更换 variant")
        if transaction.get("status") == "variant_verified":
            if _windows_path_key(transaction.get("variant_workflow_path", "")) != _windows_path_key(variant_workflow_path):
                raise AvatarError("transaction 已验证另一个 variant path")
            return {"ok": True, "idempotent": True, "transaction": transaction}
        if transaction.get("status") != "planned":
            raise AvatarError(f"transaction 状态不允许验证 variant：{transaction.get('status')}")

        original_key = _windows_path_key(transaction["original_workflow_path"])
        variant_key = _windows_path_key(variant_workflow_path)
        if variant_key == original_key:
            raise AvatarError("variant path 与原 Workflow 相同；拒绝运行原文件")
        expected_dir = _windows_path_key(transaction["variant_dir"])
        try:
            common = ntpath.commonpath([expected_dir, variant_key])
        except ValueError as exc:
            raise AvatarError("variant path 不在 transaction 专属目录") from exc
        if common != expected_dir or not variant_key.lower().endswith(".json"):
            raise AvatarError("variant path 不在 transaction 专属目录或不是 JSON")

        payload, observed = _parse_observed_slots(observed_slots_json)
        payload_path = str(payload.get("workflow", ""))
        if not payload_path or _windows_path_key(payload_path) != variant_key:
            raise AvatarError("list_workflow_slots 返回的 workflow path 与 variant 不一致")
        workflow = workflow_registry(config)["workflows"][transaction["workflow_id"]]
        allowed_addresses = visual_system.declared_parameter_addresses(workflow)
        expected = {
            item["address"]: item["value"] for item in transaction["slot_overrides"]
            if item.get("address") in allowed_addresses
        }
        input_binding = workflow.get("parameter_bindings", {}).get("input_image", {})
        input_address = input_binding.get("address")
        if input_address in expected and uploaded_filename:
            expected[input_address] = uploaded_filename
        mismatches = []
        for address, value in expected.items():
            if address not in observed or not _slot_values_equal(value, observed.get(address)):
                mismatches.append(
                    {"address": address, "expected": value, "observed": observed.get(address)}
                )
        if mismatches:
            raise AvatarError(
                "variant slot verification mismatch；禁止 run_workflow："
                + json.dumps(mismatches, ensure_ascii=False, separators=(",", ":"))
            )
        verification = {
            "verified_at": utc_now(),
            "workflow": variant_workflow_path,
            "expected": expected,
            "observed": {address: observed[address] for address in expected},
            "matched": True,
        }
        transaction.update(
            status="variant_verified",
            variant_workflow_path=variant_workflow_path,
            variant_verification=verification,
            job_status="ready_to_submit",
        )
        atomic_write_json(transaction_path(paths, transaction_id), transaction)
        append_jsonl(
            paths["history"],
            {
                "at": utc_now(), "event": "transaction_variant_verified",
                "transaction_id": transaction_id, "revision": transaction["base_revision"],
                "workflow_id": transaction["workflow_id"], "target": transaction["target"],
                "original_workflow_path": transaction["original_workflow_path"],
                "variant_workflow_path": variant_workflow_path,
                "slot_verification": "matched",
                "generation_status": "not_submitted", "delivery_status": "not_attempted",
            },
        )
    return {"ok": True, "idempotent": False, "transaction": transaction}



def cmd_verify_api_variant(
    config: dict[str, Any],
    transaction_id: str,
    variant_workflow_path: str,
    remote_sha256: str,
) -> dict[str, Any]:
    """Verify a transaction-scoped API-format workflow staged by the trusted executor.

    The trusted executor performs byte-for-byte SCP round-trip verification first.
    This durable gate independently rebuilds the expected local API variant and
    requires the reported remote digest to match before granting variant_verified.
    """
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        transaction = load_transaction(paths, transaction_id)
        if transaction.get("prompt_id") or int(transaction.get("submit_count", 0)):
            raise AvatarError("transaction already entered submit stage; API variant cannot be reverified")
        if transaction.get("status") == "variant_verified":
            if _windows_path_key(transaction.get("variant_workflow_path", "")) != _windows_path_key(variant_workflow_path):
                raise AvatarError("transaction already verified another API variant path")
            verification = transaction.get("variant_verification", {})
            if verification.get("sha256") != remote_sha256:
                raise AvatarError("idempotent API variant verification SHA256 mismatch")
            return {"ok": True, "idempotent": True, "transaction": transaction}
        if transaction.get("status") != "planned":
            raise AvatarError(f"transaction status cannot verify API variant: {transaction.get('status')}")

        original_key = _windows_path_key(transaction["original_workflow_path"])
        variant_key = _windows_path_key(variant_workflow_path)
        if variant_key == original_key:
            raise AvatarError("API variant path equals original workflow; refusing original mutation")
        expected_dir = _windows_path_key(transaction["variant_dir"])
        try:
            common = ntpath.commonpath([expected_dir, variant_key])
        except ValueError as exc:
            raise AvatarError("API variant path is outside transaction directory") from exc
        if common != expected_dir or not variant_key.lower().endswith(".json"):
            raise AvatarError("API variant path is outside transaction directory or is not JSON")

        expected = _build_everyone_api_variant(config, transaction)
        if _windows_path_key(expected["variant_workflow_path"]) != variant_key:
            raise AvatarError("API variant path does not match deterministic expected path")
        if not re.fullmatch(r"[0-9a-f]{64}", str(remote_sha256 or "")):
            raise AvatarError("remote API variant SHA256 format invalid")
        if remote_sha256 != expected["sha256"]:
            raise AvatarError(
                "API variant SHA256 mismatch; refusing run_workflow: "
                f"expected={expected['sha256']} remote={remote_sha256}"
            )

        verification = {
            "verified_at": utc_now(),
            "workflow": variant_workflow_path,
            "strategy": "api_json_scp_sha256",
            "sha256": remote_sha256,
            "expected": {"45.value": expected["prompt"]},
            "identity_reference": expected["reference_filename"],
            "matched": True,
        }
        transaction.update(
            status="variant_verified",
            variant_workflow_path=variant_workflow_path,
            variant_verification=verification,
            job_status="ready_to_submit",
        )
        atomic_write_json(transaction_path(paths, transaction_id), transaction)
        append_jsonl(
            paths["history"],
            {
                "at": utc_now(),
                "event": "transaction_api_variant_verified",
                "transaction_id": transaction_id,
                "revision": transaction["base_revision"],
                "workflow_id": transaction["workflow_id"],
                "target": transaction["target"],
                "original_workflow_path": transaction["original_workflow_path"],
                "variant_workflow_path": variant_workflow_path,
                "variant_sha256": remote_sha256,
                "variant_strategy": "api_json_scp_sha256",
                "generation_status": "not_submitted",
                "delivery_status": "not_attempted",
            },
        )
    return {"ok": True, "idempotent": False, "transaction": transaction}


def cmd_claim_submit(config: dict[str, Any], transaction_id: str) -> dict[str, Any]:
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        transaction = load_transaction(paths, transaction_id)
        if transaction.get("prompt_id") or int(transaction.get("submit_count", 0)):
            raise AvatarError("transaction 已消费唯一 submit 权限；禁止再次 run_workflow")
        if transaction.get("status") != "variant_verified":
            raise AvatarError("variant 尚未验证通过；禁止 run_workflow")
        if not transaction.get("variant_workflow_path"):
            raise AvatarError("transaction 缺少 variant_workflow_path")
        transaction.update(
            status="submit_claimed", submit_count=1,
            submit_claimed_at=utc_now(), job_status="submit_claimed",
        )
        atomic_write_json(transaction_path(paths, transaction_id), transaction)
        append_jsonl(
            paths["history"],
            {
                "at": utc_now(), "event": "transaction_submit_claimed",
                "transaction_id": transaction_id, "revision": transaction["base_revision"],
                "workflow_id": transaction["workflow_id"], "target": transaction["target"],
                "variant_workflow_path": transaction["variant_workflow_path"],
                "submit_count": 1,
                "generation_status": "submit_claimed", "delivery_status": "not_attempted",
            },
        )
    return {
        "ok": True, "transaction": transaction,
        "run_workflow": {
            "workflow_path": transaction["variant_workflow_path"],
            "wait": False,
            "confirm_spend": False,
        },
        "agent_action": "run_workflow_once_now",
    }


def cmd_bind(config: dict[str, Any], transaction_id: str, prompt_id: str) -> dict[str, Any]:
    prompt_id = prompt_id.strip()
    if not prompt_id:
        raise AvatarError("prompt_id 不能为空")
    if prompt_id == transaction_id:
        raise AvatarError("prompt_id 与 transaction_id 是不同标识，禁止复用")
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        transaction = load_transaction(paths, transaction_id)
        existing = str(transaction.get("prompt_id", ""))
        if existing:
            if existing != prompt_id:
                raise AvatarError("transaction 已绑定另一个 prompt_id，禁止重新提交")
            return {"ok": True, "idempotent": True, "transaction": transaction}
        if transaction.get("status") != "submit_claimed":
            raise AvatarError(f"transaction 状态不允许 bind：{transaction.get('status')}")
        if int(transaction.get("submit_count", 0)) != 1:
            raise AvatarError("transaction submit_count 不为 1；拒绝绑定")
        if not transaction.get("variant_workflow_path") or not transaction.get("variant_verification", {}).get("matched"):
            raise AvatarError("transaction variant 未验证；拒绝绑定")
        transaction.update(
            status="submitted", prompt_id=prompt_id,
            submitted_at=utc_now(), job_status="submitted",
        )
        atomic_write_json(transaction_path(paths, transaction_id), transaction)
        append_jsonl(
            paths["history"],
            {
                "at": utc_now(), "event": "transaction_submitted",
                "transaction_id": transaction_id, "revision": transaction["base_revision"],
                "workflow_id": transaction["workflow_id"], "target": transaction["target"],
                "prompt_id": prompt_id, "generation_status": "submitted",
                "variant_workflow_path": transaction["variant_workflow_path"],
                "submit_count": transaction["submit_count"],
                "delivery_status": "not_attempted"
            },
        )
    return {"ok": True, "idempotent": False, "transaction": transaction, "agent_action": "resume_bound_job_only"}


def cmd_abort(config: dict[str, Any], transaction_id: str, reason: str) -> dict[str, Any]:
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        transaction = load_transaction(paths, transaction_id)
        status = transaction.get("status")
        stale_seconds = int(config["runtime"].get("job_stale_seconds", 1200))
        age_seconds = max(0.0, time.time() - float(transaction.get("created_epoch", 0)))
        stale_unbound_claim = (
            status == "submit_claimed"
            and not transaction.get("prompt_id")
            and int(transaction.get("submit_count", 0)) == 1
            and age_seconds >= stale_seconds
        )
        if transaction.get("prompt_id") or status in {
            "submitted", "waiting", "fetch_pending", "generation_completed"
        }:
            raise AvatarError("transaction 已绑定 prompt_id，禁止 abort/重提；请查询并恢复原 job")
        if status == "submit_claimed" and not stale_unbound_claim:
            raise AvatarError(
                f"submit claim 尚未超过 stale 阈值（{stale_seconds}s），禁止解除"
            )
        if status == "aborted":
            return {"ok": True, "idempotent": True, "transaction": transaction}
        if status not in {"planned", "variant_verified", "submit_claimed"}:
            raise AvatarError(f"transaction 状态不允许 abort：{status}")
        transaction.update(
            status="aborted",
            job_status="aborted",
            aborted_at=utc_now(),
            abort_reason=reason,
            stale_submit_claim_released=stale_unbound_claim,
        )
        atomic_write_json(transaction_path(paths, transaction_id), transaction)
        append_jsonl(
            paths["history"],
            {
                "at": utc_now(), "event": "transaction_aborted",
                "transaction_id": transaction_id, "revision": transaction["base_revision"],
                "workflow_id": transaction["workflow_id"], "target": transaction["target"],
                "generation_status": (
                    "stale_unbound_submit_claim_released"
                    if stale_unbound_claim else "not_submitted"
                ),
                "delivery_status": "not_attempted",
                "reason": reason,
                "stale_submit_claim_released": stale_unbound_claim,
                "possible_unbound_submission": stale_unbound_claim,
            },
        )
    return {"ok": True, "idempotent": False, "transaction": transaction}



def cmd_mark_pending_completion(
    config: dict[str, Any], transaction_id: str, prompt_id: str
) -> dict[str, Any]:
    """Persist a submitted exactly-once job as still running, never as failed."""
    prompt_id = prompt_id.strip()
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        transaction = load_transaction(paths, transaction_id)
        if transaction.get("prompt_id") != prompt_id:
            raise AvatarError("pending prompt_id 与 transaction 绑定值不一致")
        if transaction.get("status") not in {"submitted", "waiting", "fetch_pending"}:
            raise AvatarError(f"transaction 状态不允许标记 pending：{transaction.get('status')}")
        if int(transaction.get("submit_count", 0)) != 1:
            raise AvatarError("pending transaction submit_count 不为 1")
        if transaction.get("status") == "waiting" and transaction.get("generation_status") == "pending_completion":
            return {
                "ok": True,
                "pending": True,
                "idempotent": True,
                "transaction": transaction,
                "agent_action": "stop_silently",
            }
        transaction.update(
            status="waiting",
            job_status="pending_completion",
            generation_status="pending_completion",
            wait_deadline_exceeded_at=utc_now(),
        )
        atomic_write_json(transaction_path(paths, transaction_id), transaction)
        append_jsonl(
            paths["history"],
            {
                "at": utc_now(),
                "event": "transaction_pending_completion",
                "transaction_id": transaction_id,
                "revision": transaction["base_revision"],
                "workflow_id": transaction["workflow_id"],
                "target": transaction["target"],
                "prompt_id": prompt_id,
                "submit_count": transaction["submit_count"],
                "generation_status": "pending_completion",
                "delivery_status": transaction.get("delivery_status", "not_attempted"),
            },
        )
    return {
        "ok": True,
        "pending": True,
        "idempotent": False,
        "transaction": transaction,
        "agent_action": "stop_silently",
    }


def cmd_mark_completed(config: dict[str, Any], transaction_id: str, prompt_id: str) -> dict[str, Any]:
    prompt_id = prompt_id.strip()
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        transaction = load_transaction(paths, transaction_id)
        if transaction.get("prompt_id") != prompt_id:
            raise AvatarError("completed prompt_id 与 transaction 绑定值不一致")
        if transaction.get("job_status") == "completed":
            return {"ok": True, "idempotent": True, "transaction": transaction}
        if transaction.get("status") not in {"submitted", "waiting", "fetch_pending"}:
            raise AvatarError(f"transaction 状态不允许标记 completed：{transaction.get('status')}")
        transaction.update(
            status="generation_completed", job_status="completed", generation_completed_at=utc_now()
        )
        atomic_write_json(transaction_path(paths, transaction_id), transaction)
        append_jsonl(
            paths["history"],
            {
                "at": utc_now(), "event": "transaction_generation_completed",
                "transaction_id": transaction_id, "revision": transaction["base_revision"],
                "workflow_id": transaction["workflow_id"], "target": transaction["target"],
                "prompt_id": prompt_id,
                "variant_workflow_path": transaction["variant_workflow_path"],
                "generation_status": "completed", "delivery_status": "not_attempted",
            },
        )
    return {"ok": True, "idempotent": False, "transaction": transaction}


def verified_local_image(result_image: str) -> tuple[Path, bytes, str]:
    raw = str(result_image or "").strip()
    if raw.startswith("MEDIA:"):
        raw = raw[6:].strip()
    if re.match(r"^[A-Za-z]:[\\/]", raw) or ("\\" in raw and not raw.startswith("/")):
        raise AvatarError("result_image 是 Windows/MCP 主机路径，不是 Hermes Linux 本地文件")
    expanded = Path(os.path.expanduser(raw))
    if not expanded.is_absolute():
        raise AvatarError("result_image 必须是 Hermes Linux 绝对路径或 MEDIA:/absolute/path")
    try:
        source = expanded.resolve(strict=True)
    except OSError as exc:
        raise AvatarError(f"MCP 本地缓存图片不存在：{expanded}") from exc
    if not source.is_file():
        raise AvatarError(f"MCP 本地缓存图片不是普通文件：{source}")
    mode = source.stat().st_mode
    if not mode & (stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH) or not os.access(source, os.R_OK):
        raise AvatarError(f"MCP 本地缓存图片不可读：{source}")
    if source.stat().st_size <= 0:
        raise AvatarError(f"MCP 本地缓存图片为空：{source}")
    data = source.read_bytes()
    suffix = validate_image_bytes(data)
    return source, data, suffix


def cmd_commit(
    config: dict[str, Any], transaction_id: str, prompt_id: str,
    result_image: str, remote_result_image: str,
) -> dict[str, Any]:
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        transaction = load_transaction(paths, transaction_id)
        if transaction.get("commit_status") == "committed" or transaction.get("status") in {
            "committed", "delivered", "pending_delivery"
        }:
            return {
                "ok": True, "idempotent": True, "generation_committed": True,
                "transaction": transaction, "agent_action": "stop_silently"
            }
    source, data, suffix = verified_local_image(result_image)
    with exclusive_lock(paths["lock"]):
        transaction = load_transaction(paths, transaction_id)
        if transaction.get("commit_status") == "committed" or transaction.get("status") in {
            "committed", "delivered", "pending_delivery"
        }:
            return {
                "ok": True, "idempotent": True, "generation_committed": True,
                "transaction": transaction, "agent_action": "stop_silently"
            }
        bound = str(transaction.get("prompt_id", ""))
        if not bound:
            raise AvatarError("transaction 尚未 bind；必须先绑定 MCP prompt_id")
        if bound != prompt_id:
            raise AvatarError("commit prompt_id 与 transaction 绑定值不一致")
        if transaction.get("job_status") != "completed" or transaction.get("status") != "generation_completed":
            raise AvatarError("job 尚未明确 completed；禁止 commit")
        if int(transaction.get("submit_count", 0)) != 1:
            raise AvatarError("transaction submit_count 不为 1；禁止 commit")
        if not transaction.get("variant_workflow_path") or not transaction.get("variant_verification", {}).get("matched"):
            raise AvatarError("transaction variant 未验证；禁止 commit")
        state = load_json(paths["state"])
        if int(state["continuity"].get("revision", 0)) != int(transaction["base_revision"]):
            transaction.update(
                status="state_conflict", result_image=str(source),
                remote_result_image=remote_result_image, conflict_at=utc_now()
            )
            atomic_write_json(transaction_path(paths, transaction_id), transaction)
            raise AvatarError("生成成功但 state revision 已变化；已保留原 job/result，禁止重提")
        revision = int(transaction["base_revision"]) + 1
        timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        final_image = paths["output"] / f"yinyue-{timestamp}-r{revision:04d}{suffix}"
        write_image(final_image, data)
        candidate = copy.deepcopy(transaction["candidate_state"])
        remote_images = copy.deepcopy(state["continuity"].get("remote_images", {}))
        if remote_result_image:
            remote_images[transaction["target"]] = {
                "path": remote_result_image, "prompt_id": prompt_id,
                "transaction_id": transaction_id, "updated_at": utc_now()
            }
        candidate["continuity"].update(
            {
                "revision": revision, "updated_at": utc_now(),
                "last_image": str(final_image.resolve()),
                "last_prompt_sha256": hashlib.sha256(
                    str(transaction["parameters"].get("prompt", "")).encode("utf-8")
                ).hexdigest(),
                "last_prompt_id": prompt_id, "last_workflow_id": transaction["workflow_id"],
                "last_target": transaction["target"], "last_transaction_id": transaction_id,
                "remote_images": remote_images, "delivery_status": "not_attempted"
            }
        )
        validate_state(candidate)
        event = {
            "at": utc_now(), "event": "transaction_committed",
            "transaction_id": transaction_id, "revision": revision,
            "user_intent": transaction["intent"], "workflow_id": transaction["workflow_id"],
            "target": transaction["target"],
            "parameter_summary": [
                item for item in transaction["parameter_summary"] if item != "seed"
            ],
            "requested_parameters": {
                key: value for key, value in transaction.get(
                    "requested_parameters", transaction["parameters"]
                ).items() if key != "seed"
            },
            "submit_count": transaction["submit_count"],
            "original_workflow_path": transaction["original_workflow_path"],
            "variant_workflow_path": transaction["variant_workflow_path"],
            "slot_verification": transaction.get("variant_verification", {}),
            "state_changes": transaction["changes"], "prompt_id": prompt_id,
            "source_image": transaction.get("source_image", ""),
            "result_image": str(final_image.resolve()), "remote_result_image": remote_result_image,
            "generation_status": "completed", "delivery_status": "not_attempted"
        }
        atomic_write_json(paths["state"], candidate)
        append_jsonl(paths["history"], event)
        transaction.update(
            status="committed", committed_at=utc_now(), committed_revision=revision,
            result_image=str(final_image.resolve()), remote_result_image=remote_result_image,
            result={
                "local_image": str(final_image.resolve()),
                "remote_image": remote_result_image,
            },
            generation_status="completed", commit_status="committed",
            delivery_status="not_attempted",
        )
        atomic_write_json(transaction_path(paths, transaction_id), transaction)
    if transaction.get("no_send"):
        delivery = {"ok": True, "attempted": False, "skipped": True}
    else:
        delivery = deliver_text_and_media(
            say=transaction.get("say", ""), image=final_image,
            channel=transaction.get("channel", config["telegram"]["default_channel"]), config=config
        )
    if delivery.get("skipped"):
        final_status = "committed"
        delivery_status = "skipped"
    elif delivery.get("ok"):
        final_status = "delivered"
        delivery_status = "delivered"
    else:
        final_status = "pending_delivery"
        delivery_status = "pending_delivery"
    with exclusive_lock(paths["lock"]):
        transaction = load_transaction(paths, transaction_id)
        transaction.update(
            status=final_status, delivery=delivery, delivery_status=delivery_status,
            delivery_completed_at=utc_now(),
        )
        atomic_write_json(transaction_path(paths, transaction_id), transaction)
        state = load_json(paths["state"])
        if state["continuity"].get("last_transaction_id") == transaction_id:
            state["continuity"]["delivery_status"] = delivery_status
            atomic_write_json(paths["state"], state)
        append_jsonl(
            paths["history"],
            {
                "at": utc_now(), "event": "transaction_delivery",
                "transaction_id": transaction_id, "revision": transaction["committed_revision"],
                "workflow_id": transaction["workflow_id"], "target": transaction["target"],
                "prompt_id": prompt_id, "result_image": transaction["result_image"],
                "generation_status": "completed", "delivery_status": delivery_status
            },
        )
    return {
        "ok": True, "idempotent": False, "generation_committed": True,
        "delivery": delivery, "transaction": transaction, "agent_action": "stop_silently"
    }


def cmd_workflows(config: dict[str, Any]) -> dict[str, Any]:
    registry = workflow_registry(config)
    rows = [
        {
            "id": workflow["id"], "purpose": workflow["purpose"],
            "description": workflow["description"],
            "preferred_target": workflow.get("preferred_target"),
            "targets": workflow["target_status"]
        }
        for workflow in registry["workflows"].values()
    ]
    return {"ok": True, "default_target": config["execution"]["default_target"], "workflows": rows}


def cmd_workflow_info(config: dict[str, Any], workflow_id: str) -> dict[str, Any]:
    workflow = workflow_registry(config)["workflows"].get(workflow_id)
    if not workflow:
        raise AvatarError(f"未知 workflow：{workflow_id}")
    return {"ok": True, "workflow": workflow}


def _primary_text_workflow(registry: dict[str, Any]) -> dict[str, Any]:
    matches = [
        workflow
        for workflow in registry["workflows"].values()
        if workflow.get("purpose") == "text_to_image"
    ]
    if not matches:
        raise AvatarError("registry 中没有 text_to_image Workflow")
    preferred = [
        workflow
        for workflow in matches
        if "show_current" in workflow.get("capabilities", [])
    ]
    return sorted(
        preferred or matches, key=lambda item: str(item.get("id", ""))
    )[0]


def _ordered_mcp_targets(registry: dict[str, Any]) -> list[str]:
    return sorted(
        registry["targets"],
        key=lambda target: (
            int(registry["targets"][target].get("preferred_rank", 10_000)),
            target,
        ),
    )


def normalize_mcp_target(value: str, registry: dict[str, Any]) -> str:
    raw = str(value or "").strip().lower()
    aliases: dict[str, str] = {}
    for target in registry["targets"]:
        normalized = target.lower()
        aliases[normalized] = target
        if normalized.startswith("comfy_"):
            aliases[normalized.removeprefix("comfy_")] = target
    selected = aliases.get(raw, "")
    if selected:
        return selected
    choices = [
        target.removeprefix("comfy_") for target in _ordered_mcp_targets(registry)
    ]
    raise AvatarError("未知 MCP 目标；可选值：" + "、".join(choices))


def _mcp_target_payload(
    config: dict[str, Any], *, updated: bool = False
) -> dict[str, Any]:
    registry = workflow_registry(config)
    workflow = _primary_text_workflow(registry)
    configured_targets = set(
        config.get("execution", {}).get("allowed_targets", [])
    )
    workflow_targets = set(workflow.get("allowed_targets", []))
    options = []
    for target in _ordered_mcp_targets(registry):
        target_config = registry["targets"][target]
        target_status = workflow.get("target_status", {}).get(target, {})
        verified = bool(target_status.get("workflow_exists")) and bool(
            target_status.get("interface_verified")
        )
        runtime_preflight = bool(target_status.get("runtime_preflight_allowed"))
        enabled = bool(target_config.get("enabled", True))
        configured = target in configured_targets
        workflow_allowed = target in workflow_targets
        has_remote_path = bool(workflow.get("remote_paths", {}).get(target))
        selectable = (
            enabled
            and configured
            and workflow_allowed
            and has_remote_path
            and (verified or runtime_preflight)
        )
        options.append(
            {
                "label": target.removeprefix("comfy_"),
                "target": target,
                "selected": target == config["execution"]["default_target"],
                "enabled": enabled,
                "configured": configured,
                "workflow_allowed": workflow_allowed,
                "selectable": selectable,
                "readiness": (
                    "verified"
                    if verified
                    else "runtime_preflight"
                    if runtime_preflight
                    else "unavailable"
                ),
                "reason": str(target_status.get("reason", "")),
            }
        )
    return {
        "ok": True,
        "updated": updated,
        "default_target": config["execution"]["default_target"],
        "workflow_id": workflow["id"],
        "options": options,
    }


def cmd_mcp_target(config: dict[str, Any], selection: str = "") -> dict[str, Any]:
    """Show or persist the strict MCP target used by future transactions."""
    if not selection:
        return _mcp_target_payload(config)

    registry = workflow_registry(config)
    target = normalize_mcp_target(selection, registry)
    current_payload = _mcp_target_payload(config)
    option = next(
        item for item in current_payload["options"] if item["target"] == target
    )
    if not option["selectable"]:
        detail = (
            option["reason"]
            or "目标未启用、未验证或不被当前 Workflow 允许"
        )
        raise AvatarError(f"MCP 目标不可选择：{target}；{detail}")

    state_root = Path(os.path.expanduser(config["runtime"]["state_dir"]))
    state_root.mkdir(parents=True, exist_ok=True)
    backup = ""
    with exclusive_lock(state_root / "config.lock"):
        if LOCAL_CONFIG_PATH.is_symlink():
            raise AvatarError("config.local.json 不得是符号链接")
        if LOCAL_CONFIG_PATH.is_file():
            local_config = load_json(LOCAL_CONFIG_PATH)
            if not isinstance(local_config, dict):
                raise AvatarError("config.local.json 必须是 JSON 对象")
        else:
            local_config = {}

        local_execution = local_config.get("execution")
        if local_execution is None:
            local_execution = {}
            local_config["execution"] = local_execution
        if not isinstance(local_execution, dict):
            raise AvatarError("config.local.json 的 execution 必须是 JSON 对象")

        base_config = load_json(CONFIG_PATH)
        if not isinstance(base_config, dict):
            raise AvatarError("config.json 必须是 JSON 对象")
        previous_target = str(
            deep_merge(base_config, local_config)["execution"]["default_target"]
        )
        if target == previous_target:
            effective = copy.deepcopy(config)
            effective["execution"]["default_target"] = target
            result = _mcp_target_payload(effective, updated=False)
            result.update(previous_target=previous_target, backup="")
            return result

        if LOCAL_CONFIG_PATH.is_file():
            backup_dir = state_root / "config-backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_path = backup_dir / (
                f"config.local-before-mcp-{stamp}-{uuid.uuid4().hex[:8]}.json"
            )
            shutil.copy2(LOCAL_CONFIG_PATH, backup_path)
            os.chmod(backup_path, 0o600)
            backup = str(backup_path)

        local_execution["default_target"] = target
        atomic_write_json(LOCAL_CONFIG_PATH, local_config)

    effective = copy.deepcopy(config)
    effective["execution"]["default_target"] = target
    result = _mcp_target_payload(effective, updated=True)
    result.update(previous_target=previous_target, backup=backup)
    return result


def cmd_transaction_status(config: dict[str, Any], transaction_id: str) -> dict[str, Any]:
    return load_transaction(ensure_runtime(config), transaction_id)


def cmd_mcp_status(config: dict[str, Any]) -> dict[str, Any]:
    registry = workflow_registry(config)
    hermes_cli = resolve_hermes_cli(config)
    try:
        result = subprocess.run(
            [hermes_cli, "mcp", "list"], text=True, capture_output=True,
            timeout=15, check=False
        )
        output = result.stdout + result.stderr
        configured = {target: target in output for target in config["execution"]["allowed_targets"]}
        command_ok = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        configured = {target: False for target in config["execution"]["allowed_targets"]}
        command_ok = False
        output = str(exc)
    return {
        "ok": command_ok and all(configured.values()), "configured_targets": configured,
        "default_target": config["execution"]["default_target"],
        "registry_targets": registry["targets"], "detail": output[-4000:],
        "note": "connection and workflow readiness require MCP tool calls by the Agent"
    }


def cmd_context(config: dict[str, Any]) -> dict[str, Any]:
    paths = ensure_runtime(config)
    state = load_json(paths["state"])
    persona = (SKILL_ROOT / "persona.md").read_text(encoding="utf-8")
    recent_config = config["recent_history"]
    history = meaningful_recent_history(
        paths["history"],
        max_events=int(recent_config["max_events"]),
        max_chars=int(recent_config["max_chars"]),
        scan_max_lines=int(recent_config["scan_max_lines"]),
    )
    lore_path = SKILL_ROOT / "lore/summary.md"
    lore_summary = lore_path.read_text(encoding="utf-8") if lore_path.is_file() else ""
    memory_summary = load_json(paths["memory_summary"])
    return {
        "ok": True,
        "persona": persona,
        "lore_summary": lore_summary,
        "relationship": state["relationship"],
        "scene": state["scene"],
        "emotion": state["emotion"],
        "memory_summary": memory_summary,
        "state": state,
        "status": visual_status(state),
        "recent_history": history["events"],
        "recent_history_truncated": history["truncated"],
        "recent_history_skipped_corrupt_lines": history[
            "skipped_corrupt_lines"
        ],
    }


def tail_jsonl(path: Path, count: int) -> list[Any]:
    if not path.is_file() or count <= 0:
        return []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        data = b""
        while position > 0 and data.count(b"\n") <= count:
            size = min(4096, position)
            position -= size
            handle.seek(position)
            data = handle.read(size) + data
    lines = data.decode("utf-8").splitlines()[-count:]
    return [json.loads(line) for line in lines if line.strip()]


def cmd_status(config: dict[str, Any]) -> dict[str, Any]:
    return load_json(ensure_runtime(config)["state"])


def cmd_history(config: dict[str, Any], last: int) -> list[Any]:
    path = ensure_runtime(config)["history"]
    return tail_jsonl(path, last)


def commit_candidate(
    paths: dict[str, Path],
    state: dict[str, Any],
    candidate: dict[str, Any],
    event_name: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    validate_state(candidate)
    revision = int(state["continuity"].get("revision", 0)) + 1
    candidate["continuity"]["revision"] = revision
    candidate["continuity"]["updated_at"] = utc_now()
    candidate["continuity"]["source_revision"] = int(
        state["continuity"].get("revision", 0)
    )
    event = {
        "at": utc_now(),
        "event": event_name,
        "revision": revision,
        **details,
    }
    atomic_write_json(paths["state"], candidate)
    append_jsonl(paths["history"], event)
    return event


def cmd_transition(config: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        state = load_json(paths["state"])
        candidate = apply_transition(state, spec)
        event = commit_candidate(
            paths, state, candidate, "transition", {"transition": spec}
        )
    return {"ok": True, "event": event, "state": candidate}


def cmd_reset(config: dict[str, Any], kind: str) -> dict[str, Any]:
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        state = load_json(paths["state"])
        if kind == "reset-scene":
            candidate = reset_scene(state)
        else:
            default_state = load_json(SKILL_ROOT / "defaults/state.default.json")
            candidate = reset_runtime_state(state, default_state)
        event = commit_candidate(paths, state, candidate, kind, {})
    return {"ok": True, "event": event, "state": candidate}


def cmd_factory_reset(
    config: dict[str, Any], *, apply: bool, confirm: str
) -> dict[str, Any]:
    paths = ensure_runtime(config)
    preview = {
        "ok": True,
        "dry_run": not apply,
        "would_reset": ["relationship", "memory", "scene", "appearance", "physical", "emotion"],
        "state": str(paths["state"]),
    }
    if not apply:
        return preview
    if confirm != "FACTORY-RESET":
        raise AvatarError("--apply requires --confirm FACTORY-RESET")
    backup = paths["baseline_backups"] / f"factory-reset-{int(time.time())}.json"
    with exclusive_lock(paths["lock"]):
        state = load_json(paths["state"])
        shutil.copy2(paths["state"], backup)
        candidate = load_json(SKILL_ROOT / "defaults/state.default.json")
        event = commit_candidate(
            paths,
            state,
            candidate,
            "factory_reset",
            {"backup": str(backup)},
        )
    return {"ok": True, "dry_run": False, "backup": str(backup), "event": event}


def load_relationship_payload(
    *, json_text: str = "", from_file: str = ""
) -> tuple[dict[str, Any], str]:
    if bool(json_text) == bool(from_file):
        raise AvatarError("provide exactly one of --json or --from-file")
    if from_file:
        source_path = Path(from_file)
        if not source_path.is_absolute():
            raise AvatarError("--from-file must be an absolute path")
        try:
            value = load_json(source_path)
        except (OSError, json.JSONDecodeError) as exc:
            raise AvatarError(f"cannot read relationship JSON: {exc}") from exc
        source = f"file:{source_path}"
    else:
        try:
            value = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise AvatarError(f"invalid relationship JSON: {exc}") from exc
        source = "inline-json"
    if not isinstance(value, dict):
        raise AvatarError("relationship input must be a JSON object")
    return value, source


def sync_relationship_compatibility(
    candidate: dict[str, Any], relationship: dict[str, Any]
) -> None:
    internal = candidate["internal"]
    if "legacy_relationship_stage" in relationship:
        internal["relationship_stage"] = copy.deepcopy(
            relationship["legacy_relationship_stage"]
        )
    elif "stage" in relationship:
        internal["relationship_stage"] = copy.deepcopy(relationship["stage"])
    if "legacy_affection" in relationship:
        internal["affection"] = copy.deepcopy(relationship["legacy_affection"])
    elif "intimacy" in relationship:
        internal["affection"] = copy.deepcopy(relationship["intimacy"])
    if "trust" in relationship:
        internal["trust"] = copy.deepcopy(relationship["trust"])


def create_relationship_backup(
    paths: dict[str, Path], state: dict[str, Any], source: str
) -> Path:
    backup_id = (
        f"relationship-r{int(state['continuity'].get('revision', 0)):04d}-"
        f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    )
    backup_path = paths["relationship_backups"] / f"{backup_id}.json"
    atomic_write_json(
        backup_path,
        {
            "created_at": utc_now(),
            "state_revision": int(state["continuity"].get("revision", 0)),
            "relationship": copy.deepcopy(state["relationship"]),
            "source": source,
        },
    )
    os.chmod(backup_path, 0o600)
    return backup_path


def replace_relationship(
    config: dict[str, Any],
    relationship: dict[str, Any],
    *,
    apply: bool,
    bypass_schema: bool,
    confirm: str,
    input_source: str,
    event_type: str = "author_relationship_overwrite",
    relationship_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if bypass_schema and confirm != "AUTHOR-BREAK-GLASS":
        raise AvatarError(
            "--bypass-schema requires --confirm AUTHOR-BREAK-GLASS"
        )
    if not isinstance(relationship, dict):
        raise AvatarError("relationship input must be a JSON object")
    if not apply:
        return {
            "ok": True,
            "dry_run": True,
            "schema_bypassed": bypass_schema,
            "input_source": input_source,
        }
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        state = load_json(paths["state"])
        effective_relationship = (
            {
                **copy.deepcopy(state["relationship"]),
                **copy.deepcopy(relationship_patch),
            }
            if relationship_patch is not None
            else copy.deepcopy(relationship)
        )
        backup_source = {
            "author_relationship_overwrite": "relationship-overwrite",
            "author_relationship_restore": "relationship-restore",
            "relationship_update": "relationship-update",
        }.get(event_type, event_type)
        backup_path = create_relationship_backup(paths, state, backup_source)
        if not bypass_schema:
            try:
                validate_relationship(effective_relationship)
            except StateValidationError as exc:
                raise AvatarError(str(exc)) from exc
        candidate = copy.deepcopy(state)
        before = copy.deepcopy(state["relationship"])
        # Overwrite passes no patch, so this is exact assignment. The separate
        # relationship-update command constructs its partial result under lock.
        candidate["relationship"] = copy.deepcopy(effective_relationship)
        sync_relationship_compatibility(candidate, effective_relationship)
        try:
            validate_state(candidate)
            if not bypass_schema:
                validate_relationship(candidate["relationship"])
        except StateValidationError as exc:
            raise AvatarError(str(exc)) from exc
        revision_before = int(state["continuity"].get("revision", 0))
        revision_after = revision_before + 1
        timestamp = utc_now()
        candidate["continuity"]["revision"] = revision_after
        candidate["continuity"]["updated_at"] = timestamp
        audit_event = {
            "at": timestamp,
            "event": event_type,
            "event_type": event_type,
            "revision": revision_after,
            "revision_before": revision_before,
            "revision_after": revision_after,
            "schema_bypassed": bypass_schema,
            "backup_path": str(backup_path),
            "changed_fields": {
                "before": before,
                "after": copy.deepcopy(effective_relationship),
            },
            "input_source": input_source,
            "timestamp": timestamp,
        }
        atomic_write_json(paths["state"], candidate)
        append_jsonl(paths["history"], audit_event)
    return {
        "ok": True,
        "dry_run": False,
        "event_type": event_type,
        "revision_before": revision_before,
        "revision_after": revision_after,
        "schema_bypassed": bypass_schema,
        "backup_path": str(backup_path),
        "input_source": input_source,
    }


def cmd_relationship_update(
    config: dict[str, Any], patch: dict[str, Any], *, apply: bool
) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise AvatarError("relationship update must be a JSON object")
    unknown = set(patch) - RELATIONSHIP_ALLOWED
    if unknown:
        raise AvatarError(f"unknown relationship fields: {sorted(unknown)}")
    return replace_relationship(
        config,
        {},
        apply=apply,
        bypass_schema=False,
        confirm="",
        input_source="relationship-update:inline-json",
        event_type="relationship_update",
        relationship_patch=patch,
    )


def cmd_relationship_backups(config: dict[str, Any]) -> dict[str, Any]:
    paths = ensure_runtime(config)
    backups = []
    for path in sorted(
        paths["relationship_backups"].glob("*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ):
        try:
            value = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        backups.append(
            {
                "backup_id": path.stem,
                "path": str(path),
                "created_at": value.get("created_at", ""),
                "state_revision": value.get("state_revision"),
                "source": value.get("source", ""),
            }
        )
    return {"ok": True, "backups": backups}


def resolve_relationship_backup(paths: dict[str, Path], raw: str) -> Path:
    backup_root = paths["relationship_backups"]
    if backup_root.is_symlink():
        raise AvatarError("relationship backup root must not be a symbolic link")
    backup_root_resolved = backup_root.resolve(strict=True)
    candidate = Path(raw)
    if candidate.is_absolute():
        path = candidate
    else:
        name = candidate.name
        if name != raw or name in {"", ".", ".."}:
            raise AvatarError("invalid relationship backup id")
        path = paths["relationship_backups"] / (
            name if name.endswith(".json") else f"{name}.json"
        )
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise AvatarError(f"relationship backup not found: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise AvatarError("relationship backup must not be a symbolic link")
    if not stat.S_ISREG(metadata.st_mode):
        raise AvatarError("relationship backup must be a regular file")
    resolved = path.resolve(strict=True)
    if resolved.parent != backup_root_resolved:
        raise AvatarError(
            "relationship restore is restricted to relationship-backups"
        )
    if not resolved.is_file():
        raise AvatarError(f"relationship backup not found: {path}")
    return resolved


def cmd_relationship_restore(
    config: dict[str, Any],
    backup: str,
    *,
    apply: bool,
    bypass_schema: bool,
    confirm: str,
) -> dict[str, Any]:
    paths = ensure_runtime(config)
    backup_path = resolve_relationship_backup(paths, backup)
    try:
        value = load_json(backup_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise AvatarError(f"invalid relationship backup: {exc}") from exc
    relationship = value.get("relationship") if isinstance(value, dict) else None
    if not isinstance(relationship, dict):
        raise AvatarError("relationship backup does not contain an object")
    return replace_relationship(
        config,
        relationship,
        apply=apply,
        bypass_schema=bypass_schema,
        confirm=confirm,
        input_source=f"relationship-backup:{backup_path}",
        event_type="author_relationship_restore",
    )


def cmd_memory_list(
    config: dict[str, Any], *, status: str, memory_type: str
) -> dict[str, Any]:
    paths = ensure_runtime(config)
    max_text, _, _ = memory_limits(config)
    events = memory_store.load_events(paths["memory_events"], max_text)
    selected = [
        event
        for event in events
        if (not status or event["status"] == status)
        and (not memory_type or event["type"] == memory_type)
    ]
    return {"ok": True, "count": len(selected), "events": selected}


def cmd_memory_show(config: dict[str, Any], memory_id: str) -> dict[str, Any]:
    paths = ensure_runtime(config)
    max_text, _, _ = memory_limits(config)
    events = memory_store.load_events(paths["memory_events"], max_text)
    try:
        event = events[memory_store.find_event(events, memory_id)]
    except memory_store.MemoryValidationError as exc:
        raise AvatarError(str(exc)) from exc
    return {"ok": True, "event": event}


def cmd_memory_add(
    config: dict[str, Any],
    *,
    memory_type: str,
    importance: int,
    text: str,
    source_event_id: str,
) -> dict[str, Any]:
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        try:
            return memory_add_locked(
                paths,
                config,
                memory_type=memory_type,
                importance=importance,
                text=text,
                source_event_id=source_event_id or None,
            )
        except memory_store.MemoryValidationError as exc:
            raise AvatarError(str(exc)) from exc


def cmd_memory_supersede(
    config: dict[str, Any],
    memory_id: str,
    *,
    text: str,
    memory_type: str,
    importance: int | None,
    source_event_id: str,
) -> dict[str, Any]:
    paths = ensure_runtime(config)
    max_text, _, _ = memory_limits(config)
    with exclusive_lock(paths["lock"]):
        try:
            old_events = memory_store.load_events(paths["memory_events"], max_text)
            timestamp = utc_now()
            candidate, event = memory_store.candidate_supersede(
                old_events,
                memory_id,
                text=text,
                memory_type=memory_type or None,
                importance=importance,
                source_event_id=source_event_id or None,
                created_at=timestamp,
                max_text_chars=max_text,
            )
            commit_memory_events_locked(
                paths,
                config,
                old_events,
                candidate,
                event_type="memory_superseded",
                event_details={
                    "event_id": event["memory_id"],
                    "memory_id": event["memory_id"],
                    "supersedes": memory_id,
                    "memory_type": event["type"],
                    "importance": event["importance"],
                    "summary": event["text"][:300],
                },
                timestamp=timestamp,
            )
        except memory_store.MemoryValidationError as exc:
            raise AvatarError(str(exc)) from exc
    return {
        "ok": True,
        "memory_id": event["memory_id"],
        "supersedes": memory_id,
    }


def cmd_memory_resolve(config: dict[str, Any], memory_id: str) -> dict[str, Any]:
    paths = ensure_runtime(config)
    max_text, _, _ = memory_limits(config)
    with exclusive_lock(paths["lock"]):
        try:
            old_events = memory_store.load_events(paths["memory_events"], max_text)
            timestamp = utc_now()
            candidate, event = memory_store.candidate_resolve(old_events, memory_id)
            commit_memory_events_locked(
                paths,
                config,
                old_events,
                candidate,
                event_type="memory_resolved",
                event_details={
                    "event_id": event["memory_id"],
                    "memory_id": event["memory_id"],
                    "memory_type": event["type"],
                    "status": event["status"],
                    "summary": event["text"][:300],
                },
                timestamp=timestamp,
            )
        except memory_store.MemoryValidationError as exc:
            raise AvatarError(str(exc)) from exc
    return {"ok": True, "memory_id": memory_id, "status": "resolved"}


def cmd_memory_rebuild_summary(config: dict[str, Any]) -> dict[str, Any]:
    paths = ensure_runtime(config)
    max_text, _, _ = memory_limits(config)
    with exclusive_lock(paths["lock"]):
        try:
            events = memory_store.load_events(paths["memory_events"], max_text)
            timestamp = utc_now()
            history_event = commit_memory_events_locked(
                paths,
                config,
                events,
                copy.deepcopy(events),
                event_type="memory_summary_rebuilt",
                event_details={"source_event_count": len(events)},
                timestamp=timestamp,
            )
        except memory_store.MemoryValidationError as exc:
            raise AvatarError(str(exc)) from exc
    return {
        "ok": True,
        "source_event_count": len(events),
        "summary_backup_path": history_event["summary_backup_path"],
    }


def cmd_update(config: dict[str, Any], patch: dict[str, Any], remember: str) -> dict[str, Any]:
    paths = ensure_runtime(config)
    with exclusive_lock(paths["lock"]):
        state = load_json(paths["state"])
        candidate = copy.deepcopy(state)
        changes = apply_patch(candidate, patch)
        sync_v2_from_legacy_patch(candidate, patch)
        revision = int(state["continuity"].get("revision", 0)) + 1
        candidate["continuity"]["revision"] = revision
        candidate["continuity"]["updated_at"] = utc_now()
        event = {
            "at": utc_now(),
            "event": "update",
            "revision": revision,
            "changes": changes,
            "memory": remember,
        }
        atomic_write_json(paths["state"], candidate)
        append_jsonl(paths["history"], event)
        if remember.strip():
            memory_add_locked(
                paths,
                config,
                memory_type="shared_experience",
                importance=3,
                text=remember,
                source_event_id=f"update-r{revision}",
            )
    return {"ok": True, "event": event, "status": visual_status(candidate)}


def sync_v2_from_legacy_patch(
    state: dict[str, Any], patch: dict[str, Any]
) -> None:
    mapping = {
        "visual.outfit": ("appearance", "outfit", "base"),
        "visual.outerwear": ("appearance", "outfit", "outerwear"),
        "visual.accessories": ("appearance", "accessories"),
        "visual.legwear": ("appearance", "outfit", "legwear"),
        "visual.footwear": ("appearance", "outfit", "footwear"),
        "visual.hair": ("appearance", "hair"),
        "visual.makeup": ("appearance", "makeup"),
        "visual.expression": ("presentation", "expression"),
        "visual.pose": ("presentation", "pose"),
        "visual.action": ("presentation", "action"),
        "visual.scene": ("scene", "location"),
        "visual.lighting": ("presentation", "lighting"),
        "visual.camera": ("presentation", "camera"),
        "internal.mood": ("emotion", "current"),
        "internal.current_activity": ("scene", "activity"),
        "internal.relationship_stage": ("relationship", "legacy_relationship_stage"),
        "internal.affection": ("relationship", "legacy_affection"),
        "internal.trust": ("relationship", "trust"),
    }
    for dotted, value in patch.items():
        target = mapping.get(dotted)
        if not target:
            continue
        current: Any = state
        for part in target[:-1]:
            current = current[part]
        if target[-1] in {"base", "outerwear", "accessories"}:
            current[target[-1]] = [value] if value else []
        else:
            current[target[-1]] = value
        if dotted == "visual.scene":
            state["scene"]["environment"] = value
        elif dotted == "internal.affection":
            state["relationship"]["intimacy"] = int(value)
            state["relationship"]["attachment"] = int(value)
        elif dotted == "internal.relationship_stage":
            state["relationship"]["stage"] = str(value)
    validate_state(state)


def cmd_doctor(config: dict[str, Any]) -> dict[str, Any]:
    paths = ensure_runtime(config)
    checks: list[dict[str, Any]] = []
    checks.append({"name": "python", "ok": sys.version_info >= (3, 10), "detail": sys.version})
    checks.append({"name": "state", "ok": paths["state"].is_file(), "detail": str(paths["state"])})
    checks.append({"name": "state_dir_write", "ok": os.access(paths["root"], os.W_OK), "detail": str(paths["root"])})
    hermes_cli = resolve_hermes_cli(config)
    hermes_path = Path(hermes_cli)
    hermes_ok = (
        hermes_path.is_file() and os.access(hermes_path, os.X_OK)
        if hermes_path.is_absolute() or os.sep in hermes_cli
        else shutil.which(hermes_cli) is not None
    )
    checks.append({"name": "hermes_cli", "ok": hermes_ok, "detail": hermes_cli})
    checks.append({"name": "systemd_run", "ok": shutil.which("systemd-run") is not None, "detail": shutil.which("systemd-run") or "fallback detached launcher will be used"})
    mode = config.get("execution", {}).get("mode", "direct_http")
    checks.append({"name": "execution_mode", "ok": mode in {"mcp", "direct_http"}, "detail": mode})
    if mode == "mcp":
        try:
            registry = workflow_registry(config)
            checks.append({"name": "workflow_registry", "ok": True, "detail": config["execution"]["registry_path"]})
            ready = []
            for workflow in registry["workflows"].values():
                for target, status in workflow["target_status"].items():
                    if status.get("workflow_exists") and status.get("interface_verified"):
                        ready.append(f"{workflow['id']}@{target}")
            checks.append({
                "name": "mcp_workflow_ready", "ok": bool(ready),
                "required": False,
                "detail": ready or "no registry workflow has both existence and interface verification"
            })
        except Exception as exc:
            checks.append({"name": "workflow_registry", "ok": False, "detail": str(exc)})
        status = cmd_mcp_status(config)
        checks.append({"name": "mcp_targets_configured", "ok": status["ok"], "detail": status["configured_targets"]})
    else:
        try:
            workflow_path, _ = validate_workflow(config)
            checks.append({"name": "legacy_workflow_interface", "ok": True, "detail": str(workflow_path)})
        except Exception as exc:
            checks.append({"name": "legacy_workflow_interface", "ok": False, "detail": str(exc)})
        try:
            base = config["comfyui"]["base_url"].rstrip("/")
            http_json(f"{base}/system_stats", timeout=5)
            checks.append({"name": "legacy_comfyui", "ok": True, "detail": base})
        except Exception as exc:
            checks.append({"name": "legacy_comfyui", "ok": False, "detail": str(exc)})
    required = [item for item in checks if item.get("required", True) and item["name"] != "systemd_run"]
    return {"ok": all(item["ok"] for item in required), "checks": checks}


def cmd_job_status(config: dict[str, Any], job_id: str) -> dict[str, Any]:
    path = ensure_runtime(config)["jobs"] / f"{job_id}.json"
    if not path.is_file():
        raise AvatarError(f"找不到任务：{job_id}")
    return load_json(path)


def cmd_jobs(config: dict[str, Any], last: int) -> list[dict[str, Any]]:
    jobs_dir = ensure_runtime(config)["jobs"]
    paths = sorted(jobs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:last]
    result = []
    for path in paths:
        job = read_job(path)
        if job:
            result.append(job)
    return result


def cmd_wait(config: dict[str, Any], job_id: str, timeout: int) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = cmd_job_status(config, job_id)
        if job.get("status") not in {"queued", "running"}:
            return job
        time.sleep(1)
    raise AvatarError(f"等待任务超时：{job_id}")


def cmd_backup(config: dict[str, Any], output: str) -> dict[str, Any]:
    paths = ensure_runtime(config)
    destination = Path(os.path.expanduser(output)).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    import tarfile

    with tarfile.open(destination, "w:gz") as archive:
        archive.add(paths["state"], arcname="state/state.json")
        if paths["history"].is_file():
            archive.add(paths["history"], arcname="state/history.jsonl")
        if LOCAL_CONFIG_PATH.is_file():
            archive.add(LOCAL_CONFIG_PATH, arcname="config.local.json")
        archive.add(SKILL_ROOT / "workflow", arcname="workflow")
    return {"ok": True, "backup": str(destination)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="avatarctl")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("context")
    sub.add_parser("status")
    wardrobe = sub.add_parser("wardrobe")
    wardrobe.add_argument("--set-json", default="")
    sub.add_parser("workflows")
    workflow_info = sub.add_parser("workflow-info")
    workflow_info.add_argument("workflow_id")
    sub.add_parser("mcp-status")
    mcp_target = sub.add_parser("mcp-target")
    mcp_target.add_argument("target", nargs="?", default="")

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--intent", required=True)
    prepare.add_argument("--set", action="append", default=[])
    prepare.add_argument("--workflow", default="")
    prepare.add_argument("--target", default="")
    prepare.add_argument("--aspect-ratio", default="")
    prepare.add_argument("--megapixels", type=float)
    prepare.add_argument("--width", type=int)
    prepare.add_argument("--height", type=int)
    prepare.add_argument("--say", default="")
    prepare.add_argument("--channel", default="")
    prepare.add_argument("--no-send", action="store_true")

    verify_variant = sub.add_parser("verify-variant")
    verify_variant.add_argument("--transaction", required=True)
    verify_variant.add_argument("--variant-workflow-path", required=True)
    observed_slots = verify_variant.add_mutually_exclusive_group(required=True)
    observed_slots.add_argument("--observed-slots-json")
    observed_slots.add_argument("--observed-slots-base64")
    verify_variant.add_argument("--uploaded-filename", default="")

    claim_submit = sub.add_parser("claim-submit")
    claim_submit.add_argument("--transaction", required=True)

    bind = sub.add_parser("bind")
    bind.add_argument("--transaction", required=True)
    bind.add_argument("--prompt-id", required=True)

    mark_completed = sub.add_parser("mark-completed")
    mark_completed.add_argument("--transaction", required=True)
    mark_completed.add_argument("--prompt-id", required=True)

    commit = sub.add_parser("commit")
    commit.add_argument("--transaction", required=True)
    commit.add_argument("--prompt-id", required=True)
    commit.add_argument("--result-image", required=True)
    commit.add_argument("--remote-result-image", default="")

    retry_delivery = sub.add_parser("retry-delivery")
    retry_delivery.add_argument("--transaction", required=True)
    retry_delivery.add_argument("--channel", default="")

    abort = sub.add_parser("abort")
    abort.add_argument("--transaction", required=True)
    abort.add_argument("--reason", default="pre-submit replanning")

    transaction_status = sub.add_parser("transaction-status")
    transaction_status.add_argument("transaction_id")

    history = sub.add_parser("history")
    history.add_argument("--last", type=int, default=10)

    update = sub.add_parser("update")
    update.add_argument("--set", action="append", default=[])
    update.add_argument("--remember", default="")

    for command in ("show", "render"):
        item = sub.add_parser(command)
        item.add_argument("--intent", default="")
        item.add_argument("--say", default="")
        item.add_argument("--remember", default="")
        item.add_argument("--channel", default="")
        item.add_argument("--no-send", action="store_true")
        item.add_argument("--foreground", action="store_true")
        if command == "render":
            item.add_argument("--set", action="append", default=[])

    resend = sub.add_parser("resend-last")
    resend.add_argument("--say", default="")
    resend.add_argument("--channel", default="")
    resend.add_argument("--foreground", action="store_true")

    sub.add_parser("doctor")

    jobs = sub.add_parser("jobs")
    jobs.add_argument("--last", type=int, default=10)

    job_status = sub.add_parser("job-status")
    job_status.add_argument("job_id")

    wait = sub.add_parser("wait")
    wait.add_argument("job_id")
    wait.add_argument("--timeout", type=int, default=240)

    backup = sub.add_parser("backup")
    backup.add_argument("--output", required=True)

    transition = sub.add_parser("transition")
    transition.add_argument("--json", default="")
    transition.add_argument(
        "--transition-type",
        choices=("continue", "partial", "new_scene"),
        default="",
    )
    transition.add_argument("--action", action="append", default=[])
    transition.add_argument("--replace-outfit", default="")
    transition.add_argument("--layer", action="append", default=[])
    transition.add_argument("--location", default="")
    transition.add_argument("--activity", default="")
    transition.add_argument("--pose", default="")
    transition.add_argument("--expression", default="")
    transition.add_argument("--render", action="store_true")
    transition.add_argument("--say", default="")
    transition.add_argument("--channel", default="")
    transition.add_argument("--no-send", action="store_true")
    transition.add_argument("--foreground", action="store_true")

    sub.add_parser("reset-scene")
    sub.add_parser("reset-runtime-state")
    factory = sub.add_parser("factory-reset")
    factory.add_argument("--apply", action="store_true")
    factory.add_argument("--confirm", default="")

    relationship_update = sub.add_parser("relationship-update")
    relationship_update.add_argument("--json", required=True)
    relationship_update.add_argument("--apply", action="store_true")

    relationship_overwrite = sub.add_parser("relationship-overwrite")
    relationship_overwrite_source = relationship_overwrite.add_mutually_exclusive_group(
        required=True
    )
    relationship_overwrite_source.add_argument("--from-file", default="")
    relationship_overwrite_source.add_argument("--json", default="")
    relationship_overwrite.add_argument("--apply", action="store_true")
    relationship_overwrite.add_argument("--bypass-schema", action="store_true")
    relationship_overwrite.add_argument("--confirm", default="")

    sub.add_parser("relationship-backups")
    relationship_restore = sub.add_parser("relationship-restore")
    relationship_restore.add_argument("--backup", required=True)
    relationship_restore.add_argument("--apply", action="store_true")
    relationship_restore.add_argument("--bypass-schema", action="store_true")
    relationship_restore.add_argument("--confirm", default="")

    memory_list = sub.add_parser("memory-list")
    memory_list.add_argument(
        "--status", choices=sorted(memory_store.MEMORY_STATUSES), default=""
    )
    memory_list.add_argument(
        "--type", choices=sorted(memory_store.MEMORY_TYPES), default=""
    )
    memory_show = sub.add_parser("memory-show")
    memory_show.add_argument("memory_id")
    memory_add = sub.add_parser("memory-add")
    memory_add.add_argument("--type", choices=sorted(memory_store.MEMORY_TYPES), required=True)
    memory_add.add_argument("--importance", type=int, required=True)
    memory_add.add_argument("--text", required=True)
    memory_add.add_argument("--source-event-id", default="")
    memory_supersede = sub.add_parser("memory-supersede")
    memory_supersede.add_argument("memory_id")
    memory_supersede.add_argument("--text", required=True)
    memory_supersede.add_argument("--type", choices=sorted(memory_store.MEMORY_TYPES), default="")
    memory_supersede.add_argument("--importance", type=int)
    memory_supersede.add_argument("--source-event-id", default="")
    memory_resolve = sub.add_parser("memory-resolve")
    memory_resolve.add_argument("memory_id")
    sub.add_parser("memory-rebuild-summary")

    worker = sub.add_parser(WORKER_ACTION, help=argparse.SUPPRESS)
    worker.add_argument("--job", required=True)
    return parser


def transition_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if args.json:
        try:
            value = json.loads(args.json)
        except json.JSONDecodeError as exc:
            raise AvatarError(f"invalid transition JSON: {exc}") from exc
        if any(
            [
                args.action,
                args.replace_outfit,
                args.layer,
                args.location,
                args.activity,
                args.pose,
                args.expression,
                args.transition_type,
            ]
        ):
            raise AvatarError("--json cannot be combined with transition field flags")
        return value
    actions: list[dict[str, Any]] = []
    for action in args.action:
        if action != "shower":
            raise AvatarError(
                "--action currently accepts only shower; use explicit field flags for others"
            )
        actions.append({"type": "shower"})
    if args.replace_outfit:
        actions.append({"type": "replace_outfit", "preset": args.replace_outfit})
    actions.extend({"type": "layer_outfit", "item": item} for item in args.layer)
    if args.location:
        actions.append({"type": "move", "location": args.location})
    if args.activity:
        actions.append({"type": "start_activity", "activity": args.activity})
    if args.pose:
        actions.append({"type": "set_pose", "pose": args.pose})
    if args.expression:
        actions.append({"type": "set_expression", "expression": args.expression})
    transition_type = args.transition_type or (
        "new_scene" if args.location else "partial"
    )
    return {"transition_type": transition_type, "actions": actions}


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=None))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == WORKER_ACTION:
        return worker_main(Path(args.job))
    config = load_config()
    try:
        if args.command == "context":
            result = cmd_context(config)
        elif args.command == "status":
            result = cmd_status(config)
        elif args.command == "wardrobe":
            if args.set_json:
                try:
                    wardrobe_value = json.loads(args.set_json)
                except json.JSONDecodeError as exc:
                    raise AvatarError(f"无效穿着 JSON：{exc}") from exc
                result = cmd_wardrobe_update(config, wardrobe_value)
            else:
                result = cmd_wardrobe_status(config)
        elif args.command == "workflows":
            result = cmd_workflows(config)
        elif args.command == "workflow-info":
            result = cmd_workflow_info(config, args.workflow_id)
        elif args.command == "mcp-status":
            result = cmd_mcp_status(config)
        elif args.command == "mcp-target":
            result = cmd_mcp_target(config, args.target)
        elif args.command == "prepare":
            result = cmd_prepare(
                config,
                intent=args.intent,
                patch=parse_set_values(args.set, config),
                requested_workflow=args.workflow,
                requested_target=args.target,
                aspect_ratio=args.aspect_ratio,
                megapixels=args.megapixels,
                width=args.width,
                height=args.height,
                say=args.say or "我已经按你说的准备好了，给你看看。",
                channel=args.channel,
                no_send=bool(args.no_send),
            )
        elif args.command == "verify-variant":
            result = cmd_verify_variant(
                config, args.transaction, args.variant_workflow_path,
                args.observed_slots_json or "",
                args.observed_slots_base64 or "",
                args.uploaded_filename,
            )
        elif args.command == "claim-submit":
            result = cmd_claim_submit(config, args.transaction)
        elif args.command == "bind":
            result = cmd_bind(config, args.transaction, args.prompt_id)
        elif args.command == "mark-completed":
            result = cmd_mark_completed(config, args.transaction, args.prompt_id)
        elif args.command == "commit":
            result = cmd_commit(
                config, args.transaction, args.prompt_id,
                args.result_image, args.remote_result_image,
            )
        elif args.command == "retry-delivery":
            result = cmd_retry_delivery(config, args.transaction, args.channel)
        elif args.command == "abort":
            result = cmd_abort(config, args.transaction, args.reason)
        elif args.command == "transaction-status":
            result = cmd_transaction_status(config, args.transaction_id)
        elif args.command == "history":
            result = cmd_history(config, max(1, args.last))
        elif args.command == "update":
            result = cmd_update(config, parse_set_values(args.set, config), args.remember)
        elif args.command in {"show", "render"}:
            default_say = (
                "我现在就拍给你看。"
                if args.command == "show"
                else "我已经按你说的调整好了，给你看看。"
            )
            patch = parse_set_values(getattr(args, "set", []), config)
            if config.get("execution", {}).get("mode") == "mcp":
                result = cmd_prepare(
                    config,
                    intent=args.intent or (
                        "给我看看你现在的样子" if args.command == "show" else "完整重新生成当前造型"
                    ),
                    patch=patch,
                    requested_workflow="",
                    requested_target="",
                    aspect_ratio="",
                    megapixels=None,
                    width=None,
                    height=None,
                    say=args.say or default_say,
                    channel=args.channel,
                    no_send=bool(args.no_send),
                )
            else:
                request = {
                    "action": args.command,
                    "patch": patch,
                    "say": args.say or default_say,
                    "remember": args.remember,
                    "channel": args.channel or config["telegram"]["default_channel"],
                    "no_send": bool(args.no_send),
                }
                result = foreground_request(request, config) if args.foreground else queue_job(request, config)
        elif args.command == "resend-last":
            request = {
                "action": "resend-last",
                "patch": {},
                "say": args.say,
                "remember": "",
                "channel": args.channel or config["telegram"]["default_channel"],
                "no_send": False,
            }
            result = foreground_request(request, config) if args.foreground else queue_job(request, config)
        elif args.command == "doctor":
            result = cmd_doctor(config)
        elif args.command == "jobs":
            result = cmd_jobs(config, max(1, args.last))
        elif args.command == "job-status":
            result = cmd_job_status(config, args.job_id)
        elif args.command == "wait":
            result = cmd_wait(config, args.job_id, max(1, args.timeout))
        elif args.command == "backup":
            result = cmd_backup(config, args.output)
        elif args.command == "transition":
            spec = transition_from_args(args)
            if args.render:
                if config.get("execution", {}).get("mode") == "mcp":
                    result = cmd_prepare(
                        config,
                        intent="完整重新生成当前视觉场景",
                        patch={},
                        requested_workflow="yinyue_cosplay01",
                        requested_target="",
                        aspect_ratio="",
                        megapixels=None,
                        width=None,
                        height=None,
                        say=args.say or "我已经按你说的调整好了，给你看看。",
                        channel=args.channel,
                        no_send=bool(args.no_send),
                        transition=spec,
                    )
                else:
                    request = {
                        "action": "render-transition",
                        "patch": {},
                        "transition": spec,
                        "say": args.say or "我已经按你说的调整好了，给你看看。",
                        "remember": "",
                        "channel": args.channel or config["telegram"]["default_channel"],
                        "no_send": bool(args.no_send),
                    }
                    result = (
                        foreground_request(request, config)
                        if args.foreground
                        else queue_job(request, config)
                    )
            else:
                result = cmd_transition(config, spec)
        elif args.command in {"reset-scene", "reset-runtime-state"}:
            result = cmd_reset(config, args.command)
        elif args.command == "factory-reset":
            result = cmd_factory_reset(
                config, apply=bool(args.apply), confirm=args.confirm
            )
        elif args.command == "relationship-update":
            try:
                patch = json.loads(args.json)
            except json.JSONDecodeError as exc:
                raise AvatarError(f"invalid relationship update JSON: {exc}") from exc
            result = cmd_relationship_update(config, patch, apply=bool(args.apply))
        elif args.command == "relationship-overwrite":
            relationship, source = load_relationship_payload(
                json_text=args.json, from_file=args.from_file
            )
            result = replace_relationship(
                config,
                relationship,
                apply=bool(args.apply),
                bypass_schema=bool(args.bypass_schema),
                confirm=args.confirm,
                input_source=source,
            )
        elif args.command == "relationship-backups":
            result = cmd_relationship_backups(config)
        elif args.command == "relationship-restore":
            result = cmd_relationship_restore(
                config,
                args.backup,
                apply=bool(args.apply),
                bypass_schema=bool(args.bypass_schema),
                confirm=args.confirm,
            )
        elif args.command == "memory-list":
            result = cmd_memory_list(
                config, status=args.status, memory_type=args.type
            )
        elif args.command == "memory-show":
            result = cmd_memory_show(config, args.memory_id)
        elif args.command == "memory-add":
            result = cmd_memory_add(
                config,
                memory_type=args.type,
                importance=args.importance,
                text=args.text,
                source_event_id=args.source_event_id,
            )
        elif args.command == "memory-supersede":
            result = cmd_memory_supersede(
                config,
                args.memory_id,
                text=args.text,
                memory_type=args.type,
                importance=args.importance,
                source_event_id=args.source_event_id,
            )
        elif args.command == "memory-resolve":
            result = cmd_memory_resolve(config, args.memory_id)
        elif args.command == "memory-rebuild-summary":
            result = cmd_memory_rebuild_summary(config)
        else:
            parser.error(f"unknown command: {args.command}")
            return 2
        print_json(result)
        return 0
    except AvatarError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: unexpected failure: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
