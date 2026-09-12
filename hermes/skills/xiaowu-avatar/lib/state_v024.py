from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = 2
TRANSITION_TYPES = {"continue", "partial", "new_scene"}
ACTION_TYPES = {
    "shower",
    "replace_outfit",
    "layer_outfit",
    "add_accessory",
    "move",
    "start_activity",
    "set_pose",
    "set_action",
    "set_expression",
    "hold_object",
    "clear_held_objects",
}

RELATIONSHIP_REQUIRED = {"role", "stage", "trust", "intimacy", "attachment"}
RELATIONSHIP_ALLOWED = RELATIONSHIP_REQUIRED | {
    "current_dynamic",
    "shared_commitments",
    "important_boundaries",
    "preferred_names",
    "last_major_change",
    "legacy_relationship_stage",
    "legacy_affection",
}


class StateValidationError(ValueError):
    pass


def validate_relationship(relationship: Any) -> None:
    if not isinstance(relationship, dict):
        raise StateValidationError("relationship must be a JSON object")
    missing = RELATIONSHIP_REQUIRED - set(relationship)
    if missing:
        raise StateValidationError(
            f"relationship missing required fields: {sorted(missing)}"
        )
    unknown = set(relationship) - RELATIONSHIP_ALLOWED
    if unknown:
        raise StateValidationError(
            f"relationship contains unknown fields: {sorted(unknown)}"
        )
    for key in ("role", "stage"):
        if not isinstance(relationship[key], str):
            raise StateValidationError(f"relationship.{key} must be a string")
    for key in ("trust", "intimacy", "attachment"):
        value = relationship[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise StateValidationError(f"relationship.{key} must be an integer")
        if not 0 <= value <= 100:
            raise StateValidationError(f"relationship.{key} must be between 0 and 100")
    optional_strings = ("current_dynamic",)
    for key in optional_strings:
        if key in relationship and not isinstance(relationship[key], str):
            raise StateValidationError(f"relationship.{key} must be a string")
    for key in ("shared_commitments", "important_boundaries"):
        if key in relationship and (
            not isinstance(relationship[key], list)
            or not all(isinstance(item, str) for item in relationship[key])
        ):
            raise StateValidationError(f"relationship.{key} must be an array of strings")
    if "preferred_names" in relationship and not isinstance(
        relationship["preferred_names"], dict
    ):
        raise StateValidationError("relationship.preferred_names must be an object")
    if (
        "last_major_change" in relationship
        and relationship["last_major_change"] is not None
        and not isinstance(relationship["last_major_change"], str)
    ):
        raise StateValidationError(
            "relationship.last_major_change must be a string or null"
        )


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def default_relationship(internal: dict[str, Any]) -> dict[str, Any]:
    affection = _integer(internal.get("affection"))
    stage = internal.get("relationship_stage", "")
    return {
        "role": "",
        "stage": str(stage),
        "trust": _integer(internal.get("trust")),
        "intimacy": affection,
        "attachment": affection,
        "current_dynamic": "",
        "shared_commitments": [],
        "important_boundaries": [],
        "preferred_names": {},
        "last_major_change": None,
        "legacy_relationship_stage": copy.deepcopy(stage),
        "legacy_affection": copy.deepcopy(internal.get("affection", 0)),
    }


def migrate_v1_to_v2(old: dict[str, Any]) -> dict[str, Any]:
    if _integer(old.get("schema_version"), 1) >= SCHEMA_VERSION:
        candidate = copy.deepcopy(old)
        validate_state(candidate)
        return candidate
    required = {"character", "visual", "internal", "continuity", "memory"}
    if not required.issubset(old):
        raise StateValidationError(
            f"legacy state missing required keys: {sorted(required - set(old))}"
        )
    candidate = copy.deepcopy(old)
    visual = candidate["visual"]
    internal = candidate["internal"]
    continuity = candidate["continuity"]
    memory = candidate["memory"]
    scene_id = f"legacy-r{_integer(continuity.get('revision')):04d}"
    candidate.update(
        {
            "schema_version": SCHEMA_VERSION,
            "relationship": default_relationship(internal),
            "scene": {
                "scene_id": scene_id,
                "location": _text(visual.get("scene")),
                "environment": _text(visual.get("scene")),
                "activity": _text(internal.get("current_activity")),
                "time_of_day": "",
                "props": [],
            },
            "appearance": {
                "outfit": {
                    "base": [_text(visual.get("outfit"))]
                    if _text(visual.get("outfit"))
                    else [],
                    "legwear": _text(visual.get("legwear")),
                    "footwear": _text(visual.get("footwear")),
                    "outerwear": [],
                },
                "hair": _text(visual.get("hair")),
                "makeup": _text(visual.get("makeup")),
                "accessories": [],
            },
            "physical": {
                "cleanliness": "unspecified",
                "wetness": "dry",
                "sweat": "none",
                "dirt": "none",
                "fatigue": "none",
                "injury": [],
            },
            "emotion": {
                "baseline": _text(internal.get("mood")),
                "current": _text(internal.get("mood")),
                "intensity": 0,
                "reason": "",
                "persist_across_scenes": False,
            },
            "presentation": {
                "pose": _text(visual.get("pose")),
                "action": _text(visual.get("action")),
                "expression": _text(visual.get("expression")),
                "gaze": "",
                "held_objects": [],
                "lighting": _text(visual.get("lighting")),
                "camera": _text(visual.get("camera")),
            },
        }
    )
    candidate["continuity"].update(
        {
            "mode": "continue",
            "scene_id": scene_id,
            "source_revision": _integer(continuity.get("revision")),
            "source_job": _text(continuity.get("last_job_id")),
            "inherit_fields": [],
        }
    )
    candidate["memory"] = {
        **memory,
        "long_term_summary": _text(memory.get("long_term_summary")),
        "shared_experiences": copy.deepcopy(memory.get("shared_experiences", [])),
        "stable_user_preferences": copy.deepcopy(
            memory.get("stable_user_preferences", [])
        ),
        "unfinished_items": copy.deepcopy(memory.get("unfinished_items", [])),
    }
    sync_legacy_view(candidate)
    validate_state(candidate)
    return candidate


def validate_state(state: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "character",
        "visual",
        "internal",
        "continuity",
        "memory",
        "relationship",
        "scene",
        "appearance",
        "physical",
        "emotion",
        "presentation",
    }
    missing = required - set(state)
    if missing:
        raise StateValidationError(f"state missing keys: {sorted(missing)}")
    if _integer(state["schema_version"]) != SCHEMA_VERSION:
        raise StateValidationError("unsupported state schema version")
    if not isinstance(state["relationship"], dict) or not isinstance(
        state["memory"], dict
    ):
        raise StateValidationError("relationship and memory must be objects")
    if state["continuity"].get("last_image") is None:
        raise StateValidationError("continuity.last_image must remain readable")
    outfit = state["appearance"].get("outfit")
    if not isinstance(outfit, dict) or not isinstance(outfit.get("base"), list):
        raise StateValidationError("appearance.outfit.base must be an array")
    for key in ("outerwear",):
        if not isinstance(outfit.get(key), list):
            raise StateValidationError(f"appearance.outfit.{key} must be an array")
    for key in ("accessories",):
        if not isinstance(state["appearance"].get(key), list):
            raise StateValidationError(f"appearance.{key} must be an array")
    if not isinstance(state["presentation"].get("held_objects"), list):
        raise StateValidationError("presentation.held_objects must be an array")
    if not isinstance(state["physical"].get("injury"), list):
        raise StateValidationError("physical.injury must be an array")


def outfit_text(outfit: dict[str, Any]) -> str:
    parts = [
        *[_text(item) for item in outfit.get("base", []) if _text(item)],
        *[_text(item) for item in outfit.get("outerwear", []) if _text(item)],
    ]
    return "，".join(parts)


def sync_legacy_view(state: dict[str, Any]) -> None:
    """Keep the v0.2.3 read/write surface valid for old callers."""
    appearance = state["appearance"]
    presentation = state["presentation"]
    scene = state["scene"]
    relationship = state["relationship"]
    emotion = state["emotion"]
    visual = state["visual"]
    internal = state["internal"]
    visual.update(
        {
            "outfit": outfit_text(appearance["outfit"]),
            "legwear": _text(appearance["outfit"].get("legwear")),
            "footwear": _text(appearance["outfit"].get("footwear")),
            "hair": _text(appearance.get("hair")),
            "makeup": _text(appearance.get("makeup")),
            "expression": _text(presentation.get("expression")),
            "pose": _text(presentation.get("pose")),
            "action": _text(presentation.get("action")),
            "scene": _text(scene.get("location")) or _text(scene.get("environment")),
            "lighting": _text(presentation.get("lighting")),
            "camera": _text(presentation.get("camera")),
        }
    )
    if "outerwear" in visual:
        visual["outerwear"] = "，".join(
            _text(item)
            for item in appearance["outfit"].get("outerwear", [])
            if _text(item)
        )
    if "accessories" in visual:
        visual["accessories"] = "，".join(
            _text(item) for item in appearance.get("accessories", []) if _text(item)
        )
    internal.update(
        {
            "mood": _text(emotion.get("current")) or _text(emotion.get("baseline")),
            "current_activity": _text(scene.get("activity")),
            "relationship_stage": copy.deepcopy(
                relationship.get(
                    "legacy_relationship_stage", relationship.get("stage", "")
                )
            ),
            "affection": copy.deepcopy(
                relationship.get("legacy_affection", relationship.get("intimacy", 0))
            ),
            "trust": _integer(relationship.get("trust")),
        }
    )


def validate_transition(spec: dict[str, Any]) -> None:
    if not isinstance(spec, dict):
        raise StateValidationError("transition must be a JSON object")
    allowed = {"transition_type", "actions"}
    unknown = set(spec) - allowed
    if unknown:
        raise StateValidationError(f"unknown transition fields: {sorted(unknown)}")
    transition_type = spec.get("transition_type")
    if transition_type not in TRANSITION_TYPES:
        raise StateValidationError(f"invalid transition_type: {transition_type!r}")
    actions = spec.get("actions")
    if not isinstance(actions, list) or not actions:
        raise StateValidationError("actions must be a non-empty array")
    allowed_fields = {
        "shower": {"type", "final_wetness"},
        "replace_outfit": {"type", "preset", "items", "legwear", "footwear"},
        "layer_outfit": {"type", "item"},
        "add_accessory": {"type", "item"},
        "move": {"type", "location", "environment", "time_of_day"},
        "start_activity": {"type", "activity"},
        "set_pose": {"type", "pose"},
        "set_action": {"type", "action"},
        "set_expression": {"type", "expression", "intensity", "reason", "persist"},
        "hold_object": {"type", "object"},
        "clear_held_objects": {"type"},
    }
    for action in actions:
        if not isinstance(action, dict) or action.get("type") not in ACTION_TYPES:
            raise StateValidationError(f"invalid transition action: {action!r}")
        unknown_action_fields = set(action) - allowed_fields[action["type"]]
        if unknown_action_fields:
            raise StateValidationError(
                f"unknown fields for {action['type']}: {sorted(unknown_action_fields)}"
            )


def _new_scene(candidate: dict[str, Any]) -> None:
    old_scene_id = _text(candidate["scene"].get("scene_id"))
    revision = _integer(candidate["continuity"].get("revision"))
    injury = copy.deepcopy(candidate["physical"].get("injury", []))
    hair = _text(candidate["appearance"].get("hair"))
    makeup = _text(candidate["appearance"].get("makeup"))
    candidate["scene"] = {
        "scene_id": f"scene-r{revision + 1:04d}",
        "location": "",
        "environment": "",
        "activity": "",
        "time_of_day": "",
        "props": [],
    }
    candidate["appearance"] = {
        "outfit": {"base": [], "legwear": "", "footwear": "", "outerwear": []},
        "hair": hair,
        "makeup": makeup,
        "accessories": [],
    }
    candidate["physical"] = {
        "cleanliness": "unspecified",
        "wetness": "dry",
        "sweat": "none",
        "dirt": "none",
        "fatigue": "none",
        "injury": injury,
    }
    candidate["presentation"] = {
        "pose": "",
        "action": "",
        "expression": "",
        "gaze": "",
        "held_objects": [],
        "lighting": "",
        "camera": "",
    }
    if not candidate["emotion"].get("persist_across_scenes", False):
        candidate["emotion"].update(
            {"current": candidate["emotion"].get("baseline", ""), "intensity": 0, "reason": ""}
        )
    candidate["continuity"].update(
        {
            "mode": "new_scene",
            "scene_id": candidate["scene"]["scene_id"],
            "source_revision": revision,
            "source_scene_id": old_scene_id,
            "source_job": "",
            "inherit_fields": [
                "character",
                "relationship",
                "memory",
                "appearance.hair",
                "appearance.makeup",
                "physical.injury",
            ],
        }
    )


def apply_transition(state: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    validate_state(state)
    validate_transition(spec)
    candidate = copy.deepcopy(state)
    relationship_before = copy.deepcopy(candidate["relationship"])
    memory_before = copy.deepcopy(candidate["memory"])
    transition_type = spec["transition_type"]
    if transition_type == "new_scene":
        _new_scene(candidate)
    else:
        candidate["continuity"]["mode"] = transition_type

    actions = spec["actions"]
    shower_indexes = [
        index for index, action in enumerate(actions) if action["type"] == "shower"
    ]
    for index, action in enumerate(actions):
        kind = action["type"]
        if kind == "shower":
            later_departure = any(
                item["type"] in {"replace_outfit", "move", "start_activity"}
                for item in actions[index + 1 :]
            )
            wetness = action.get("final_wetness") or (
                "dry" if later_departure else "damp"
            )
            if wetness not in {"damp", "dry", "wet"}:
                raise StateValidationError("shower.final_wetness must be damp, dry, or wet")
            candidate["physical"].update(
                {
                    "cleanliness": "clean",
                    "sweat": "none",
                    "dirt": "none",
                    "wetness": wetness,
                }
            )
        elif kind == "replace_outfit":
            preset = _text(action.get("preset"))
            items = action.get("items")
            if items is None:
                items = [preset] if preset else []
            if not isinstance(items, list) or not all(
                isinstance(item, str) and item.strip() for item in items
            ):
                raise StateValidationError("replace_outfit items must be non-empty strings")
            candidate["appearance"]["outfit"] = {
                "base": copy.deepcopy(items),
                "legwear": _text(action.get("legwear")),
                "footwear": _text(action.get("footwear")),
                "outerwear": [],
            }
        elif kind == "layer_outfit":
            item = _text(action.get("item")).strip()
            if not item:
                raise StateValidationError("layer_outfit.item is required")
            candidate["appearance"]["outfit"]["outerwear"].append(item)
        elif kind == "add_accessory":
            item = _text(action.get("item")).strip()
            if not item:
                raise StateValidationError("add_accessory.item is required")
            candidate["appearance"]["accessories"].append(item)
        elif kind == "move":
            location = _text(action.get("location")).strip()
            if not location:
                raise StateValidationError("move.location is required")
            candidate["scene"]["location"] = location
            candidate["scene"]["environment"] = _text(
                action.get("environment", location)
            )
            if "time_of_day" in action:
                candidate["scene"]["time_of_day"] = _text(action["time_of_day"])
        elif kind == "start_activity":
            activity = _text(action.get("activity")).strip()
            if not activity:
                raise StateValidationError("start_activity.activity is required")
            candidate["scene"]["activity"] = activity
            candidate["presentation"]["action"] = activity
        elif kind == "set_pose":
            candidate["presentation"]["pose"] = _text(action.get("pose")).strip()
        elif kind == "set_action":
            candidate["presentation"]["action"] = _text(action.get("action")).strip()
        elif kind == "set_expression":
            candidate["presentation"]["expression"] = _text(
                action.get("expression")
            ).strip()
            if "intensity" in action:
                candidate["emotion"]["intensity"] = _integer(action["intensity"])
            if "reason" in action:
                candidate["emotion"]["reason"] = _text(action["reason"])
            if "persist" in action:
                candidate["emotion"]["persist_across_scenes"] = bool(action["persist"])
        elif kind == "hold_object":
            item = _text(action.get("object")).strip()
            if not item:
                raise StateValidationError("hold_object.object is required")
            candidate["presentation"]["held_objects"].append(item)
        elif kind == "clear_held_objects":
            candidate["presentation"]["held_objects"] = []

    # A composite shower followed by departure must end dry even if an
    # intermediate action would otherwise leave the character damp.
    if shower_indexes and any(
        item["type"] in {"move", "start_activity"}
        for item in actions[shower_indexes[-1] + 1 :]
    ):
        candidate["physical"]["wetness"] = "dry"
    if candidate["relationship"] != relationship_before:
        raise StateValidationError("scene transition attempted to alter relationship")
    if candidate["memory"] != memory_before:
        raise StateValidationError("scene transition attempted to alter memory")
    sync_legacy_view(candidate)
    validate_state(candidate)
    return candidate


def migrate_state_file(
    state_path: Path,
    history_path: Path,
    backup_dir: Path,
    *,
    atomic_writer: Callable[[Path, Any], None],
    append_event: Callable[[Path, Any], None],
    now: Callable[[], str],
) -> tuple[dict[str, Any], bool, str]:
    old = json.loads(state_path.read_text(encoding="utf-8"))
    if _integer(old.get("schema_version"), 1) >= SCHEMA_VERSION:
        validate_state(old)
        return old, False, ""
    candidate = migrate_v1_to_v2(old)
    backup_dir.mkdir(parents=True, exist_ok=True)
    revision = _integer(old.get("continuity", {}).get("revision"))
    backup = backup_dir / f"state-v0.2.3-pre-migration-r{revision:04d}.json"
    if not backup.exists():
        shutil.copy2(state_path, backup)
    # Re-read to detect a concurrent state change before the atomic write.
    if json.loads(state_path.read_text(encoding="utf-8")) != old:
        raise StateValidationError("state changed during migration; refusing write")
    atomic_writer(state_path, candidate)
    append_event(
        history_path,
        {
            "at": now(),
            "event": "migration",
            "from_schema": _integer(old.get("schema_version"), 1),
            "to_schema": SCHEMA_VERSION,
            "revision": revision,
            "backup": str(backup),
        },
    )
    return candidate, True, str(backup)


def reset_scene(state: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(state)
    relationship = copy.deepcopy(state["relationship"])
    memory = copy.deepcopy(state["memory"])
    _new_scene(candidate)
    candidate["scene"]["scene_id"] = ""
    candidate["continuity"]["scene_id"] = ""
    candidate["relationship"] = relationship
    candidate["memory"] = memory
    sync_legacy_view(candidate)
    validate_state(candidate)
    return candidate


def reset_runtime_state(
    state: dict[str, Any], default_v2: dict[str, Any]
) -> dict[str, Any]:
    candidate = copy.deepcopy(default_v2)
    candidate["relationship"] = copy.deepcopy(state["relationship"])
    candidate["memory"] = copy.deepcopy(state["memory"])
    candidate["continuity"]["revision"] = _integer(
        state["continuity"].get("revision")
    )
    candidate["continuity"]["last_image"] = _text(
        state["continuity"].get("last_image")
    )
    candidate["continuity"]["last_prompt_id"] = _text(
        state["continuity"].get("last_prompt_id")
    )
    candidate["continuity"]["last_prompt_sha256"] = _text(
        state["continuity"].get("last_prompt_sha256")
    )
    sync_legacy_view(candidate)
    validate_state(candidate)
    return candidate
