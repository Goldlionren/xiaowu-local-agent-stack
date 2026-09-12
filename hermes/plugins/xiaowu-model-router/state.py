"""Session-scoped router state stored in Hermes gateway session metadata."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from typing import Any

try:
    from .router import Route
except ImportError:  # Standalone validation outside the Hermes package loader.
    from router import Route


METADATA_KEY = "xiaowu_model_router"
MODE_WORKER = "xiaowu-worker"
LEGACY_MODE_WORKER = "skill-worker"


class RouterStateError(ValueError):
    """Raised when persisted router state is malformed or inconsistent."""


@dataclass(frozen=True)
class RouterState:
    session_id: str
    mode: str
    active_skill: str
    model: str
    provider: str
    base_url: str
    reasoning_effort: str
    activated_at: str
    persona_enabled: bool = True
    visual_skill_sticky: bool = False
    worker_turns: int = 0
    qwen_api_calls: int = 0
    luna_api_calls: int = 0
    routing_violations: int = 0
    last_actual_provider: str = ""
    last_actual_model: str = ""
    last_actual_endpoint: str = ""
    last_actual_api_mode: str = ""
    last_api_request_id: str = ""
    last_request_at: str = ""

    @classmethod
    def activate(cls, session_id: str, route: Route, provider: str) -> "RouterState":
        return cls(
            session_id=str(session_id),
            mode=MODE_WORKER,
            active_skill=route.skill,
            model=route.model,
            provider=provider,
            base_url=route.base_url,
            reasoning_effort=route.reasoning_effort,
            activated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )

    @classmethod
    def from_metadata(cls, value: Any) -> "RouterState | None":
        if value is None:
            return None
        if not isinstance(value, dict):
            raise RouterStateError("router metadata is not an object")
        required = {
            "session_id", "mode", "active_skill", "model", "provider",
            "base_url", "reasoning_effort", "activated_at",
        }
        missing = sorted(required - set(value))
        if missing:
            raise RouterStateError(f"router metadata is missing: {', '.join(missing)}")
        mode = str(value["mode"])
        if mode == LEGACY_MODE_WORKER:
            mode = MODE_WORKER
        state = cls(
            session_id=str(value["session_id"]),
            mode=mode,
            active_skill=str(value["active_skill"]),
            model=str(value["model"]),
            provider=str(value["provider"]),
            base_url=str(value["base_url"]),
            reasoning_effort=str(value["reasoning_effort"]),
            activated_at=str(value["activated_at"]),
            persona_enabled=bool(value.get("persona_enabled", True)),
            visual_skill_sticky=bool(value.get("visual_skill_sticky", False)),
            worker_turns=int(value.get("worker_turns", 0) or 0),
            qwen_api_calls=int(value.get("qwen_api_calls", 0) or 0),
            luna_api_calls=int(value.get("luna_api_calls", 0) or 0),
            routing_violations=int(value.get("routing_violations", 0) or 0),
            last_actual_provider=str(value.get("last_actual_provider") or ""),
            last_actual_model=str(value.get("last_actual_model") or ""),
            last_actual_endpoint=str(value.get("last_actual_endpoint") or ""),
            last_actual_api_mode=str(value.get("last_actual_api_mode") or ""),
            last_api_request_id=str(value.get("last_api_request_id") or ""),
            last_request_at=str(value.get("last_request_at") or ""),
        )
        if state.mode != MODE_WORKER:
            raise RouterStateError(f"unsupported router mode: {state.mode!r}")
        if not all((state.session_id, state.active_skill, state.model, state.provider)):
            raise RouterStateError("router metadata contains empty routing fields")
        return state

    def validate_route(self, route: Route) -> None:
        expected = (
            route.skill, route.model, route.runtime_provider,
            route.base_url, route.reasoning_effort,
        )
        actual = (
            self.active_skill, self.model, self.provider,
            self.base_url.rstrip("/"), self.reasoning_effort,
        )
        if actual != expected:
            raise RouterStateError("persisted worker state does not match routes.yaml")

    def record_worker_turn(self) -> "RouterState":
        return replace(self, worker_turns=self.worker_turns + 1)

    def record_api_request(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_mode: str,
        api_request_id: str,
        expected_qwen: bool,
        is_luna: bool,
    ) -> "RouterState":
        return replace(
            self,
            qwen_api_calls=self.qwen_api_calls + int(expected_qwen),
            luna_api_calls=self.luna_api_calls + int(is_luna),
            routing_violations=(
                self.routing_violations + int(not expected_qwen)
            ),
            last_actual_provider=provider,
            last_actual_model=model,
            last_actual_endpoint=base_url.rstrip("/"),
            last_actual_api_mode=api_mode,
            last_api_request_id=api_request_id,
            last_request_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )

    def reset_runtime(self) -> "RouterState":
        return replace(
            self,
            worker_turns=0,
            qwen_api_calls=0,
            luna_api_calls=0,
            routing_violations=0,
            last_actual_provider="",
            last_actual_model="",
            last_actual_endpoint="",
            last_actual_api_mode="",
            last_api_request_id="",
            last_request_at="",
        )

    def to_metadata(self) -> dict[str, Any]:
        return asdict(self)


def override_matches(override: Any, state: RouterState) -> bool:
    if not isinstance(override, dict):
        return False
    return (
        str(override.get("model") or "") == state.model
        and str(override.get("provider") or "") == state.provider
        and str(override.get("base_url") or "").rstrip("/") == state.base_url.rstrip("/")
    )
