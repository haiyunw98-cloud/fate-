from __future__ import annotations

import argparse
import heapq
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from project_io import atomic_write_json, load_json
from validate_references import _scan_tokens


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
    return sorted(
        (item for item in raw if isinstance(item, dict)),
        key=lambda item: str(item.get(id_field, "")),
    )


def _assets(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("assets", [])
    if not isinstance(raw, list):
        raise MediaJobError("assets must be a list")
    return [asset for asset in raw if isinstance(asset, dict)]


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
        "character_sheet": f"{name}角色综合设定图，正面、侧面、背面和四分之三视角",
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
    output = {"file_name": str(file_name), "relative_path": str(relative_path)}
    token = asset.get("reference_token")
    if _nonempty(token):
        output["reference_token"] = str(token)
    return output


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


def _covers_episode(asset: dict[str, Any], episode_number: int) -> bool:
    episode_range = asset.get("episode_range")
    if episode_range is None:
        return True
    return (
        isinstance(episode_range, list)
        and len(episode_range) == 2
        and all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in episode_range
        )
        and episode_range[0] <= episode_number <= episode_range[1]
    )


def _active_asset_for_episode(
    assets: Iterable[dict[str, Any]],
    *,
    kind: str,
    episode_number: int,
    owner_id: str | None = None,
    asset_id: str | None = None,
) -> dict[str, Any] | None:
    candidates = [
        asset
        for asset in assets
        if asset.get("asset_type") == kind
        and asset.get("status") != "discarded"
        and (owner_id is None or asset.get("owner_id") == owner_id)
        and (asset_id is None or asset.get("asset_id") == asset_id)
        and _positive_version(asset.get("version")) is not None
        and _covers_episode(asset, episode_number)
    ]
    return max(candidates, key=lambda asset: int(asset["version"]), default=None)


def _tokens_in_shot(
    shot: dict[str, Any],
    known_tokens: set[str],
    allowed_tokens: set[str],
) -> list[str]:
    result: list[str] = []
    for field in ("prompt_zh", "prompt_en"):
        prompt = shot.get(field)
        if not isinstance(prompt, str):
            continue
        for occurrence in _scan_tokens(prompt, known_tokens):
            if occurrence.known and occurrence.token in allowed_tokens:
                if occurrence.token not in result:
                    result.append(occurrence.token)
    return result


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

    assets = _assets(data)
    jobs: list[dict[str, Any]] = []
    requirement_assets: dict[tuple[str, str], dict[str, Any]] = {}
    requirement_jobs: dict[tuple[str, str], dict[str, Any]] = {}

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
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        key = (kind, owner_id)
        if key in requirement_assets:
            return requirement_assets[key], requirement_jobs.get(key)
        asset, completed = _resolve_asset(
            assets,
            kind=kind,
            owner_id=owner_id,
            name=name,
            forced_asset_id=forced_asset_id,
        )
        requirement_assets[key] = asset
        if completed:
            return asset, None
        dependency_ids = [
            dependency["job_id"] for dependency in dependencies if dependency is not None
        ]
        job = _job_for_asset(
            asset,
            kind=kind,
            owner_id=owner_id,
            reference_tokens=refs,
            depends_on=dependency_ids,
            input_data=input_data,
            episode_id=episode_id,
        )
        jobs.append(job)
        requirement_jobs[key] = job
        return asset, job

    style_asset, style_job = require(
        "style_reference",
        str(project_id),
        str(title),
        input_data={
            "provider": data.get("generation_settings", {}).get(
                "image_provider", "built_in_image_gen"
            ),
            "aspect_ratio": data.get("generation_settings", {}).get(
                "aspect_ratio", "9:16"
            ),
        },
    )
    style_token = _active_token(style_asset)

    characters = [
        character
        for character in _objects(data, "characters", "character_id")
        if character.get("importance") in {"lead", "major"}
    ]
    character_assets: dict[str, dict[str, Any]] = {}
    character_jobs: dict[str, dict[str, Any] | None] = {}
    for character in characters:
        character_id = str(character["character_id"])
        name = str(character.get("name", character_id))
        asset, job = require(
            "character_sheet",
            character_id,
            name,
            refs=[style_token] if style_token else [],
            dependencies=[style_job],
            input_data={
                "provider": data.get("generation_settings", {}).get(
                    "image_provider", "built_in_image_gen"
                ),
                "appearance": character.get("appearance", ""),
                "layout": "one comprehensive character sheet",
            },
        )
        character_assets[character_id] = asset
        character_jobs[character_id] = job

    for kind in ("expression_sheet", "action_sheet"):
        for character in characters:
            character_id = str(character["character_id"])
            base_asset = character_assets[character_id]
            base_token = _active_token(base_asset)
            require(
                kind,
                character_id,
                str(character.get("name", character_id)),
                refs=[base_token] if base_token else [],
                dependencies=[character_jobs[character_id]],
                input_data={
                    "provider": data.get("generation_settings", {}).get(
                        "image_provider", "built_in_image_gen"
                    ),
                    "character_reference": base_token,
                    "layout": "single multi-panel sheet",
                },
            )

    for scene in _objects(data, "scenes", "scene_id"):
        if scene.get("importance") != "important":
            continue
        scene_id = str(scene["scene_id"])
        require(
            "scene_sheet",
            scene_id,
            str(scene.get("name", scene_id)),
            refs=[style_token] if style_token else [],
            dependencies=[style_job],
            input_data={"provider": data.get("generation_settings", {}).get("image_provider")},
        )

    for prop in _objects(data, "props", "prop_id"):
        if prop.get("importance") != "important":
            continue
        prop_id = str(prop["prop_id"])
        require(
            "prop_sheet",
            prop_id,
            str(prop.get("name", prop_id)),
            refs=[style_token] if style_token else [],
            dependencies=[style_job],
            input_data={"provider": data.get("generation_settings", {}).get("image_provider")},
        )

    for food in _objects(data, "foods", "food_id"):
        if food.get("importance") != "important":
            continue
        food_id = str(food["food_id"])
        require(
            "food_image",
            food_id,
            str(food.get("name", food_id)),
            refs=[style_token] if style_token else [],
            dependencies=[style_job],
            input_data={"provider": data.get("generation_settings", {}).get("image_provider")},
        )

    voice_assets: dict[str, dict[str, Any]] = {}
    voice_jobs: dict[str, dict[str, Any] | None] = {}
    characters_by_id = {str(character["character_id"]): character for character in characters}
    for character in characters:
        character_id = str(character["character_id"])
        name = str(character.get("name", character_id))
        profile = character.get("voice_profile")
        if not isinstance(profile, dict):
            raise MediaJobError(f"{character_id} requires voice_profile")
        asset, job = require(
            "voice_sample",
            character_id,
            name,
            input_data={
                "provider": data.get("generation_settings", {}).get(
                    "voice_provider", "openai_speech"
                ),
                "voice_profile": dict(profile),
                "sample_text": profile.get("sample_text", ""),
                "built_in_voice": "coral",
                "delivery_instructions": _delivery_instructions(profile, name),
                "ai_generated": True,
            },
        )
        voice_assets[character_id] = asset
        voice_jobs[character_id] = job

    raw_episodes = data.get("episodes", [])
    if not isinstance(raw_episodes, list):
        raise MediaJobError("episodes must be a list")
    sample_count = data.get("generation_settings", {}).get("sample_episode_count", 1)
    first_episodes = raw_episodes[:1] if sample_count else []
    all_known_tokens = {
        str(asset["reference_token"])
        for asset in [*assets, *requirement_assets.values()]
        if _nonempty(asset.get("reference_token"))
    }

    for episode in first_episodes:
        if not isinstance(episode, dict):
            continue
        episode_id = str(episode.get("episode_id", "E001"))
        episode_number = episode.get("episode_number", 1)
        if not isinstance(episode_number, int) or isinstance(episode_number, bool):
            raise MediaJobError(f"{episode_id}.episode_number must be an integer")
        shots = episode.get("shots", [])
        if not isinstance(shots, list):
            raise MediaJobError(f"{episode_id}.shots must be a list")
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            shot_id = str(shot.get("shot_id", ""))
            allowed_tokens: set[str] = set()
            shot_dependencies: list[dict[str, Any] | None] = []
            available_assets = [*assets, *requirement_assets.values()]

            def add_shot_reference(asset: dict[str, Any] | None) -> None:
                if asset is None:
                    return
                token = _active_token(asset)
                if token:
                    allowed_tokens.add(token)
                key = (str(asset.get("asset_type")), str(asset.get("owner_id")))
                dependency = requirement_jobs.get(key)
                if (
                    dependency is not None
                    and dependency.get("asset_id") == asset.get("asset_id")
                    and dependency.get("version") == asset.get("version")
                ):
                    shot_dependencies.append(dependency)

            for character_id in shot.get("character_ids", []):
                add_shot_reference(
                    _active_asset_for_episode(
                        available_assets,
                        kind="character_sheet",
                        owner_id=str(character_id),
                        episode_number=episode_number,
                    )
                )
            scene_id = str(shot.get("scene_id", ""))
            add_shot_reference(
                _active_asset_for_episode(
                    available_assets,
                    kind="scene_sheet",
                    owner_id=scene_id,
                    episode_number=episode_number,
                )
            )
            for prop_id in shot.get("prop_ids", []):
                add_shot_reference(
                    _active_asset_for_episode(
                        available_assets,
                        kind="prop_sheet",
                        owner_id=str(prop_id),
                        episode_number=episode_number,
                    )
                )
            for food_id in shot.get("food_ids", []):
                add_shot_reference(
                    _active_asset_for_episode(
                        available_assets,
                        kind="food_image",
                        owner_id=str(food_id),
                        episode_number=episode_number,
                    )
                )
            for field, kind in (
                ("expression_asset_id", "expression_sheet"),
                ("action_asset_id", "action_sheet"),
            ):
                selected_id = shot.get(field)
                if not _nonempty(selected_id):
                    continue
                add_shot_reference(
                    _active_asset_for_episode(
                        available_assets,
                        kind=kind,
                        asset_id=str(selected_id),
                        episode_number=episode_number,
                    )
                )

            reference_tokens = _tokens_in_shot(
                shot, all_known_tokens, allowed_tokens
            )
            require(
                "shot_sample",
                shot_id,
                shot_id,
                refs=reference_tokens,
                dependencies=shot_dependencies,
                input_data={
                    "provider": data.get("generation_settings", {}).get(
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
                        f"{shot_id} dialogue speaker has no lead/major voice: {speaker_id}"
                    )
                dialogue_asset_id = (
                    f"DIALOGUE_{shot_id}_L{line_number:03d}"
                )
                matching = [
                    asset
                    for asset in assets
                    if asset.get("asset_id") == dialogue_asset_id
                    and asset.get("asset_type") == "dialogue_audio"
                ]
                usable = [
                    asset for asset in matching if asset.get("status") != "discarded"
                ]
                selected = max(
                    usable,
                    key=lambda asset: _positive_version(asset.get("version")) or 0,
                    default=None,
                )
                if selected is not None and selected.get("status") == "completed":
                    continue
                version = (
                    _positive_version(selected.get("version"))
                    if selected is not None
                    else 1
                ) or 1
                if selected is None:
                    file_name = f"{dialogue_asset_id}_V{version:03d}.wav"
                    selected = {
                        "asset_id": dialogue_asset_id,
                        "version": version,
                        "asset_type": "dialogue_audio",
                        "owner_id": shot_id,
                        "reference_token": (
                            f"@声音_{dialogue_asset_id}_对白_V{version:03d}"
                        ),
                        "file_name": file_name,
                        "relative_path": f"assets/audio/dialogue/{file_name}",
                        "prompt": line["text"],
                    }
                voice_asset = voice_assets[speaker_id]
                voice_token = _active_token(voice_asset)
                voice_job = voice_jobs[speaker_id]
                jobs.append(
                    _job_for_asset(
                        selected,
                        kind="dialogue_audio",
                        owner_id=shot_id,
                        reference_tokens=[voice_token] if voice_token else [],
                        depends_on=[voice_job["job_id"]] if voice_job else [],
                        input_data={
                            "provider": data.get("generation_settings", {}).get(
                                "voice_provider", "openai_speech"
                            ),
                            "speaker_id": speaker_id,
                            "text": line["text"],
                            "voice_asset_id": voice_asset.get("asset_id"),
                            "voice_reference_token": voice_token,
                            "built_in_voice": "coral",
                            "delivery_instructions": _delivery_instructions(
                                characters_by_id[speaker_id]["voice_profile"],
                                str(characters_by_id[speaker_id].get("name", speaker_id)),
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
