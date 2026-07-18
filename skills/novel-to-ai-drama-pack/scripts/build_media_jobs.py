from __future__ import annotations

import argparse
import copy
import hashlib
import heapq
import json
import os
import re
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Iterable, Sequence

from asset_stages import (
    StageSelectionError,
    active_assets_by_stage,
    select_active_for_episode,
    select_parent_for_child,
)
from project_io import atomic_write_json, load_json
from validate_references import _scan_tokens, validate_references


class MediaJobError(ValueError):
    """Raised when a media manifest cannot be built safely."""


_KIND_PRIORITY = {
    "style_reference": 0,
    "character_sheet": 10,
    "expression_sheet": 20,
    "action_sheet": 21,
    "scene_sheet": 30,
    "prop_sheet": 40,
    "food_image": 41,
    "voice_sample": 50,
    "shot_sample": 60,
    "dialogue_audio": 61,
}

_OUTPUT_FOLDERS = {
    "style_reference": "assets/style",
    "character_sheet": "assets/characters",
    "expression_sheet": "assets/characters",
    "action_sheet": "assets/characters",
    "scene_sheet": "assets/scenes",
    "prop_sheet": "assets/props",
    "food_image": "assets/foods",
    "voice_sample": "assets/audio",
    "shot_sample": "assets/shots",
    "dialogue_audio": "assets/audio/dialogue",
}

_ASSET_PREFIXES = {
    "style_reference": "STYLE",
    "character_sheet": "CHAR",
    "expression_sheet": "EXPR",
    "action_sheet": "ACTION",
    "scene_sheet": "SCENE",
    "prop_sheet": "PROP",
    "food_image": "FOOD",
    "shot_sample": "SHOT",
    "voice_sample": "AUD",
}
_CONTENT_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _positive_version(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _objects(data: dict[str, Any], collection: str, id_field: str) -> list[dict[str, Any]]:
    raw = data.get(collection, [])
    if not isinstance(raw, list):
        raise MediaJobError(f"{collection} must be a list")
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise MediaJobError(f"{collection}[{index}] must be an object")
        object_id = item.get(id_field)
        if not _nonempty(object_id):
            raise MediaJobError(
                f"{collection}[{index}].{id_field} must be a nonempty string"
            )
        object_id = str(object_id)
        if object_id in seen_ids:
            raise MediaJobError(f"duplicate {id_field}: {object_id}")
        seen_ids.add(object_id)
        result.append(item)
    return sorted(result, key=lambda item: str(item.get(id_field, "")))


def _assets(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("assets", [])
    if not isinstance(raw, list):
        raise MediaJobError("assets must be a list")
    result: list[dict[str, Any]] = []
    identities: set[tuple[str, int]] = set()
    identity_values_by_asset_id: dict[str, dict[str, set[str]]] = {}
    for index, asset in enumerate(raw):
        if not isinstance(asset, dict):
            raise MediaJobError(f"assets[{index}] must be an object")
        asset_id = asset.get("asset_id")
        version = _positive_version(asset.get("version"))
        if not _nonempty(asset_id) or version is None:
            raise MediaJobError(
                f"assets[{index}] requires nonempty asset_id and positive version"
            )
        identity = (str(asset_id), version)
        if identity in identities:
            raise MediaJobError(
                f"duplicate asset identity: {asset_id} V{version:03d}"
            )
        identities.add(identity)
        identity_values = identity_values_by_asset_id.setdefault(
            str(asset_id),
            {
                "asset_type": set(),
                "owner_type": set(),
                "owner_id": set(),
            },
        )
        for field in ("asset_type", "owner_type", "owner_id"):
            value = asset.get(field)
            if _nonempty(value):
                identity_values[field].add(str(value))
        result.append(asset)
    for asset_id in sorted(identity_values_by_asset_id):
        identity_values = identity_values_by_asset_id[asset_id]
        for field in ("asset_type", "owner_type", "owner_id"):
            if len(identity_values[field]) > 1:
                raise MediaJobError(
                    f"asset version history for {asset_id} has inconsistent {field}"
                )
    return result


def _canonical_asset_id(kind: str, owner_id: str) -> str:
    return f"{_ASSET_PREFIXES[kind]}_{owner_id}"


def _canonical_token(
    kind: str,
    owner_id: str,
    name: str,
    version: int,
) -> str:
    suffix = f"_V{version:03d}"
    if kind == "style_reference":
        return f"@项目_{owner_id}_{name}_风格参考图{suffix}"
    if kind == "character_sheet":
        return f"@角色_{owner_id}_{name}_综合设定图{suffix}"
    if kind == "expression_sheet":
        return f"@角色_{owner_id}_{name}_表情设定图{suffix}"
    if kind == "action_sheet":
        return f"@角色_{owner_id}_{name}_动作设定图{suffix}"
    if kind == "scene_sheet":
        return f"@场景_{owner_id}_{name}_场景设定图{suffix}"
    if kind == "prop_sheet":
        return f"@道具_{owner_id}_{name}_道具设定图{suffix}"
    if kind == "food_image":
        return f"@食物_{owner_id}_{name}_食物设定图{suffix}"
    if kind == "voice_sample":
        return f"@声音_AUD_{owner_id}_{name}_标准声音{suffix}"
    if kind == "shot_sample":
        return f"@镜头_{owner_id}_样片图{suffix}"
    raise MediaJobError(f"cannot create token for unsupported kind: {kind}")


def _default_prompt(kind: str, name: str) -> str:
    descriptions = {
        "style_reference": f"{name}项目统一视觉风格参考图",
        "character_sheet": (
            f"{name}角色综合设定图，正面、侧面、背面和四分之三视角"
        ),
        "expression_sheet": f"{name}角色表情多宫格设定图",
        "action_sheet": f"{name}角色动作多宫格设定图",
        "scene_sheet": f"{name}场景综合设定图与空间机位参考",
        "prop_sheet": f"{name}重要道具多角度设定图",
        "food_image": f"{name}重要菜肴与摆盘设定图",
        "voice_sample": f"{name}标准 AI 声音样音",
        "shot_sample": f"{name}首集镜头样片图",
    }
    return descriptions[kind]


def _resolve_asset(
    assets: list[dict[str, Any]],
    *,
    kind: str,
    owner_id: str,
    name: str,
    forced_asset_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    matching = [
        asset
        for asset in assets
        if asset.get("asset_type") == kind and asset.get("owner_id") == owner_id
    ]
    usable = [asset for asset in matching if asset.get("status") != "discarded"]
    if usable:
        selected = max(
            usable,
            key=lambda asset: (
                _positive_version(asset.get("version")) or 0,
                str(asset.get("asset_id", "")),
            ),
        )
        return selected, selected.get("status") == "completed"

    asset_id = forced_asset_id or _canonical_asset_id(kind, owner_id)
    versions = [
        _positive_version(asset.get("version"))
        for asset in assets
        if asset.get("asset_id") == asset_id
    ]
    version = max((value for value in versions if value is not None), default=0) + 1
    extension = ".wav" if kind in {"voice_sample", "dialogue_audio"} else ".png"
    file_name = f"{asset_id}_V{version:03d}{extension}"
    return (
        {
            "asset_id": asset_id,
            "version": version,
            "asset_type": kind,
            "owner_id": owner_id,
            "reference_token": _canonical_token(kind, owner_id, name, version),
            "file_name": file_name,
            "relative_path": f"{_OUTPUT_FOLDERS[kind]}/{file_name}",
            "prompt": _default_prompt(kind, name),
            "status": "confirmed",
        },
        False,
    )


def _required_stage_assets(
    assets: list[dict[str, Any]],
    *,
    kind: str,
    owner_id: str,
    name: str,
    forced_asset_id: str | None = None,
) -> list[dict[str, Any]]:
    matching = [
        asset
        for asset in assets
        if asset.get("asset_type") == kind
        and asset.get("owner_id") == owner_id
        and asset.get("status") != "discarded"
    ]
    if matching:
        try:
            return active_assets_by_stage(
                matching, owner_id=owner_id, asset_type=kind
            )
        except StageSelectionError as error:
            raise MediaJobError(str(error)) from error
    generated, _ = _resolve_asset(
        assets,
        kind=kind,
        owner_id=owner_id,
        name=name,
        forced_asset_id=forced_asset_id,
    )
    return [generated]


def _asset_output(asset: dict[str, Any], kind: str) -> dict[str, str]:
    asset_id = str(asset["asset_id"])
    version = _positive_version(asset.get("version")) or 1
    extension = ".wav" if kind in {"voice_sample", "dialogue_audio"} else ".png"
    file_name = asset.get("file_name")
    if not _nonempty(file_name):
        file_name = f"{asset_id}_V{version:03d}{extension}"
    relative_path = asset.get("relative_path")
    if not _nonempty(relative_path):
        relative_path = f"{_OUTPUT_FOLDERS[kind]}/{file_name}"
    file_name = str(file_name)
    relative_path = str(relative_path)
    _validate_output_path(kind, file_name, relative_path)
    output = {"file_name": file_name, "relative_path": relative_path}
    token = asset.get("reference_token")
    if _nonempty(token):
        output["reference_token"] = str(token)
    fingerprint = asset.get("content_fingerprint")
    if kind == "dialogue_audio" and _nonempty(fingerprint):
        output["content_fingerprint"] = str(fingerprint)
    return output


def _validate_output_path(kind: str, file_name: str, relative_path: str) -> None:
    label = f"{kind} output"
    if not _nonempty(file_name) or not _nonempty(relative_path):
        raise MediaJobError(f"{label} requires file_name and relative_path")
    if (
        PurePosixPath(file_name).name != file_name
        or PureWindowsPath(file_name).name != file_name
        or "/" in file_name
        or "\\" in file_name
        or ":" in file_name
        or "\x00" in file_name
    ):
        raise MediaJobError(f"{label} file_name must not contain directories")

    posix_path = PurePosixPath(relative_path)
    windows_path = PureWindowsPath(relative_path)
    if (
        "\\" in relative_path
        or "\x00" in relative_path
        or posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in posix_path.parts
        or "." in posix_path.parts
    ):
        raise MediaJobError(f"{label} relative_path must be confined and relative")
    if posix_path.name != file_name:
        raise MediaJobError(f"{label} relative_path basename must equal file_name")

    required_folder = PurePosixPath(_OUTPUT_FOLDERS[kind])
    prefix = posix_path.parts[: len(required_folder.parts)]
    if tuple(prefix) != required_folder.parts:
        raise MediaJobError(
            f"{label} relative_path must be under {_OUTPUT_FOLDERS[kind]}"
        )

    image_extensions = {".png", ".jpg", ".jpeg", ".webp"}
    audio_extensions = {".wav", ".mp3", ".aac", ".flac", ".opus"}
    expected_extensions = (
        audio_extensions
        if kind in {"voice_sample", "dialogue_audio"}
        else image_extensions
    )
    if Path(file_name).suffix.lower() not in expected_extensions:
        raise MediaJobError(f"{label} has an unexpected media extension")


def _job_for_asset(
    asset: dict[str, Any],
    *,
    kind: str,
    owner_id: str,
    reference_tokens: Iterable[str] = (),
    depends_on: Iterable[str] = (),
    input_data: dict[str, Any] | None = None,
    episode_id: str | None = None,
) -> dict[str, Any]:
    version = _positive_version(asset.get("version")) or 1
    asset_id = str(asset["asset_id"])
    job: dict[str, Any] = {
        "job_id": f"JOB_{asset_id}_V{version:03d}",
        "kind": kind,
        "owner_id": owner_id,
        "asset_id": asset_id,
        "version": version,
        "output": _asset_output(asset, kind),
        "prompt": str(asset.get("prompt") or ""),
        "reference_tokens": list(dict.fromkeys(reference_tokens)),
        "depends_on": sorted(set(depends_on)),
        "input": input_data or {},
        "status": "pending",
    }
    if episode_id is not None:
        job["episode_id"] = episode_id
    return job


def _delivery_instructions(profile: dict[str, Any], name: str) -> list[str]:
    return [
        f"使用单一内置 AI 声纹演绎{name}，不模仿任何真人。",
        f"音色与年龄感遵循角色设定：{profile.get('voice', '自然人声')}。",
        f"整体语气保持：{profile.get('tone', '自然')}。",
        f"语速保持：{profile.get('pace', '中速')}，不抢词。",
        "句意转折处停顿，重点词自然重读，不加额外台词。",
        "保持人名发音清晰、口齿自然，避免机械腔和过度戏剧化。",
    ]


def _active_token(asset: dict[str, Any]) -> str | None:
    token = asset.get("reference_token")
    return str(token) if _nonempty(token) else None


def _asset_identity(asset: dict[str, Any]) -> tuple[str, int]:
    version = _positive_version(asset.get("version")) or 0
    return str(asset.get("asset_id")), version


def _active_asset_for_episode(
    assets: Iterable[dict[str, Any]],
    *,
    kind: str,
    episode_number: int,
    owner_id: str | None = None,
    asset_id: str | None = None,
) -> dict[str, Any] | None:
    try:
        return select_active_for_episode(
            assets,
            episode_number,
            owner_id=owner_id,
            asset_type=kind,
            asset_id=asset_id,
            description=kind,
        )
    except StageSelectionError as error:
        raise MediaJobError(str(error)) from error


def _dialogue_lines(shot: dict[str, Any]) -> list[dict[str, str]]:
    raw = shot.get("dialogue_lines", shot.get("dialogue", []))
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise MediaJobError("shot dialogue_lines must be a list")
    lines: list[dict[str, str]] = []
    for index, line in enumerate(raw):
        if not isinstance(line, dict):
            raise MediaJobError(f"dialogue line {index + 1} must be an object")
        speaker_id = line.get("speaker_id")
        text = line.get("text")
        if not _nonempty(speaker_id) or not _nonempty(text):
            raise MediaJobError(
                f"dialogue line {index + 1} requires speaker_id and text"
            )
        lines.append({"speaker_id": str(speaker_id), "text": str(text)})
    return lines


def _validated_first_episode(data: dict[str, Any]) -> dict[str, Any]:
    episodes = data.get("episodes")
    if not isinstance(episodes, list):
        raise MediaJobError("episodes must be a list")
    if not episodes:
        raise MediaJobError("episodes must contain E001")

    seen_ids: set[str] = set()
    seen_numbers: set[int] = set()
    seen_shot_ids: set[str] = set()
    first_episode: dict[str, Any] | None = None
    for episode_index, episode in enumerate(episodes):
        episode_path = f"episodes[{episode_index}]"
        if not isinstance(episode, dict):
            raise MediaJobError(f"{episode_path} must be an object")
        episode_id = episode.get("episode_id")
        episode_number = episode.get("episode_number")
        if not _nonempty(episode_id):
            raise MediaJobError(f"{episode_path}.episode_id must be a nonempty string")
        if not isinstance(episode_number, int) or isinstance(episode_number, bool):
            raise MediaJobError(f"{episode_path}.episode_number must be an integer")
        episode_id = str(episode_id)
        if episode_id in seen_ids:
            raise MediaJobError(f"duplicate episode_id {episode_id}")
        if episode_number in seen_numbers:
            raise MediaJobError(f"duplicate episode_number {episode_number}")
        seen_ids.add(episode_id)
        seen_numbers.add(episode_number)

        expected_id = f"E{episode_index + 1:03d}"
        if episode_id != expected_id or episode_number != episode_index + 1:
            raise MediaJobError(
                f"{episode_path} must be canonical {expected_id} "
                f"episode_number {episode_index + 1}; E001 must be first"
            )
        if episode_id == "E001":
            first_episode = episode

        shots = episode.get("shots")
        if not isinstance(shots, list):
            raise MediaJobError(f"{episode_path}.shots must be a list")
        for shot_index, shot in enumerate(shots):
            shot_path = f"{episode_path}.shots[{shot_index}]"
            if not isinstance(shot, dict):
                raise MediaJobError(f"{shot_path} must be an object")
            shot_id = shot.get("shot_id")
            expected_shot_id = f"{episode_id}_SH{shot_index + 1:03d}"
            if not _nonempty(shot_id) or shot_id != expected_shot_id:
                raise MediaJobError(
                    f"{shot_path}.shot_id must be {expected_shot_id}"
                )
            if str(shot_id) in seen_shot_ids:
                raise MediaJobError(f"duplicate shot_id {shot_id}")
            seen_shot_ids.add(str(shot_id))
            for field in ("character_ids", "prop_ids", "food_ids"):
                values = shot.get(field)
                if not isinstance(values, list):
                    raise MediaJobError(f"{shot_path}.{field} must be a list")
                if any(not _nonempty(value) for value in values):
                    raise MediaJobError(
                        f"{shot_path}.{field} must contain nonempty strings"
                    )
            if not _nonempty(shot.get("scene_id")):
                raise MediaJobError(f"{shot_path}.scene_id must be a nonempty string")
            for field in ("prompt_zh", "prompt_en", "negative_prompt"):
                if not isinstance(shot.get(field), str):
                    raise MediaJobError(f"{shot_path}.{field} must be a string")

    if first_episode is None or episodes[0] is not first_episode:
        raise MediaJobError("exactly one canonical E001 must be first")
    return first_episode


def _validate_shot_object_references(
    data: dict[str, Any],
    *,
    character_ids: set[str],
    scene_ids: set[str],
    prop_ids: set[str],
    food_ids: set[str],
) -> None:
    for episode_index, episode in enumerate(data["episodes"]):
        for shot_index, shot in enumerate(episode["shots"]):
            path = f"episodes[{episode_index}].shots[{shot_index}]"
            for field, known in (
                ("character_ids", character_ids),
                ("prop_ids", prop_ids),
                ("food_ids", food_ids),
            ):
                for object_id in shot[field]:
                    if str(object_id) not in known:
                        raise MediaJobError(
                            f"{path}.{field} unknown reference: {object_id}"
                        )
            if str(shot["scene_id"]) not in scene_ids:
                raise MediaJobError(
                    f"{path}.scene_id unknown reference: {shot['scene_id']}"
                )


def _validate_shot_reference_tokens(
    shot: dict[str, Any],
    *,
    shot_path: str,
    known_tokens: set[str],
    expected_tokens: set[str],
) -> list[str]:
    tokens_by_field: dict[str, list[str]] = {}
    for field in ("prompt_zh", "prompt_en"):
        prompt = shot[field]
        occurrences = _scan_tokens(prompt, known_tokens)
        unknown = [item.token for item in occurrences if not item.known]
        if unknown:
            raise MediaJobError(
                f"{shot_path}.{field} unknown reference token: {unknown[0]}"
            )
        tokens = [item.token for item in occurrences if item.known]
        if len(tokens) != len(set(tokens)):
            raise MediaJobError(
                f"{shot_path}.{field} contains duplicate reference tokens"
            )
        token_set = set(tokens)
        if token_set != expected_tokens:
            missing = sorted(expected_tokens - token_set)
            unexpected = sorted(token_set - expected_tokens)
            raise MediaJobError(
                f"{shot_path}.{field} token set mismatch; "
                f"missing={missing}, unexpected={unexpected}"
            )
        tokens_by_field[field] = tokens
    if set(tokens_by_field["prompt_zh"]) != set(tokens_by_field["prompt_en"]):
        raise MediaJobError(
            f"{shot_path} prompt_zh and prompt_en token sets differ"
        )
    return tokens_by_field["prompt_zh"]


def _validate_shared_inline_references(
    data: dict[str, Any],
    *,
    required_assets: Iterable[dict[str, Any]],
    important_prop_ids: set[str],
    important_food_ids: set[str],
) -> None:
    validation_data = copy.deepcopy(data)
    raw_assets = validation_data["assets"]
    asset_indexes = {
        (str(asset["asset_id"]), int(asset["version"])): index
        for index, asset in enumerate(raw_assets)
    }
    for required in required_assets:
        if required.get("status") == "discarded":
            continue
        registered = copy.deepcopy(required)
        registered["status"] = "completed"
        identity = (str(registered["asset_id"]), int(registered["version"]))
        if identity in asset_indexes:
            raw_assets[asset_indexes[identity]] = registered
        else:
            asset_indexes[identity] = len(raw_assets)
            raw_assets.append(registered)

    validation_data["episodes"] = [validation_data["episodes"][0]]
    for episode in validation_data["episodes"]:
        for shot in episode["shots"]:
            shot["prop_ids"] = [
                prop_id
                for prop_id in shot["prop_ids"]
                if str(prop_id) in important_prop_ids
            ]
            shot["food_ids"] = [
                food_id
                for food_id in shot["food_ids"]
                if str(food_id) in important_food_ids
            ]

    errors = validate_references(validation_data)
    if errors:
        raise MediaJobError(errors[0])


def _resolve_dialogue_asset(
    assets: list[dict[str, Any]],
    *,
    asset_id: str,
    shot_id: str,
    speaker_id: str,
    text: str,
) -> tuple[dict[str, Any], bool]:
    fingerprint = _dialogue_content_fingerprint(speaker_id, text)
    history = [
        asset
        for asset in assets
        if asset.get("asset_id") == asset_id
        and asset.get("asset_type") == "dialogue_audio"
        and _positive_version(asset.get("version")) is not None
    ]
    if history:
        validated_fingerprints = {
            id(asset): _validated_dialogue_fingerprint(asset, asset_id)
            for asset in history
        }
        latest = max(history, key=lambda asset: int(asset["version"]))
        recorded_fingerprint = validated_fingerprints[id(latest)]
        if recorded_fingerprint is not None:
            identity_matches = recorded_fingerprint == fingerprint
        else:
            identity_matches = (
                latest.get("speaker_id") == speaker_id
                and latest.get("prompt") == text
            )
        if identity_matches and latest.get("status") == "completed":
            return latest, True
        if identity_matches and latest.get("status") != "discarded":
            resumed = dict(latest)
            resumed.update(
                {
                    "speaker_id": speaker_id,
                    "text": text,
                    "prompt": text,
                    "content_fingerprint": fingerprint,
                }
            )
            return resumed, False
        version = max(int(asset["version"]) for asset in history) + 1
    else:
        version = 1

    file_name = f"{asset_id}_V{version:03d}.wav"
    return (
        {
            "asset_id": asset_id,
            "version": version,
            "asset_type": "dialogue_audio",
            "owner_id": shot_id,
            "speaker_id": speaker_id,
            "text": text,
            "content_fingerprint": fingerprint,
            "reference_token": f"@声音_{asset_id}_对白_V{version:03d}",
            "file_name": file_name,
            "relative_path": f"assets/audio/dialogue/{file_name}",
            "prompt": text,
        },
        False,
    )


def _validated_dialogue_fingerprint(
    asset: dict[str, Any], asset_id: str
) -> str | None:
    if "content_fingerprint" not in asset:
        return None
    recorded = asset.get("content_fingerprint")
    if not isinstance(recorded, str) or not _CONTENT_FINGERPRINT.fullmatch(recorded):
        raise MediaJobError(
            f"{asset_id} content_fingerprint must be 64 lowercase hex characters"
        )
    recorded_speaker = asset.get("speaker_id")
    recorded_text = asset.get("prompt")
    has_speaker = _nonempty(recorded_speaker)
    has_text = _nonempty(recorded_text)
    if has_speaker or has_text:
        if not (has_speaker and has_text):
            raise MediaJobError(f"{asset_id} content_fingerprint metadata conflict")
        metadata_fingerprint = _dialogue_content_fingerprint(
            str(recorded_speaker), str(recorded_text)
        )
        if metadata_fingerprint != recorded:
            raise MediaJobError(f"{asset_id} content_fingerprint metadata conflict")
    return recorded


def _dialogue_content_fingerprint(speaker_id: str, text: str) -> str:
    canonical = json.dumps(
        {"speaker_id": speaker_id, "text": text},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _cycle_job_ids(graph: dict[str, set[str]]) -> list[str]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: set[str] = set()

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for dependency in sorted(graph[node]):
            if dependency not in indices:
                visit(dependency)
                lowlinks[node] = min(lowlinks[node], lowlinks[dependency])
            elif dependency in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[dependency])
        if lowlinks[node] != indices[node]:
            return
        component: list[str] = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1 or node in graph[node]:
            cycles.update(component)

    for job_id in sorted(graph):
        if job_id not in indices:
            visit(job_id)
    return sorted(cycles)


def sort_media_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return deterministic priority-aware topological order."""
    by_id: dict[str, dict[str, Any]] = {}
    for job in jobs:
        job_id = job.get("job_id")
        if not _nonempty(job_id):
            raise MediaJobError("every media job requires a nonempty job_id")
        job_id = str(job_id)
        if job_id in by_id:
            raise MediaJobError(f"duplicate media job id: {job_id}")
        by_id[job_id] = job

    graph: dict[str, set[str]] = {}
    dependents: dict[str, set[str]] = {job_id: set() for job_id in by_id}
    for job_id, job in by_id.items():
        raw_dependencies = job.get("depends_on", [])
        if not isinstance(raw_dependencies, list):
            raise MediaJobError(f"{job_id}.depends_on must be a list")
        dependencies = set()
        for dependency in raw_dependencies:
            if dependency not in by_id:
                raise MediaJobError(f"{job_id} has unknown dependency: {dependency}")
            dependencies.add(str(dependency))
            dependents[str(dependency)].add(job_id)
        graph[job_id] = dependencies

    queue: list[tuple[int, str]] = []
    for job_id, dependencies in graph.items():
        if not dependencies:
            heapq.heappush(
                queue,
                (_KIND_PRIORITY.get(str(by_id[job_id].get("kind")), 999), job_id),
            )

    ordered: list[dict[str, Any]] = []
    while queue:
        _, job_id = heapq.heappop(queue)
        ordered.append(by_id[job_id])
        for dependent in sorted(dependents[job_id]):
            graph[dependent].discard(job_id)
            if not graph[dependent]:
                heapq.heappush(
                    queue,
                    (
                        _KIND_PRIORITY.get(
                            str(by_id[dependent].get("kind")), 999
                        ),
                        dependent,
                    ),
                )

    if len(ordered) != len(jobs):
        cycle_ids = _cycle_job_ids(graph)
        raise MediaJobError(
            "media job dependency cycle: " + ", ".join(cycle_ids)
        )
    return ordered


def build_media_jobs(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Build pending media work without generating or modifying any assets."""
    if not isinstance(data, dict):
        raise MediaJobError("project root must be an object")
    project = data.get("project")
    if not isinstance(project, dict):
        raise MediaJobError("project must be an object")
    project_id = project.get("project_id")
    title = project.get("title")
    if not _nonempty(project_id) or not _nonempty(title):
        raise MediaJobError("project requires project_id and title")

    settings = data.get("generation_settings")
    if not isinstance(settings, dict):
        raise MediaJobError("generation_settings must be an object")
    sample_count = settings.get("sample_episode_count")
    if (
        not isinstance(sample_count, int)
        or isinstance(sample_count, bool)
        or sample_count != 1
    ):
        raise MediaJobError(
            "generation_settings.sample_episode_count must be integer 1"
        )

    assets = _assets(data)
    first_episode = _validated_first_episode(data)
    sample_character_ids = {
        str(character_id)
        for shot in first_episode["shots"]
        for character_id in shot["character_ids"]
    }
    sample_scene_ids = {
        str(shot["scene_id"])
        for shot in first_episode["shots"]
    }
    jobs: list[dict[str, Any]] = []
    requirement_assets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    requirement_jobs: dict[tuple[str, int], dict[str, Any]] = {}

    def require(
        kind: str,
        owner_id: str,
        name: str,
        *,
        refs: Iterable[str] = (),
        dependencies: Iterable[dict[str, Any] | None] = (),
        input_data: dict[str, Any] | None = None,
        episode_id: str | None = None,
        forced_asset_id: str | None = None,
        asset_context: Callable[
            [dict[str, Any]],
            tuple[
                Iterable[str],
                Iterable[dict[str, Any] | None],
                dict[str, Any] | None,
            ],
        ]
        | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        key = (kind, owner_id)
        if key in requirement_assets:
            cached_assets = requirement_assets[key]
            cached_jobs: list[dict[str, Any]] = []
            for asset in cached_assets:
                version = _positive_version(asset.get("version"))
                identity = (str(asset.get("asset_id")), version or 0)
                if identity in requirement_jobs:
                    cached_jobs.append(requirement_jobs[identity])
            return cached_assets, cached_jobs
        required = _required_stage_assets(
            assets,
            kind=kind,
            owner_id=owner_id,
            name=name,
            forced_asset_id=forced_asset_id,
        )
        requirement_assets[key] = required
        scheduled: list[dict[str, Any]] = []
        for asset in required:
            if asset.get("status") == "completed":
                continue
            if asset_context is None:
                asset_refs = list(refs)
                asset_dependencies = list(dependencies)
                asset_input = input_data
            else:
                context_refs, context_dependencies, context_input = asset_context(asset)
                asset_refs = list(context_refs)
                asset_dependencies = list(context_dependencies)
                asset_input = context_input
            asset_id = asset.get("asset_id")
            version = _positive_version(asset.get("version"))
            if not _nonempty(asset_id) or version is None:
                raise MediaJobError(
                    f"{kind} {owner_id} requires valid asset_id and version"
                )
            identity = (str(asset_id), version)
            if identity in requirement_jobs:
                raise MediaJobError(
                    f"duplicate scheduled asset identity: {asset_id} V{version:03d}"
                )
            job = _job_for_asset(
                asset,
                kind=kind,
                owner_id=owner_id,
                reference_tokens=asset_refs,
                depends_on=[
                    dependency["job_id"]
                    for dependency in asset_dependencies
                    if dependency is not None
                ],
                input_data=asset_input,
                episode_id=episode_id,
            )
            jobs.append(job)
            scheduled.append(job)
            requirement_jobs[identity] = job
        return required, scheduled

    style_assets, _ = require(
        "style_reference",
        str(project_id),
        str(title),
        input_data={
            "provider": settings.get(
                "image_provider", "built_in_image_gen"
            ),
            "aspect_ratio": settings.get(
                "aspect_ratio", "9:16"
            ),
        },
    )

    def pending_job_for(asset: dict[str, Any]) -> dict[str, Any] | None:
        version = _positive_version(asset.get("version"))
        return requirement_jobs.get((str(asset.get("asset_id")), version or 0))

    def staged_context(
        parent_assets: list[dict[str, Any]],
        parent_kind: str,
        base_input: dict[str, Any],
        input_reference_field: str,
    ) -> Callable[
        [dict[str, Any]],
        tuple[list[str], list[dict[str, Any] | None], dict[str, Any]],
    ]:
        def resolve(
            child: dict[str, Any],
        ) -> tuple[list[str], list[dict[str, Any] | None], dict[str, Any]]:
            try:
                parent = select_parent_for_child(
                    child, parent_assets, parent_kind=parent_kind
                )
            except StageSelectionError as error:
                raise MediaJobError(str(error)) from error
            token = _active_token(parent)
            if token is None:
                raise MediaJobError(
                    f"{parent_kind} parent requires a reference_token"
                )
            job_input = dict(base_input)
            job_input[input_reference_field] = token
            return [token], [pending_job_for(parent)], job_input

        return resolve

    all_characters = _objects(data, "characters", "character_id")
    all_scenes = _objects(data, "scenes", "scene_id")
    all_props = _objects(data, "props", "prop_id")
    all_foods = _objects(data, "foods", "food_id")
    _validate_shot_object_references(
        data,
        character_ids={str(item["character_id"]) for item in all_characters},
        scene_ids={str(item["scene_id"]) for item in all_scenes},
        prop_ids={str(item["prop_id"]) for item in all_props},
        food_ids={str(item["food_id"]) for item in all_foods},
    )
    media_characters = [
        character
        for character in all_characters
        if character.get("importance") in {"lead", "major"}
    ]
    visual_characters = [
        character
        for character in all_characters
        if character.get("importance") in {"lead", "major"}
        or str(character.get("character_id")) in sample_character_ids
    ]
    character_assets: dict[str, list[dict[str, Any]]] = {}
    for character in visual_characters:
        character_id = str(character["character_id"])
        name = str(character.get("name", character_id))
        character_input = {
            "provider": settings.get("image_provider", "built_in_image_gen"),
            "appearance": character.get("appearance", ""),
            "layout": "one comprehensive character sheet",
        }
        owner_assets, _ = require(
            "character_sheet",
            character_id,
            name,
            asset_context=staged_context(
                style_assets,
                "style_reference",
                character_input,
                "style_reference",
            ),
        )
        character_assets[character_id] = owner_assets

    for kind in ("expression_sheet", "action_sheet"):
        for character in media_characters:
            character_id = str(character["character_id"])
            sheet_input = {
                "provider": settings.get(
                    "image_provider", "built_in_image_gen"
                ),
                "layout": "single multi-panel sheet",
            }
            require(
                kind,
                character_id,
                str(character.get("name", character_id)),
                asset_context=staged_context(
                    character_assets[character_id],
                    "character_sheet",
                    sheet_input,
                    "character_reference",
                ),
            )

    for scene in all_scenes:
        if (
            scene.get("importance") != "important"
            and str(scene.get("scene_id")) not in sample_scene_ids
        ):
            continue
        scene_id = str(scene["scene_id"])
        scene_input = {"provider": settings.get("image_provider")}
        require(
            "scene_sheet",
            scene_id,
            str(scene.get("name", scene_id)),
            asset_context=staged_context(
                style_assets,
                "style_reference",
                scene_input,
                "style_reference",
            ),
        )

    important_prop_ids = {
        str(prop.get("prop_id"))
        for prop in all_props
        if prop.get("importance") == "important"
    }
    for prop in all_props:
        if prop.get("importance") != "important":
            continue
        prop_id = str(prop["prop_id"])
        prop_input = {"provider": settings.get("image_provider")}
        require(
            "prop_sheet",
            prop_id,
            str(prop.get("name", prop_id)),
            asset_context=staged_context(
                style_assets,
                "style_reference",
                prop_input,
                "style_reference",
            ),
        )

    important_food_ids = {
        str(food.get("food_id"))
        for food in all_foods
        if food.get("importance") == "important"
    }
    for food in all_foods:
        if food.get("importance") != "important":
            continue
        food_id = str(food["food_id"])
        food_input = {"provider": settings.get("image_provider")}
        require(
            "food_image",
            food_id,
            str(food.get("name", food_id)),
            asset_context=staged_context(
                style_assets,
                "style_reference",
                food_input,
                "style_reference",
            ),
        )

    voice_assets: dict[str, list[dict[str, Any]]] = {}
    characters_by_id = {
        str(character["character_id"]): character for character in all_characters
    }
    for character in media_characters:
        character_id = str(character["character_id"])
        name = str(character.get("name", character_id))
        profile = character.get("voice_profile")
        if not isinstance(profile, dict):
            raise MediaJobError(f"{character_id} requires voice_profile")
        owner_assets, _ = require(
            "voice_sample",
            character_id,
            name,
            input_data={
                "provider": settings.get(
                    "voice_provider", "openai_speech"
                ),
                "voice_profile": dict(profile),
                "sample_text": profile.get("sample_text", ""),
                "built_in_voice": "coral",
                "delivery_instructions": _delivery_instructions(profile, name),
                "ai_generated": True,
            },
        )
        voice_assets[character_id] = owner_assets

    _validate_shared_inline_references(
        data,
        required_assets=(
            asset
            for group in requirement_assets.values()
            for asset in group
        ),
        important_prop_ids=important_prop_ids,
        important_food_ids=important_food_ids,
    )

    first_episodes = [first_episode]
    all_known_tokens = {
        str(asset["reference_token"])
        for asset in [
            *assets,
            *(asset for group in requirement_assets.values() for asset in group),
        ]
        if _nonempty(asset.get("reference_token"))
    }

    for episode in first_episodes:
        episode_id = str(episode.get("episode_id", "E001"))
        episode_number = episode.get("episode_number", 1)
        if not isinstance(episode_number, int) or isinstance(episode_number, bool):
            raise MediaJobError(f"{episode_id}.episode_number must be an integer")
        shots = episode.get("shots", [])
        if not isinstance(shots, list):
            raise MediaJobError(f"{episode_id}.shots must be a list")
        for shot_index, shot in enumerate(shots):
            shot_path = f"episodes[0].shots[{shot_index}]"
            shot_id = str(shot.get("shot_id", ""))
            allowed_tokens: set[str] = set()
            shot_dependencies: list[dict[str, Any] | None] = []
            available_assets = [
                *assets,
                *(asset for group in requirement_assets.values() for asset in group),
            ]

            def add_shot_reference(
                asset: dict[str, Any] | None, reference_label: str
            ) -> None:
                if asset is None:
                    raise MediaJobError(
                        f"{shot_path} has no active {reference_label} asset"
                    )
                token = _active_token(asset)
                if token:
                    allowed_tokens.add(token)
                version = _positive_version(asset.get("version"))
                identity = (str(asset.get("asset_id")), version or 0)
                dependency = requirement_jobs.get(identity)
                if dependency is not None:
                    shot_dependencies.append(dependency)

            episode_characters: dict[str, dict[str, Any]] = {}
            for character_id in shot.get("character_ids", []):
                character_id = str(character_id)
                character_asset = _active_asset_for_episode(
                    available_assets,
                    kind="character_sheet",
                    owner_id=character_id,
                    episode_number=episode_number,
                )
                add_shot_reference(
                    character_asset, f"character_sheet for {character_id}"
                )
                if character_asset is not None:
                    episode_characters[character_id] = character_asset
            scene_id = str(shot.get("scene_id", ""))
            add_shot_reference(
                _active_asset_for_episode(
                    available_assets,
                    kind="scene_sheet",
                    owner_id=scene_id,
                    episode_number=episode_number,
                ),
                f"scene_sheet for {scene_id}",
            )
            for prop_id in shot.get("prop_ids", []):
                if str(prop_id) not in important_prop_ids:
                    continue
                add_shot_reference(
                    _active_asset_for_episode(
                        available_assets,
                        kind="prop_sheet",
                        owner_id=str(prop_id),
                        episode_number=episode_number,
                    ),
                    f"prop_sheet for {prop_id}",
                )
            for food_id in shot.get("food_ids", []):
                if str(food_id) not in important_food_ids:
                    continue
                add_shot_reference(
                    _active_asset_for_episode(
                        available_assets,
                        kind="food_image",
                        owner_id=str(food_id),
                        episode_number=episode_number,
                    ),
                    f"food_image for {food_id}",
                )
            for field, kind in (
                ("expression_asset_id", "expression_sheet"),
                ("action_asset_id", "action_sheet"),
            ):
                selected_id = shot.get(field)
                if not _nonempty(selected_id):
                    continue
                extra_asset = _active_asset_for_episode(
                    available_assets,
                    kind=kind,
                    asset_id=str(selected_id),
                    episode_number=episode_number,
                )
                if extra_asset is None:
                    add_shot_reference(None, f"{kind} {selected_id}")
                    continue
                owner_id = str(extra_asset.get("owner_id"))
                character_candidates = [
                    candidate
                    for candidate in available_assets
                    if candidate.get("asset_type") == "character_sheet"
                    and candidate.get("owner_id") == owner_id
                ]
                try:
                    parent = select_parent_for_child(
                        extra_asset,
                        character_candidates,
                        parent_kind="character_sheet",
                    )
                except StageSelectionError as error:
                    raise MediaJobError(str(error)) from error
                episode_character = episode_characters.get(owner_id)
                if (
                    episode_character is None
                    or _asset_identity(parent) != _asset_identity(episode_character)
                ):
                    raise MediaJobError(
                        f"{kind} parent does not match active character for "
                        f"{owner_id}"
                    )
                add_shot_reference(
                    extra_asset,
                    f"{kind} {selected_id}",
                )

            reference_tokens = _validate_shot_reference_tokens(
                shot,
                shot_path=shot_path,
                known_tokens=all_known_tokens,
                expected_tokens=allowed_tokens,
            )
            require(
                "shot_sample",
                shot_id,
                shot_id,
                refs=reference_tokens,
                dependencies=shot_dependencies,
                input_data={
                    "provider": settings.get(
                        "image_provider", "built_in_image_gen"
                    ),
                    "prompt_zh": shot.get("prompt_zh", ""),
                    "prompt_en": shot.get("prompt_en", ""),
                    "negative_prompt": shot.get("negative_prompt", ""),
                    "sample_count": 1,
                },
                episode_id=episode_id,
            )

            for line_number, line in enumerate(_dialogue_lines(shot), start=1):
                speaker_id = line["speaker_id"]
                if speaker_id not in characters_by_id:
                    raise MediaJobError(
                        f"{shot_id} dialogue speaker is unknown: {speaker_id}"
                    )
                speaker = characters_by_id[speaker_id]
                profile = speaker.get("voice_profile")
                if not isinstance(profile, dict):
                    raise MediaJobError(
                        f"{speaker_id} dialogue speaker requires voice_profile"
                    )
                dialogue_asset_id = f"DIALOGUE_{shot_id}_L{line_number:03d}"
                selected, completed = _resolve_dialogue_asset(
                    assets,
                    asset_id=dialogue_asset_id,
                    shot_id=shot_id,
                    speaker_id=speaker_id,
                    text=line["text"],
                )
                if completed:
                    continue
                speaker_voice_assets = voice_assets.get(speaker_id, [])
                voice_asset = _active_asset_for_episode(
                    speaker_voice_assets,
                    kind="voice_sample",
                    owner_id=speaker_id,
                    episode_number=episode_number,
                )
                if speaker_id in voice_assets and voice_asset is None:
                    raise MediaJobError(
                        f"{speaker_id} has no voice_sample active for E001"
                    )
                voice_token = (
                    _active_token(voice_asset) if voice_asset is not None else None
                )
                active_voice_job = (
                    pending_job_for(voice_asset) if voice_asset is not None else None
                )
                jobs.append(
                    _job_for_asset(
                        selected,
                        kind="dialogue_audio",
                        owner_id=shot_id,
                        reference_tokens=[voice_token] if voice_token else [],
                        depends_on=(
                            [active_voice_job["job_id"]]
                            if active_voice_job is not None
                            else []
                        ),
                        input_data={
                            "provider": settings.get(
                                "voice_provider", "openai_speech"
                            ),
                            "speaker_id": speaker_id,
                            "text": line["text"],
                            "content_fingerprint": _dialogue_content_fingerprint(
                                speaker_id, line["text"]
                            ),
                            "voice_profile": dict(profile),
                            "voice_asset_id": (
                                voice_asset.get("asset_id")
                                if voice_asset is not None
                                else None
                            ),
                            "voice_reference_token": voice_token,
                            "built_in_voice": "coral",
                            "delivery_instructions": _delivery_instructions(
                                profile,
                                str(speaker.get("name", speaker_id)),
                            ),
                            "ai_generated": True,
                        },
                        episode_id=episode_id,
                    )
                )

    return sort_media_jobs(jobs)


def write_jobs(project_path: Path, output_path: Path | None = None) -> Path:
    project_path = Path(project_path)
    destination = Path(output_path) if output_path is not None else project_path.with_name(
        "media-jobs.json"
    )
    same_resolved_path = project_path.resolve(strict=False) == destination.resolve(
        strict=False
    )
    same_existing_file = False
    if not same_resolved_path and project_path.exists() and destination.exists():
        try:
            same_existing_file = os.path.samefile(project_path, destination)
        except OSError:
            same_existing_file = False
    if same_resolved_path or same_existing_file:
        raise MediaJobError("project input and media job output are the same file")
    data = load_json(project_path)
    jobs = build_media_jobs(data)
    project = data.get("project", {})
    manifest = {
        "schema_version": "1.0.0",
        "project_id": project.get("project_id"),
        "jobs": jobs,
    }
    atomic_write_json(destination, manifest)
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a deterministic media-jobs.json manifest."
    )
    parser.add_argument("project", type=Path, help="Path to canonical project.json")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output path (defaults to media-jobs.json beside project.json)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        output = write_jobs(args.project, args.output)
    except (MediaJobError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
