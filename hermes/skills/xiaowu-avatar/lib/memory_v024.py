from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Callable

MEMORY_TYPES = {
    "relationship_event",
    "shared_experience",
    "user_preference",
    "commitment",
    "important_fact",
    "open_thread",
}
MEMORY_STATUSES = {"active", "superseded", "resolved", "archived"}
CATEGORY_BY_TYPE = {
    "relationship_event": "relationship_events",
    "shared_experience": "shared_experiences",
    "user_preference": "user_preferences",
    "commitment": "commitments",
    "important_fact": "important_facts",
    "open_thread": "open_threads",
}
CATEGORIES = tuple(CATEGORY_BY_TYPE.values())


class MemoryValidationError(ValueError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def dedupe_key(memory_type: str, text: str) -> str:
    return hashlib.sha256(
        canonical_bytes({"type": memory_type, "text": normalize_text(text)})
    ).hexdigest()


def validate_memory_event(event: dict[str, Any], max_text_chars: int) -> None:
    required = {
        "memory_id",
        "created_at",
        "type",
        "importance",
        "text",
        "source_event_id",
        "status",
        "supersedes",
        "dedupe_key",
    }
    if not isinstance(event, dict) or required - set(event):
        raise MemoryValidationError("memory event is missing required fields")
    if not isinstance(event["memory_id"], str) or not event["memory_id"]:
        raise MemoryValidationError("memory_id must be a non-empty string")
    if event["type"] not in MEMORY_TYPES:
        raise MemoryValidationError(f"invalid memory type: {event['type']!r}")
    importance = event["importance"]
    if isinstance(importance, bool) or not isinstance(importance, int):
        raise MemoryValidationError("importance must be an integer")
    if not 1 <= importance <= 5:
        raise MemoryValidationError("importance must be between 1 and 5")
    if not isinstance(event["text"], str) or not normalize_text(event["text"]):
        raise MemoryValidationError("memory text must not be empty")
    if len(event["text"]) > max_text_chars:
        raise MemoryValidationError(
            f"memory text exceeds {max_text_chars} characters"
        )
    if event["status"] not in MEMORY_STATUSES:
        raise MemoryValidationError(f"invalid memory status: {event['status']!r}")
    for key in ("source_event_id", "supersedes"):
        if event[key] is not None and not isinstance(event[key], str):
            raise MemoryValidationError(f"{key} must be a string or null")
    expected = dedupe_key(event["type"], event["text"])
    if event["dedupe_key"] != expected:
        raise MemoryValidationError("invalid memory dedupe_key")


def load_events(path: Path, max_text_chars: int) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                validate_memory_event(event, max_text_chars)
            except (json.JSONDecodeError, MemoryValidationError) as exc:
                raise MemoryValidationError(
                    f"invalid memory event at line {line_number}: {exc}"
                ) from exc
            events.append(event)
    return events


def event_jsonl(events: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        for event in events
    )


def build_summary(
    events: list[dict[str, Any]],
    *,
    generated_at: str,
    per_category_limit: int,
    max_chars: int,
) -> dict[str, Any]:
    if per_category_limit < 1 or max_chars < 1:
        raise MemoryValidationError("summary limits must be positive")
    active = [event for event in events if event["status"] == "active"]
    by_category: dict[str, list[dict[str, Any]]] = {
        category: [] for category in CATEGORIES
    }
    seen: set[str] = set()
    ordered = sorted(
        active,
        key=lambda event: (
            -int(event["importance"]),
            str(event["created_at"]),
            str(event["memory_id"]),
        ),
        reverse=False,
    )
    # Importance is already descending via negative value. For equal
    # importance, reverse time deterministically.
    ordered = sorted(
        ordered,
        key=lambda event: (
            int(event["importance"]),
            str(event["created_at"]),
            str(event["memory_id"]),
        ),
        reverse=True,
    )
    used_chars = 0
    truncated = False
    for event in ordered:
        key = event["dedupe_key"]
        category = CATEGORY_BY_TYPE[event["type"]]
        if key in seen:
            continue
        if len(by_category[category]) >= per_category_limit:
            truncated = True
            continue
        remaining = max_chars - used_chars
        if remaining <= 0:
            truncated = True
            continue
        text = event["text"]
        if len(text) > remaining:
            text = text[:remaining]
            truncated = True
        item = {
            "memory_id": event["memory_id"],
            "importance": event["importance"],
            "text": text,
            "created_at": event["created_at"],
            "dedupe_key": key,
        }
        by_category[category].append(item)
        seen.add(key)
        used_chars += len(text)
    summary = {
        "schema_version": 1,
        "generated_at": generated_at,
        "source_event_count": len(events),
        **by_category,
        "used_chars": used_chars,
        "max_chars": max_chars,
        "truncated": truncated,
    }
    validate_summary(summary, max_chars)
    return summary


def validate_summary(summary: dict[str, Any], max_chars: int) -> None:
    if not isinstance(summary, dict) or summary.get("schema_version") != 1:
        raise MemoryValidationError("invalid memory summary schema")
    if summary.get("used_chars", 0) > max_chars:
        raise MemoryValidationError("memory summary exceeds character budget")
    for category in CATEGORIES:
        if not isinstance(summary.get(category), list):
            raise MemoryValidationError(f"summary.{category} must be an array")


def empty_summary(generated_at: str, max_chars: int) -> dict[str, Any]:
    return build_summary(
        [],
        generated_at=generated_at,
        per_category_limit=1,
        max_chars=max_chars,
    )


def make_event(
    *,
    memory_type: str,
    importance: int,
    text: str,
    source_event_id: str | None,
    supersedes: str | None,
    created_at: str,
    max_text_chars: int,
) -> dict[str, Any]:
    normalized = normalize_text(text)
    event = {
        "memory_id": str(uuid.uuid4()),
        "created_at": created_at,
        "type": memory_type,
        "importance": importance,
        "text": normalized,
        "source_event_id": source_event_id,
        "status": "active",
        "supersedes": supersedes,
        "dedupe_key": dedupe_key(memory_type, normalized),
    }
    validate_memory_event(event, max_text_chars)
    return event


def find_event(events: list[dict[str, Any]], memory_id: str) -> int:
    for index, event in enumerate(events):
        if event["memory_id"] == memory_id:
            return index
    raise MemoryValidationError(f"memory event not found: {memory_id}")


def candidate_add(
    events: list[dict[str, Any]],
    *,
    memory_type: str,
    importance: int,
    text: str,
    source_event_id: str | None,
    created_at: str,
    max_text_chars: int,
    supersedes: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], bool]:
    event = make_event(
        memory_type=memory_type,
        importance=importance,
        text=text,
        source_event_id=source_event_id,
        supersedes=supersedes,
        created_at=created_at,
        max_text_chars=max_text_chars,
    )
    for existing in events:
        if (
            existing["status"] == "active"
            and existing["dedupe_key"] == event["dedupe_key"]
        ):
            return copy.deepcopy(events), copy.deepcopy(existing), True
    candidate = copy.deepcopy(events)
    candidate.append(event)
    return candidate, event, False


def candidate_supersede(
    events: list[dict[str, Any]],
    memory_id: str,
    *,
    text: str,
    memory_type: str | None,
    importance: int | None,
    source_event_id: str | None,
    created_at: str,
    max_text_chars: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate = copy.deepcopy(events)
    index = find_event(candidate, memory_id)
    old = candidate[index]
    if old["status"] != "active":
        raise MemoryValidationError("only active memory can be superseded")
    candidate[index]["status"] = "superseded"
    candidate, new_event, duplicate = candidate_add(
        candidate,
        memory_type=memory_type or old["type"],
        importance=importance if importance is not None else old["importance"],
        text=text,
        source_event_id=source_event_id,
        created_at=created_at,
        max_text_chars=max_text_chars,
        supersedes=memory_id,
    )
    if duplicate:
        raise MemoryValidationError("superseding memory duplicates an active event")
    return candidate, new_event


def candidate_resolve(
    events: list[dict[str, Any]], memory_id: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate = copy.deepcopy(events)
    index = find_event(candidate, memory_id)
    if candidate[index]["status"] != "active":
        raise MemoryValidationError("only active memory can be resolved")
    candidate[index]["status"] = "resolved"
    return candidate, copy.deepcopy(candidate[index])


def commit_memory_transaction(
    *,
    events_path: Path,
    summary_path: Path,
    backups_dir: Path,
    old_events: list[dict[str, Any]],
    candidate_events: list[dict[str, Any]],
    candidate_summary: dict[str, Any],
    atomic_text_writer: Callable[[Path, str], None],
    atomic_json_writer: Callable[[Path, Any], None],
    now: str,
) -> str:
    # Candidate summary is fully validated before any write or backup.
    validate_summary(candidate_summary, int(candidate_summary["max_chars"]))
    backups_dir.mkdir(parents=True, exist_ok=True)
    backup_path = ""
    if summary_path.is_file():
        old_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        backup = backups_dir / (
            f"summary-{now.replace(':', '').replace('+', '_')}-{uuid.uuid4().hex[:8]}.json"
        )
        atomic_json_writer(
            backup,
            {
                "created_at": now,
                "source": "memory-summary-before-rebuild",
                "summary": old_summary,
            },
        )
        backup.chmod(0o600)
        backup_path = str(backup)
    atomic_text_writer(events_path, event_jsonl(candidate_events))
    try:
        atomic_json_writer(summary_path, candidate_summary)
    except Exception:
        atomic_text_writer(events_path, event_jsonl(old_events))
        raise
    return backup_path
