"""Pure route registry and deterministic command parsing."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


CONTROL_RE = re.compile(
    r"^/(xiaowu-avatar|yinyue_avatar)(?:@[A-Za-z0-9_]+)?\s+"
    r"(启动|结束|状态|重置)\s*$",
    re.IGNORECASE,
)


class RouteConfigError(ValueError):
    """Raised when the static route registry is invalid."""


@dataclass(frozen=True)
class Route:
    skill: str
    model: str
    provider: str
    base_url: str
    api_mode: str
    reasoning_effort: str
    endpoint_timeout_seconds: float = 2.0

    @property
    def runtime_provider(self) -> str:
        if self.provider.startswith("custom:"):
            return self.provider
        return f"custom:{self.provider}"


class RouteRegistry:
    def __init__(self, routes: dict[str, Route]) -> None:
        self._routes = dict(routes)

    @classmethod
    def load(cls, path: Path) -> "RouteRegistry":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        route_map = raw.get("routes")
        if not isinstance(route_map, dict) or not route_map:
            raise RouteConfigError("routes.yaml must contain a non-empty routes mapping")
        routes: dict[str, Route] = {}
        required = {
            "model", "provider", "base_url", "api_mode", "reasoning_effort"
        }
        for skill, value in route_map.items():
            if not isinstance(skill, str) or not skill.strip() or not isinstance(value, dict):
                raise RouteConfigError("each route must be a named mapping")
            missing = sorted(required - set(value))
            if missing:
                raise RouteConfigError(f"route {skill!r} is missing: {', '.join(missing)}")
            route = Route(
                skill=skill.strip(),
                model=str(value["model"]).strip(),
                provider=str(value["provider"]).strip(),
                base_url=str(value["base_url"]).strip().rstrip("/"),
                api_mode=str(value["api_mode"]).strip(),
                reasoning_effort=str(value["reasoning_effort"]).strip().lower(),
                endpoint_timeout_seconds=float(value.get("endpoint_timeout_seconds", 2.0)),
            )
            if not all((route.model, route.provider, route.base_url, route.api_mode)):
                raise RouteConfigError(f"route {skill!r} contains an empty required value")
            if route.reasoning_effort not in {"none", "off", "disabled"}:
                raise RouteConfigError(
                    f"route {skill!r} must disable reasoning for the Phase 1 worker"
                )
            if route.endpoint_timeout_seconds <= 0:
                raise RouteConfigError(f"route {skill!r} has an invalid endpoint timeout")
            routes[route.skill] = route
        return cls(routes)

    def get(self, skill: str) -> Route | None:
        return self._routes.get(skill)

    def require(self, skill: str) -> Route:
        route = self.get(skill)
        if route is None:
            raise RouteConfigError(f"no worker route exists for skill {skill!r}")
        return route

    def as_dict(self) -> dict[str, Route]:
        return dict(self._routes)


def parse_control_command(*texts: Any) -> str | None:
    """Return a control verb only for an exact, explicit slash command."""
    for value in texts:
        text = str(value or "").strip()
        match = CONTROL_RE.fullmatch(text)
        if match:
            return match.group(2)
    return None


def is_slash_command(*texts: Any) -> bool:
    return any(str(value or "").lstrip().startswith("/") for value in texts)
