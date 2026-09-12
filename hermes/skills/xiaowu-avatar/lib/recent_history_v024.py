from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ALLOWED_EVENT_TYPES = {
    "scene_transition",
    "relationship_change",
    "author_relationship_overwrite",
    "relationship_restore",
    "author_relationship_restore",
    "memory_added",
    "memory_superseded",
    "memory_resolved",
    "important_commitment",
    "render_completed",
    "delivery_pending",
    "migration",
}
IGNORED_EVENT_TYPES = {
    "status",
    "doctor",
    "job_poll",
    "launcher_metadata",
    "duplicate_request",
    "debug",
}
PRIORITY = {
    "author_relationship_overwrite": 100,
    "relationship_restore": 100,
    "author_relationship_restore": 100,
    "relationship_change": 95,
    "important_commitment": 90,
    "delivery_pending": 90,
    "memory_superseded": 80,
    "memory_resolved": 80,
    "memory_added": 75,
    "migration": 70,
    "scene_transition": 60,
    "render_completed": 40,
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def normalize_event_type(event: dict[str, Any]) -> str:
    raw = str(event.get("event_type") or event.get("event") or "")
    if raw == "transition":
        return "scene_transition"
    if raw in {"relationship_update"}:
        return "relationship_change"
    if raw == "author_relationship_restore":
        return raw
    if raw in {"render", "show", "transition_render"} and event.get("image"):
        return "render_completed"
    if raw == "pending_delivery":
        return "delivery_pending"
    return raw


def compact_event(event: dict[str, Any], event_type: str) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "event_id": str(
            event.get("event_id")
            or event.get("job_id")
            or event.get("memory_id")
            or hashlib.sha256(canonical_bytes(event)).hexdigest()
        ),
        "event_type": event_type,
        "timestamp": str(event.get("timestamp") or event.get("at") or ""),
    }
    for key in (
        "revision",
        "revision_before",
        "revision_after",
        "schema_bypassed",
        "memory_id",
        "memory_type",
        "importance",
        "status",
        "location",
        "activity",
        "image",
        "backup_path",
    ):
        if key in event:
            compact[key] = event[key]
    if event_type in {
        "author_relationship_overwrite",
        "relationship_restore",
        "author_relationship_restore",
        "relationship_change",
    }:
        compact["summary"] = (
            f"relationship revision {event.get('revision_before', '?')}"
            f"→{event.get('revision_after', event.get('revision', '?'))}"
        )
    elif event_type.startswith("memory_"):
        compact["summary"] = str(event.get("summary") or event.get("text") or "")[:300]
    elif event_type == "scene_transition":
        transition = event.get("transition", {})
        compact["summary"] = str(
            transition.get("transition_type") if isinstance(transition, dict) else ""
        )
    elif event_type == "migration":
        compact["summary"] = (
            f"schema {event.get('from_schema', '?')}→{event.get('to_schema', '?')}"
        )
    # Explicitly never copy changed_fields/before/after relationship objects.
    return compact


def business_fingerprint(event: dict[str, Any]) -> str:
    relevant = {
        key: value
        for key, value in event.items()
        if key not in {"event_id", "timestamp"}
    }
    return hashlib.sha256(canonical_bytes(relevant)).hexdigest()


def meaningful_recent_history(
    path: Path,
    *,
    max_events: int,
    max_chars: int,
    scan_max_lines: int,
) -> dict[str, Any]:
    if max_events < 1 or max_chars < 1 or scan_max_lines < 1:
        raise ValueError("history limits must be positive")
    if not path.is_file():
        return {"events": [], "truncated": False, "skipped_corrupt_lines": 0}
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        data = b""
        while position > 0 and data.count(b"\n") <= scan_max_lines:
            size = min(4096, position)
            position -= size
            handle.seek(position)
            data = handle.read(size) + data
    all_scanned = data.splitlines()
    scanned = all_scanned[-scan_max_lines:]
    truncated = position > 0 or len(all_scanned) > len(scanned)
    corrupt = 0
    candidates: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    # Walk newest-first so a duplicate event id/fingerprint retains the latest
    # occurrence, then sort the final selection chronologically.
    for raw in reversed(scanned):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
            if not isinstance(event, dict):
                raise ValueError("history event is not an object")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            corrupt += 1
            continue
        event_type = normalize_event_type(event)
        if event_type not in ALLOWED_EVENT_TYPES:
            continue
        compact = compact_event(event, event_type)
        event_id = compact["event_id"]
        fingerprint = business_fingerprint(compact)
        if event_id in seen_event_ids or fingerprint in seen_fingerprints:
            continue
        seen_event_ids.add(event_id)
        seen_fingerprints.add(fingerprint)
        compact["_priority"] = (
            90
            if event_type == "memory_added"
            and compact.get("memory_type") == "commitment"
            else PRIORITY.get(event_type, 0)
        )
        candidates.append(compact)
    ranked = sorted(
        candidates,
        key=lambda item: (
            int(item["_priority"]),
            str(item["timestamp"]),
            str(item["event_id"]),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    used = 0
    for item in ranked:
        clean = {key: value for key, value in item.items() if key != "_priority"}
        size = len(json.dumps(clean, ensure_ascii=False, separators=(",", ":")))
        if len(selected) >= max_events or used + size > max_chars:
            truncated = True
            continue
        selected.append(clean)
        used += size
    selected.sort(key=lambda item: (str(item["timestamp"]), str(item["event_id"])))
    if len(candidates) > len(selected):
        truncated = True
    return {
        "events": selected,
        "truncated": truncated,
        "skipped_corrupt_lines": corrupt,
    }
