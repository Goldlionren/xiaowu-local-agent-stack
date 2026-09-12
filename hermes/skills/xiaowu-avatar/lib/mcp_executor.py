"""Deterministic MCP transaction runner for the Yinyue visual plugin."""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import re
import time
from pathlib import Path
from typing import Any, Callable

import avatarctl as core


class MCPExecutionError(core.AvatarError):
    pass


ToolCaller = Callable[[str, dict[str, Any]], str | dict[str, Any]]
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_MEDIA_RE = re.compile(r"MEDIA:(/[^\s\r\n]+)")
_REMOTE_IMAGE_RE = re.compile(
    r"[A-Za-z]:\\[^\r\n\"']+?\.(?:png|jpe?g|webp|gif)", re.IGNORECASE
)


def _expand_json(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return _expand_json(json.loads(stripped), depth + 1)
            except json.JSONDecodeError:
                return value
        return value
    if isinstance(value, dict):
        return {key: _expand_json(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_json(item, depth + 1) for item in value]
    return value


def _walk(value: Any):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _tool_result(call_tool: ToolCaller, name: str, arguments: dict[str, Any]) -> Any:
    raw = call_tool(name, arguments)
    try:
        outer = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise MCPExecutionError(f"{name} 返回了无效 JSON") from exc
    if isinstance(outer, dict) and outer.get("error"):
        raise MCPExecutionError(f"{name} 失败：{outer['error']}")
    return _expand_json(outer)


def _mcp_name(target: str, tool: str) -> str:
    return f"mcp__{target}__{tool}"


def _find_slots_payload(value: Any) -> dict[str, Any]:
    for item in _walk(value):
        if isinstance(item, dict) and isinstance(item.get("slots"), list):
            return item
    raise MCPExecutionError("list_workflow_slots 返回中缺少 slots")


def _candidate_strings(value: Any, preferred_keys: set[str]) -> list[str]:
    preferred: list[str] = []
    remaining: list[str] = []
    for item in _walk(value):
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(child, str):
                    (preferred if key.lower() in preferred_keys else remaining).append(child)
    return preferred + remaining


def _variant_path(value: Any, transaction: dict[str, Any]) -> str:
    candidates = _candidate_strings(
        value,
        {"workflow", "workflow_path", "variant", "variant_path", "output", "path"},
    )
    for item in _walk(value):
        if isinstance(item, str):
            candidates.extend(re.findall(r"[A-Za-z]:\\[^\r\n\"']+?\.json", item))
    expected_dir = core._windows_path_key(transaction["variant_dir"])
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.lower().endswith(".json"):
            continue
        try:
            key = core._windows_path_key(candidate)
        except core.AvatarError:
            continue
        if key != core._windows_path_key(transaction["original_workflow_path"]):
            try:
                import ntpath
                if ntpath.commonpath([expected_dir, key]) == expected_dir:
                    return candidate
            except ValueError:
                continue
    raise MCPExecutionError("vary_workflow 未返回 transaction 专属 variant path")


def _uploaded_filename(value: Any) -> str:
    for candidate in _candidate_strings(
        value, {"filename", "name", "uploaded_filename", "uploaded", "path"}
    ):
        if candidate and not candidate.startswith(("{", "[")):
            return candidate.replace("\\", "/").rsplit("/", 1)[-1]
    raise MCPExecutionError("upload_file 未返回 uploaded filename")


def _prompt_id(value: Any) -> str:
    for item in _walk(value):
        if isinstance(item, dict):
            for key in ("prompt_id", "promptId"):
                candidate = item.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
    for item in _walk(value):
        if isinstance(item, str):
            match = _UUID_RE.search(item)
            if match:
                return match.group(0)
    raise MCPExecutionError("run_workflow 未返回 prompt_id")


def _job_status(value: Any) -> str:
    statuses: list[str] = []
    for item in _walk(value):
        if isinstance(item, dict):
            for key in ("status", "state", "job_status"):
                candidate = item.get(key)
                if isinstance(candidate, str):
                    statuses.append(candidate.strip().lower())
    terminal = {"completed", "failed", "error", "cancelled", "canceled"}
    return next((status for status in statuses if status in terminal), statuses[0] if statuses else "")


def _output_paths(value: Any) -> tuple[str, str]:
    strings = [item for item in _walk(value) if isinstance(item, str)]
    joined = "\n".join(strings)
    media = _MEDIA_RE.search(joined)
    if not media:
        raise MCPExecutionError("fetch_outputs 未返回 Hermes Linux MEDIA path")
    remote = ""
    for candidate in _candidate_strings(
        value, {"saved", "saved_path", "output", "output_path", "path", "filename"}
    ) + _REMOTE_IMAGE_RE.findall(joined):
        match = _REMOTE_IMAGE_RE.search(candidate)
        if match:
            remote = match.group(0)
            break
    return f"MEDIA:{media.group(1)}", remote


def _replace(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace(item, replacements) for key, item in value.items()}
    return value


def _step(plan: dict[str, Any], name: str) -> dict[str, Any]:
    for item in plan.get("steps", []):
        if item.get("step") == name:
            return item
    raise MCPExecutionError(f"transaction plan 缺少步骤：{name}")



# identity-studio:v0.1.7 everyone-avatar api runtime

def _everyone_api_contract(workflow: dict[str, Any]) -> dict[str, Any] | None:
    contract = workflow.get("identity_workflow_contract", {})
    if not isinstance(contract, dict):
        return None
    if (
        contract.get("mode") == "everyone-avatar"
        and contract.get("workflow_format") == "api"
        and contract.get("variant_strategy") == "local_json_scp_sha256"
    ):
        return contract
    return None


def _transport_for_target(config: dict[str, Any], target: str) -> tuple[Path, str]:
    contract = config.get("identity_workflow_contract", {})
    transports = contract.get("api_variant_transport", {}) if isinstance(contract, dict) else {}
    item = transports.get(target) if isinstance(transports, dict) else None
    if not isinstance(item, dict):
        raise MCPExecutionError(f"Everyone Avatar API transport missing for {target}")
    key = Path(str(item.get("ssh_key") or "")).expanduser()
    remote = str(item.get("ssh_remote") or "").strip()
    if not key.is_file():
        raise MCPExecutionError(f"Everyone Avatar SSH key missing: {key}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.:-]+", remote):
        raise MCPExecutionError(f"Everyone Avatar ssh_remote is unsafe: {remote!r}")
    return key, remote


def _safe_windows_path(raw: str) -> str:
    value = str(raw or "").replace("/", "\\")
    if not re.fullmatch(r"[A-Za-z]:\\[A-Za-z0-9_.\\-]+", value):
        raise MCPExecutionError(f"unsafe Windows transaction path: {raw!r}")
    return value


def _windows_to_scp_path(raw: str) -> str:
    value = _safe_windows_path(raw)
    return "/" + value[0].upper() + ":/" + value[3:].replace("\\", "/")


def _run_external(argv: list[str], label: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        proc = subprocess.run(
            argv, text=True, capture_output=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MCPExecutionError(f"{label} failed before submit: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:1000]
        raise MCPExecutionError(f"{label} failed before submit: rc={proc.returncode} {detail}")
    return proc


def _stage_everyone_api_variant(
    config: dict[str, Any], transaction: dict[str, Any]
) -> dict[str, str]:
    package = core.build_everyone_api_variant(config, transaction["transaction_id"])
    variant_path = _safe_windows_path(package["variant_workflow_path"])
    variant_dir = _safe_windows_path(transaction["variant_dir"])
    if not variant_path.startswith(variant_dir.rstrip("\\") + "\\"):
        raise MCPExecutionError("API variant path escaped transaction directory")
    key, remote = _transport_for_target(config, transaction["target"])
    ssh_opts = ["-i", str(key), "-o", "BatchMode=yes", "-o", "LogLevel=ERROR"]
    scp_path = _windows_to_scp_path(variant_path)

    with tempfile.TemporaryDirectory(prefix="yinyue-api-variant-") as td:
        local = Path(td) / "variant.json"
        roundtrip = Path(td) / "roundtrip.json"
        local.write_bytes(package["content_bytes"])

        mkdir_command = f'cmd.exe /d /q /c if not exist "{variant_dir}" mkdir "{variant_dir}"'
        _run_external(
            ["ssh", "-T", *ssh_opts, remote, mkdir_command],
            "create remote API variant directory",
            timeout=30,
        )
        _run_external(
            ["scp", "-p", *ssh_opts, str(local), f"{remote}:{scp_path}"],
            "upload API variant",
            timeout=60,
        )
        _run_external(
            ["scp", "-p", *ssh_opts, f"{remote}:{scp_path}", str(roundtrip)],
            "round-trip API variant",
            timeout=60,
        )
        sent = local.read_bytes()
        received = roundtrip.read_bytes()
        if sent != received:
            raise MCPExecutionError("remote API variant byte verification mismatch before submit")
        remote_sha = hashlib.sha256(received).hexdigest()
        if remote_sha != package["sha256"]:
            raise MCPExecutionError("remote API variant SHA256 verification mismatch before submit")

    return {
        "variant_workflow_path": variant_path,
        "remote_sha256": remote_sha,
    }


def execute_transaction(
    config: dict[str, Any], transaction_id: str, call_tool: ToolCaller
) -> dict[str, Any]:
    transaction = core.cmd_transaction_status(config, transaction_id)
    if transaction.get("status") in {"committed", "delivered", "pending_delivery"}:
        return {
            "ok": True,
            "idempotent": True,
            "transaction": transaction,
            "agent_action": "stop_silently",
        }
    registry = core.workflow_registry(config)
    target = transaction["target"]
    status = transaction.get("status", "planned")

    if status == "planned":
        workflow = registry["workflows"][transaction["workflow_id"]]
        if _everyone_api_contract(workflow):
            # API-format Everyone Avatar must never pass through frontend slot tools.
            # Build one deterministic local variant, SCP it to the transaction-only
            # directory, round-trip verify bytes/SHA256, then grant variant_verified.
            _tool_result(call_tool, _mcp_name(target, "server_info"), {})
            staged = _stage_everyone_api_variant(config, transaction)
            core.cmd_verify_api_variant(
                config,
                transaction_id,
                staged["variant_workflow_path"],
                staged["remote_sha256"],
            )
            transaction = core.cmd_transaction_status(config, transaction_id)
            status = transaction["status"]
        else:
            plan = core.visual_system.build_mcp_plan(transaction, registry)
            _tool_result(call_tool, _mcp_name(target, "server_info"), {})
            uploaded = ""
            if transaction.get("source_remote_image"):
                upload_step = _step(plan, "upload_source")
                uploaded_result = _tool_result(
                    call_tool, upload_step["tool"], upload_step["arguments"]
                )
                uploaded = _uploaded_filename(uploaded_result)
            vary_step = _step(plan, "create_transaction_variant")
            vary_arguments = _replace(
                vary_step["arguments"], {"$UPLOADED_FILENAME": uploaded}
            )
            vary_result = _tool_result(call_tool, vary_step["tool"], vary_arguments)
            variant = _variant_path(vary_result, transaction)
            slots_result = _tool_result(
                call_tool,
                _mcp_name(target, "list_workflow_slots"),
                {"workflow_path": variant},
            )
            payload = _find_slots_payload(slots_result)
            wanted = set(_step(plan, "verify_transaction_variant")["verification_addresses"])
            minimal = {
                "workflow": payload.get("workflow"),
                "slots": [
                    item for item in payload["slots"]
                    if isinstance(item, dict) and item.get("address") in wanted
                ],
            }
            core.cmd_verify_variant(
                config,
                transaction_id,
                variant,
                observed_slots_json=json.dumps(minimal, ensure_ascii=False),
                uploaded_filename=uploaded,
            )
            transaction = core.cmd_transaction_status(config, transaction_id)
            status = transaction["status"]

    if status == "variant_verified":
        claimed = core.cmd_claim_submit(config, transaction_id)
        run_result = _tool_result(
            call_tool,
            _mcp_name(target, "run_workflow"),
            claimed["run_workflow"],
        )
        prompt_id = _prompt_id(run_result)
        core.cmd_bind(config, transaction_id, prompt_id)
        transaction = core.cmd_transaction_status(config, transaction_id)
        status = transaction["status"]

    if status == "submit_claimed":
        raise MCPExecutionError(
            "transaction 已 claim 但尚未绑定 prompt_id；禁止自动重提，请管理员审计恢复"
        )

    prompt_id = str(transaction.get("prompt_id", ""))
    if status in {"submitted", "waiting", "fetch_pending"}:
        if not prompt_id:
            raise MCPExecutionError("submitted transaction 缺少 prompt_id")
        deadline = time.monotonic() + int(transaction["deadline_seconds"])
        poll_failures = 0
        while time.monotonic() < deadline:
            try:
                wait_result = _tool_result(
                    call_tool,
                    _mcp_name(target, "job"),
                    {"action": "wait", "prompt_id": prompt_id, "timeout_seconds": 20},
                )
            except MCPExecutionError as exc:
                # A read-only polling failure does not prove the GPU job failed.
                # Never release its claim or submit a replacement.
                if not any(marker in str(exc).lower() for marker in (
                    "server_not_running", "connection refused", "connection reset",
                    "timed out", "timeout", "connection closed",
                )):
                    raise
                poll_failures += 1
                if poll_failures >= 3:
                    return core.cmd_mark_pending_completion(config, transaction_id, prompt_id)
                time.sleep(min(2, max(0, deadline - time.monotonic())))
                continue
            poll_failures = 0
            job_status = _job_status(wait_result)
            if job_status == "completed":
                core.cmd_mark_completed(config, transaction_id, prompt_id)
                break
            if job_status in {"failed", "error", "cancelled", "canceled"}:
                raise MCPExecutionError(f"Comfy job terminal status：{job_status}")
        else:
            # The workflow was already submitted exactly once and prompt_id is bound.
            # A wait budget expiring means "still running", not generation failure.
            return core.cmd_mark_pending_completion(config, transaction_id, prompt_id)
        transaction = core.cmd_transaction_status(config, transaction_id)
        status = transaction["status"]

    if status == "generation_completed":
        fetch_result = _tool_result(
            call_tool,
            _mcp_name(target, "fetch_outputs"),
            {
                "prompt_id": prompt_id,
                "out_dir": transaction["remote_output_dir"],
                "url_only": False,
                "inline_images": True,
            },
        )
        local_media, remote_image = _output_paths(fetch_result)
        return core.cmd_commit(
            config, transaction_id, prompt_id, local_media, remote_image
        )

    raise MCPExecutionError(f"transaction 状态无法自动执行：{status}")


def _visual_patch(value: Any, config: dict[str, Any]) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MCPExecutionError("visual 必须是对象")
    allowed = {
        item.removeprefix("visual.")
        for item in config["state"]["allowed_set_fields"]
        if item.startswith("visual.")
    }
    unknown = set(value) - allowed
    if unknown:
        raise MCPExecutionError(f"未知 visual 字段：{sorted(unknown)}")
    if not all(isinstance(item, str) for item in value.values()):
        raise MCPExecutionError("visual 字段值必须是字符串")
    return {f"visual.{key}": item for key, item in value.items()}


def generate(args: dict[str, Any], call_tool: ToolCaller) -> dict[str, Any]:
    # Compatibility aliases select the normal router, never a GPU or fixed workflow.
    # Copy the input so the original tool-call record remains unchanged.
    args = dict(args)
    if args.get("workflow") in ("avatar", "auto"):
        args["workflow"] = ""
    config = core.load_config()
    intent = str(args.get("intent") or "").strip()
    if not intent:
        raise MCPExecutionError("intent 不能为空")
    try:
        prepared = core.cmd_prepare(
            config,
            intent=intent,
            patch=_visual_patch(args.get("visual"), config),
            requested_workflow=str(args.get("workflow") or ""),
            requested_target=str(args.get("target") or ""),
            aspect_ratio=str(args.get("aspect_ratio") or ""),
            megapixels=args.get("megapixels"),
            width=args.get("width"),
            height=args.get("height"),
            say=str(args.get("say") or "奴家已经按照主人的吩咐摆好姿势了，主人还满意吗？"),
            channel=str(args.get("channel") or ""),
            no_send=bool(args.get("no_send", False)),
        )
    except core.AvatarError as exc:
        # Single-flight recovery: a bound job always wins over a new request.
        # An unsubmitted transaction is safe to supersede, which also repairs
        # orphans left by older agent-driven/manual orchestration.
        match = re.search(
            r"active transaction[^：:]*[：:]\s*([0-9a-f]{32})\s+prompt_id=([^\s]*)",
            str(exc),
            re.IGNORECASE,
        )
        if match:
            active_id = match.group(1)
            active = core.cmd_transaction_status(config, active_id)
            if active.get("prompt_id"):
                return execute_transaction(config, active_id, call_tool)
            if (
                active.get("status") in {"planned", "variant_verified"}
                and int(active.get("submit_count", 0) or 0) == 0
            ):
                core.cmd_abort(
                    config,
                    active_id,
                    "superseded by deterministic single-tool request",
                )
                prepared = core.cmd_prepare(
                    config,
                    intent=intent,
                    patch=_visual_patch(args.get("visual"), config),
                    requested_workflow=str(args.get("workflow") or ""),
                    requested_target=str(args.get("target") or ""),
                    aspect_ratio=str(args.get("aspect_ratio") or ""),
                    megapixels=args.get("megapixels"),
                    width=args.get("width"),
                    height=args.get("height"),
                    say=str(args.get("say") or "奴家已经按照主人的吩咐摆好姿势了，主人还满意吗？"),
                    channel=str(args.get("channel") or ""),
                    no_send=bool(args.get("no_send", False)),
                )
            else:
                raise MCPExecutionError(
                    "已有未绑定但可能已提交的 transaction；禁止自动重提"
                ) from exc
        else:
            raise
    return execute_transaction(
        config, prepared["transaction"]["transaction_id"], call_tool
    )
