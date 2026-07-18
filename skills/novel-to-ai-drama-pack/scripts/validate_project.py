from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from project_io import load_json


REQUIRED_TOP_LEVEL = frozenset(
    {
        "schema_version",
        "project",
        "source",
        "analysis",
        "characters",
        "scenes",
        "props",
        "foods",
        "episodes",
        "assets",
        "generation_settings",
        "generation_runs",
    }
)

_PROJECT_ID = re.compile(r"^PRJ\d{3}$")
_OBJECT_IDS = {
    "character": re.compile(r"^C\d{3}$"),
    "scene": re.compile(r"^S\d{3}$"),
    "prop": re.compile(r"^P\d{3}$"),
    "food": re.compile(r"^F\d{3}$"),
}
_EPISODE_ID = re.compile(r"^E\d{3}$")
_SHOT_ID = re.compile(r"^E\d{3}_SH\d{3}$")
_ASSET_ID = re.compile(r"^[A-Z][A-Z0-9_]*$")
_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")
_FILE_VERSION = re.compile(r"_V(\d{3})(?=\.[^.]+$)")
_TOKEN_VERSION = re.compile(r"_V(\d{3})$")

_ASSET_OWNER = {
    "style_reference": "project",
    "character_sheet": "character",
    "expression_sheet": "character",
    "action_sheet": "character",
    "voice_sample": "character",
    "scene_sheet": "scene",
    "prop_sheet": "prop",
    "food_image": "food",
    "shot_sample": "shot",
    "dialogue_audio": "shot",
}
_AUDIO_ASSETS = {"voice_sample", "dialogue_audio"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_AUDIO_EXTENSIONS = {".wav", ".mp3", ".aac", ".flac", ".opus"}
_ASSET_STATUSES = {
    "draft",
    "confirmed",
    "generating",
    "completed",
    "redo",
    "discarded",
}
_PROJECT_STATUSES = _ASSET_STATUSES | {"core_assets_confirmed", "production"}
_MEDIA_GATE_STATUSES = {"core_assets_confirmed", "production", "completed"}
_SCRIPT_GATE_STATUSES = {"production", "completed"}


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(
        value, float
    )


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _contains_delimited_id(value: str, owner_id: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9]){re.escape(owner_id)}(?![A-Za-z0-9])"
    return re.search(pattern, value) is not None


def _require_object(
    value: object, path: str, errors: list[str]
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        errors.append(f"{path} must be an object")
        return None
    return value


def _require_list(
    value: object, path: str, errors: list[str]
) -> list[Any] | None:
    if not isinstance(value, list):
        errors.append(f"{path} must be a list")
        return None
    return value


def _require_nonempty_string(
    obj: dict[str, Any], field: str, path: str, errors: list[str]
) -> str | None:
    value = obj.get(field)
    if not _is_nonempty_string(value):
        errors.append(f"{path}.{field} must be a nonempty string")
        return None
    return value


def _validate_source(value: object, errors: list[str]) -> dict[str, Any] | None:
    source = _require_object(value, "source", errors)
    if source is None:
        return None

    for field in ("input_type", "sha256"):
        _require_nonempty_string(source, field, "source", errors)
    file_name = source.get("file_name")
    if file_name is not None and not _is_nonempty_string(file_name):
        errors.append("source.file_name must be null or a nonempty string")
    character_count = source.get("character_count")
    if not _is_integer(character_count) or character_count < 0:
        errors.append("source.character_count must be a nonnegative integer")
    _require_list(source.get("chapter_index"), "source.chapter_index", errors)
    coverage = source.get("coverage")
    if not _is_number(coverage) or not 0 <= coverage <= 1:
        errors.append("source.coverage must be a number from 0 to 1")
    return source


def _validate_analysis(value: object, errors: list[str]) -> None:
    analysis = _require_object(value, "analysis", errors)
    if analysis is None:
        return
    _require_nonempty_string(analysis, "world_bible", "analysis", errors)
    for field in ("timeline", "story_arc", "adaptation_decisions"):
        _require_list(analysis.get(field), f"analysis.{field}", errors)


def _validate_generation_settings(
    value: object, errors: list[str]
) -> dict[str, Any] | None:
    settings = _require_object(value, "generation_settings", errors)
    if settings is None:
        return None
    for field in ("aspect_ratio", "image_provider", "voice_provider"):
        _require_nonempty_string(settings, field, "generation_settings", errors)
    sample_count = settings.get("sample_episode_count")
    if not _is_integer(sample_count) or sample_count < 0:
        errors.append(
            "generation_settings.sample_episode_count must be a nonnegative integer"
        )
    script_count = settings.get("script_episode_count")
    if not _is_integer(script_count) or script_count <= 0:
        errors.append(
            "generation_settings.script_episode_count must be a positive integer"
        )
    return settings


def _validate_named_objects(
    value: object,
    collection: str,
    kind: str,
    id_field: str,
    errors: list[str],
    all_object_ids: set[str],
) -> tuple[list[dict[str, Any]], set[str]]:
    items = _require_list(value, collection, errors)
    if items is None:
        return [], set()

    valid_items: list[dict[str, Any]] = []
    ids: set[str] = set()
    id_pattern = _OBJECT_IDS[kind]
    for index, raw_item in enumerate(items):
        path = f"{collection}[{index}]"
        item = _require_object(raw_item, path, errors)
        if item is None:
            continue
        valid_items.append(item)
        object_id = item.get(id_field)
        if not isinstance(object_id, str) or not id_pattern.fullmatch(object_id):
            errors.append(f"{path}.{id_field} has invalid {kind} id")
        else:
            if object_id in ids or object_id in all_object_ids:
                errors.append(f"duplicate object id: {object_id}")
            ids.add(object_id)
            all_object_ids.add(object_id)
        _require_nonempty_string(item, "name", path, errors)
        _require_nonempty_string(item, "importance", path, errors)

        if kind == "character":
            _require_nonempty_string(item, "role", path, errors)
            _require_nonempty_string(item, "appearance", path, errors)
            voice = _require_object(item.get("voice_profile"), f"{path}.voice_profile", errors)
            if voice is not None:
                for field in ("voice", "tone", "pace", "sample_text"):
                    _require_nonempty_string(
                        voice, field, f"{path}.voice_profile", errors
                    )
    return valid_items, ids


def _validate_string_list(
    value: object, path: str, errors: list[str]
) -> list[str]:
    items = _require_list(value, path, errors)
    if items is None:
        return []
    result: list[str] = []
    for index, item in enumerate(items):
        if not _is_nonempty_string(item):
            errors.append(f"{path}[{index}] must be a nonempty string")
        else:
            result.append(item)
    return result


def _validate_episodes(
    value: object,
    errors: list[str],
    references: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], set[str]]:
    raw_episodes = _require_list(value, "episodes", errors)
    if raw_episodes is None:
        return [], set()

    episodes: list[dict[str, Any]] = []
    episode_ids: set[str] = set()
    shot_ids: set[str] = set()
    for episode_index, raw_episode in enumerate(raw_episodes):
        path = f"episodes[{episode_index}]"
        episode = _require_object(raw_episode, path, errors)
        if episode is None:
            continue
        episodes.append(episode)

        expected_number = episode_index + 1
        episode_number = episode.get("episode_number")
        if not _is_integer(episode_number):
            errors.append(f"{path}.episode_number must be a positive integer")
        elif episode_number != expected_number:
            errors.append(f"{path}.episode_number must be {expected_number}")

        episode_id = episode.get("episode_id")
        expected_id = f"E{expected_number:03d}"
        if not isinstance(episode_id, str) or not _EPISODE_ID.fullmatch(episode_id):
            errors.append(f"{path}.episode_id has invalid episode id")
        else:
            if episode_id in episode_ids:
                errors.append(f"duplicate episode id: {episode_id}")
            episode_ids.add(episode_id)
            if episode_id != expected_id:
                errors.append(f"{path}.episode_id must be {expected_id}")

        _require_nonempty_string(episode, "title", path, errors)
        script = episode.get("script")
        if not isinstance(script, str):
            errors.append(f"{path}.script must be a string")

        raw_shots = _require_list(episode.get("shots"), f"{path}.shots", errors)
        if raw_shots is None:
            continue
        if not raw_shots:
            errors.append(f"{path}.shots must contain at least one shot")
        for shot_index, raw_shot in enumerate(raw_shots):
            shot_path = f"{path}.shots[{shot_index}]"
            shot = _require_object(raw_shot, shot_path, errors)
            if shot is None:
                continue
            shot_id = shot.get("shot_id")
            if not isinstance(shot_id, str) or not _SHOT_ID.fullmatch(shot_id):
                errors.append(f"{shot_path}.shot_id has invalid shot id")
            else:
                if shot_id in shot_ids:
                    errors.append(f"duplicate shot id: {shot_id}")
                shot_ids.add(shot_id)
                if isinstance(episode_id, str) and not shot_id.startswith(
                    f"{episode_id}_SH"
                ):
                    errors.append(f"shot id {shot_id} does not belong to {episode_id}")

            character_refs = _validate_string_list(
                shot.get("character_ids"), f"{shot_path}.character_ids", errors
            )
            scene_ref = shot.get("scene_id")
            if not _is_nonempty_string(scene_ref):
                errors.append(f"{shot_path}.scene_id must be a nonempty string")
                scene_ref = None
            prop_refs = _validate_string_list(
                shot.get("prop_ids"), f"{shot_path}.prop_ids", errors
            )
            food_refs = _validate_string_list(
                shot.get("food_ids"), f"{shot_path}.food_ids", errors
            )

            for character_id in character_refs:
                if character_id not in references["character"]:
                    errors.append(
                        f"{shot_path}.character_ids unknown character reference: {character_id}"
                    )
            if scene_ref is not None and scene_ref not in references["scene"]:
                errors.append(
                    f"{shot_path}.scene_id unknown scene reference: {scene_ref}"
                )
            for prop_id in prop_refs:
                if prop_id not in references["prop"]:
                    errors.append(
                        f"{shot_path}.prop_ids unknown prop reference: {prop_id}"
                    )
            for food_id in food_refs:
                if food_id not in references["food"]:
                    errors.append(
                        f"{shot_path}.food_ids unknown food reference: {food_id}"
                    )

            for prompt_field in ("prompt_zh", "prompt_en", "negative_prompt"):
                prompt = shot.get(prompt_field)
                if not isinstance(prompt, str):
                    errors.append(f"{shot_path}.{prompt_field} must be a string")
            for link_field in ("expression_asset_id", "action_asset_id"):
                if link_field not in shot:
                    errors.append(f"{shot_path}.{link_field} is required")
                elif shot[link_field] is not None and not _is_nonempty_string(
                    shot[link_field]
                ):
                    errors.append(
                        f"{shot_path}.{link_field} must be null or a nonempty string"
                    )

    return episodes, shot_ids


def _validate_assets(
    value: object,
    errors: list[str],
    owner_ids: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], set[str]]:
    raw_assets = _require_list(value, "assets", errors)
    if raw_assets is None:
        return [], set()

    assets: list[dict[str, Any]] = []
    asset_ids: set[str] = set()
    asset_keys: set[tuple[str, int]] = set()
    tokens: set[str] = set()

    for index, raw_asset in enumerate(raw_assets):
        path = f"assets[{index}]"
        asset = _require_object(raw_asset, path, errors)
        if asset is None:
            continue
        assets.append(asset)

        asset_id = asset.get("asset_id")
        if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
            errors.append(f"{path}.asset_id must use uppercase letters, digits, and underscores")
            asset_id = None
        else:
            asset_ids.add(asset_id)

        version = asset.get("version")
        valid_version = _is_integer(version) and version > 0
        if not valid_version:
            errors.append(f"{path}.version must be a positive integer")
        elif asset_id is not None:
            key = (asset_id, version)
            if key in asset_keys:
                errors.append(
                    f"duplicate asset (asset_id, version): {asset_id}, {version}"
                )
            asset_keys.add(key)

        asset_type = asset.get("asset_type")
        if not _is_nonempty_string(asset_type):
            errors.append(f"{path}.asset_type must be a nonempty string")
            asset_type = None
        expected_owner = _ASSET_OWNER.get(asset_type) if asset_type is not None else None
        if asset_type is not None and expected_owner is None:
            errors.append(f"{path}.asset_type is not allowed: {asset_type}")

        owner_type = asset.get("owner_type")
        if not _is_nonempty_string(owner_type):
            errors.append(f"{path}.owner_type must be a nonempty string")
        elif expected_owner is not None and owner_type != expected_owner:
            errors.append(
                f"{path}.owner_type must be {expected_owner} for {asset_type}"
            )
        owner_id = asset.get("owner_id")
        if not _is_nonempty_string(owner_id):
            errors.append(f"{path}.owner_id must be a nonempty string")
            owner_id = None
        elif expected_owner is not None and owner_id not in owner_ids[expected_owner]:
            errors.append(f"{path} unknown {expected_owner} owner_id: {owner_id}")

        reference_token = asset.get("reference_token")
        if not _is_nonempty_string(reference_token):
            errors.append(f"{path}.reference_token must be a nonempty string")
            reference_token = None
        else:
            if reference_token in tokens:
                errors.append(f"duplicate asset reference_token: {reference_token}")
            tokens.add(reference_token)

        file_name = asset.get("file_name")
        if not _is_nonempty_string(file_name):
            errors.append(f"{path}.file_name must be a nonempty string")
            file_name = None

        if valid_version and file_name is not None:
            match = _FILE_VERSION.search(file_name)
            if match is None or int(match.group(1)) != version:
                errors.append(f"{path} asset version does not match file name")
        if valid_version and reference_token is not None:
            match = _TOKEN_VERSION.search(reference_token)
            if match is None or int(match.group(1)) != version:
                errors.append(f"{path} asset version does not match reference_token")

        if owner_id is not None:
            if file_name is not None and not _contains_delimited_id(
                file_name, owner_id
            ):
                errors.append(f"{path}.file_name must include owner_id {owner_id}")
            if reference_token is not None and not _contains_delimited_id(
                reference_token, owner_id
            ):
                errors.append(f"{path}.reference_token must include owner_id {owner_id}")

        _validate_string_list(
            asset.get("parent_asset_ids"), f"{path}.parent_asset_ids", errors
        )

        status = asset.get("status")
        if not isinstance(status, str) or status not in _ASSET_STATUSES:
            errors.append(f"{path}.status is not allowed: {status}")
        if status == "completed":
            if not _is_nonempty_string(asset.get("relative_path")):
                errors.append(f"{path}.relative_path must be nonempty when completed")
            checksum = asset.get("checksum")
            if not isinstance(checksum, str) or not _CHECKSUM.fullmatch(checksum):
                errors.append(
                    f"{path}.checksum must be 64 lowercase hexadecimal characters"
                )
            if not _is_nonempty_string(asset.get("prompt")):
                errors.append(f"{path}.prompt must be nonempty when completed")
            if file_name is not None and expected_owner is not None:
                extension = Path(file_name).suffix.lower()
                expected_extensions = (
                    _AUDIO_EXTENSIONS
                    if asset_type in _AUDIO_ASSETS
                    else _IMAGE_EXTENSIONS
                )
                if extension not in expected_extensions:
                    errors.append(
                        f"{path}.file extension is invalid for {asset_type}"
                    )

    for index, asset in enumerate(assets):
        parent_ids = asset.get("parent_asset_ids")
        if not isinstance(parent_ids, list):
            continue
        for parent_id in parent_ids:
            if isinstance(parent_id, str) and parent_id not in asset_ids:
                errors.append(
                    f"assets[{index}].parent_asset_ids unknown asset reference: {parent_id}"
                )
    return assets, asset_ids


def _validate_media_gate(
    project_status: object,
    project_id: str | None,
    characters: list[dict[str, Any]],
    scenes: list[dict[str, Any]],
    props: list[dict[str, Any]],
    foods: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    assets: list[dict[str, Any]],
    sample_count: object,
    errors: list[str],
) -> None:
    if project_status not in _MEDIA_GATE_STATUSES:
        return

    completed: set[tuple[str, str, str]] = set()
    for asset in assets:
        media_key = (
            asset.get("asset_type"),
            asset.get("owner_type"),
            asset.get("owner_id"),
        )
        if asset.get("status") == "completed" and all(
            isinstance(value, str) for value in media_key
        ):
            completed.add(media_key)

    if project_id is not None and (
        "style_reference",
        "project",
        project_id,
    ) not in completed:
        errors.append(f"missing completed style_reference for {project_id}")

    for character in characters:
        importance = character.get("importance")
        if not isinstance(importance, str) or importance not in {"lead", "major"}:
            continue
        owner_id = character.get("character_id")
        if not isinstance(owner_id, str):
            continue
        for asset_type in (
            "character_sheet",
            "expression_sheet",
            "action_sheet",
            "voice_sample",
        ):
            if (asset_type, "character", owner_id) not in completed:
                errors.append(f"missing completed {asset_type} for {owner_id}")

    for items, importance, id_field, asset_type, owner_type in (
        (scenes, "important", "scene_id", "scene_sheet", "scene"),
        (props, "important", "prop_id", "prop_sheet", "prop"),
        (foods, "important", "food_id", "food_image", "food"),
    ):
        for item in items:
            if item.get("importance") != importance:
                continue
            owner_id = item.get(id_field)
            if isinstance(owner_id, str) and (
                asset_type,
                owner_type,
                owner_id,
            ) not in completed:
                errors.append(f"missing completed {asset_type} for {owner_id}")

    if not _is_integer(sample_count) or sample_count < 0:
        return
    for episode in episodes[:sample_count]:
        shots = episode.get("shots")
        if not isinstance(shots, list):
            continue
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            shot_id = shot.get("shot_id")
            if isinstance(shot_id, str) and (
                "shot_sample",
                "shot",
                shot_id,
            ) not in completed:
                errors.append(f"missing completed shot_sample for {shot_id}")


def _validate_script_gate(
    project_status: object,
    episodes: list[dict[str, Any]],
    script_count: object,
    errors: list[str],
) -> None:
    if project_status not in _SCRIPT_GATE_STATUSES:
        return
    if not _is_integer(script_count) or script_count <= 0:
        return
    for episode_index, episode in enumerate(episodes[:script_count]):
        path = f"episodes[{episode_index}]"
        if not _is_nonempty_string(episode.get("script")):
            errors.append(f"{path}.script must be nonempty in {project_status}")
        shots = episode.get("shots")
        if not isinstance(shots, list):
            continue
        for shot_index, shot in enumerate(shots):
            if not isinstance(shot, dict):
                continue
            shot_path = f"{path}.shots[{shot_index}]"
            for field in ("prompt_zh", "prompt_en", "negative_prompt"):
                if not _is_nonempty_string(shot.get(field)):
                    errors.append(
                        f"{shot_path}.{field} must be nonempty in {project_status}"
                    )


def validate_project(data: dict[str, Any]) -> list[str]:
    """Return canonical validation errors in deterministic traversal order."""
    if not isinstance(data, dict):
        return ["project root must be an object"]

    errors: list[str] = []
    keys = set(data)
    missing = sorted(REQUIRED_TOP_LEVEL - keys)
    unexpected = sorted(
        keys - REQUIRED_TOP_LEVEL,
        key=lambda key: (type(key).__name__, repr(key)),
    )
    errors.extend(f"missing top-level key: {key}" for key in missing)
    errors.extend(f"unexpected top-level key: {key}" for key in unexpected)
    if missing:
        return errors

    if data.get("schema_version") != "1.0.0":
        errors.append("schema_version must be 1.0.0")

    project = _require_object(data.get("project"), "project", errors)
    project_id: str | None = None
    project_status: object = None
    target_count: object = None
    if project is not None:
        candidate_id = project.get("project_id")
        if not isinstance(candidate_id, str) or not _PROJECT_ID.fullmatch(candidate_id):
            errors.append("project.project_id must match PRJ followed by three digits")
        else:
            project_id = candidate_id
        _require_nonempty_string(project, "title", "project", errors)
        candidate_status = project.get("status")
        if not isinstance(candidate_status, str) or candidate_status not in _PROJECT_STATUSES:
            errors.append(f"project.status is not allowed: {candidate_status}")
        else:
            project_status = candidate_status
        target_count = project.get("target_episode_count")
        if not _is_integer(target_count) or target_count <= 0:
            errors.append("project.target_episode_count must be a positive integer")

    source = _validate_source(data.get("source"), errors)
    _validate_analysis(data.get("analysis"), errors)
    settings = _validate_generation_settings(data.get("generation_settings"), errors)
    generation_runs = _require_list(
        data.get("generation_runs"), "generation_runs", errors
    )
    if generation_runs is not None:
        for index, generation_run in enumerate(generation_runs):
            _require_object(generation_run, f"generation_runs[{index}]", errors)

    all_object_ids: set[str] = set()
    characters, character_ids = _validate_named_objects(
        data.get("characters"),
        "characters",
        "character",
        "character_id",
        errors,
        all_object_ids,
    )
    scenes, scene_ids = _validate_named_objects(
        data.get("scenes"),
        "scenes",
        "scene",
        "scene_id",
        errors,
        all_object_ids,
    )
    props, prop_ids = _validate_named_objects(
        data.get("props"),
        "props",
        "prop",
        "prop_id",
        errors,
        all_object_ids,
    )
    foods, food_ids = _validate_named_objects(
        data.get("foods"),
        "foods",
        "food",
        "food_id",
        errors,
        all_object_ids,
    )

    references = {
        "character": character_ids,
        "scene": scene_ids,
        "prop": prop_ids,
        "food": food_ids,
    }
    episodes, shot_ids = _validate_episodes(data.get("episodes"), errors, references)

    if _is_integer(target_count) and isinstance(data.get("episodes"), list):
        if target_count != len(data["episodes"]):
            errors.append("project.target_episode_count must equal episodes count")

    sample_count = settings.get("sample_episode_count") if settings else None
    script_count = settings.get("script_episode_count") if settings else None
    if (
        _is_integer(script_count)
        and script_count > 0
        and isinstance(data.get("episodes"), list)
        and script_count > len(data["episodes"])
    ):
        errors.append(
            "generation_settings.script_episode_count cannot exceed episodes count"
        )
    if (
        _is_integer(sample_count)
        and isinstance(data.get("episodes"), list)
        and sample_count > len(data["episodes"])
    ):
        errors.append(
            "generation_settings.sample_episode_count cannot exceed episodes count"
        )
    if project_status == "completed" and source is not None and source.get("coverage") != 1:
        errors.append("source.coverage must be 1 when project is completed")

    owner_ids = {
        "project": {project_id} if project_id is not None else set(),
        "character": character_ids,
        "scene": scene_ids,
        "prop": prop_ids,
        "food": food_ids,
        "shot": shot_ids,
    }
    assets, asset_ids = _validate_assets(data.get("assets"), errors, owner_ids)

    for episode_index, episode in enumerate(episodes):
        shots = episode.get("shots")
        if not isinstance(shots, list):
            continue
        for shot_index, shot in enumerate(shots):
            if not isinstance(shot, dict):
                continue
            for field in ("expression_asset_id", "action_asset_id"):
                linked_id = shot.get(field)
                if isinstance(linked_id, str) and linked_id not in asset_ids:
                    errors.append(
                        f"episodes[{episode_index}].shots[{shot_index}].{field} "
                        f"unknown asset reference: {linked_id}"
                    )

    _validate_media_gate(
        project_status,
        project_id,
        characters,
        scenes,
        props,
        foods,
        episodes,
        assets,
        sample_count,
        errors,
    )
    _validate_script_gate(project_status, episodes, script_count, errors)
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a canonical drama project")
    parser.add_argument("project", type=Path)
    args = parser.parse_args(argv)

    try:
        data = load_json(args.project)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    errors = validate_project(data)
    if errors:
        print("\n".join(errors))
        return 1
    print("VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
