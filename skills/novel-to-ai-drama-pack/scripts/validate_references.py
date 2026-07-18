from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from asset_stages import (
    StageSelectionError,
    select_active_for_episode,
    select_parent_for_child,
)
from project_io import load_json


_UNKNOWN_TOKEN = re.compile(
    r"@[A-Za-z0-9_\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002fa1f-]+"
)
_CJK_IDEOGRAPH = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f]"
)
_REFERENCE_SPECS = (
    ("prop_ids", "props", "prop_id", "prop_sheet"),
    ("food_ids", "foods", "food_id", "food_image"),
)


@dataclass(frozen=True)
class _TokenOccurrence:
    token: str
    start: int
    end: int
    known: bool


def _single_line(value: object) -> str:
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _object_map(
    data: dict[str, Any],
    collection: str,
    id_field: str,
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    raw_items = data.get(collection)
    if not isinstance(raw_items, list):
        errors.append(f"{collection} must be a list")
        return {}

    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw_items):
        path = f"{collection}[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path} must be an object")
            continue
        object_id = item.get(id_field)
        if not isinstance(object_id, str) or not object_id:
            errors.append(f"{path}.{id_field} must be a nonempty string")
            continue
        result.setdefault(object_id, item)
    return result


def _valid_episode_range(
    asset: dict[str, Any], path: str, errors: list[str]
) -> tuple[int, int] | None | bool:
    if "episode_range" not in asset:
        return None
    value = asset.get("episode_range")
    valid = (
        isinstance(value, list)
        and len(value) == 2
        and _is_positive_integer(value[0])
        and _is_positive_integer(value[1])
        and value[0] <= value[1]
    )
    if not valid:
        errors.append(
            f"{path}.episode_range must be [start, end] positive integers"
        )
        return False
    return value[0], value[1]


def _build_registry(
    data: dict[str, Any], errors: list[str]
) -> tuple[list[dict[str, Any]], set[str]]:
    raw_assets = data.get("assets")
    if not isinstance(raw_assets, list):
        errors.append("assets must be a list")
        return [], set()

    registered: list[dict[str, Any]] = []
    tokens: set[str] = set()
    for index, asset in enumerate(raw_assets):
        path = f"assets[{index}]"
        if not isinstance(asset, dict):
            errors.append(f"{path} must be an object")
            continue
        episode_range = _valid_episode_range(asset, path, errors)
        asset_copy = dict(asset)
        asset_copy["__path"] = path
        asset_copy["__episode_range"] = episode_range
        registered.append(asset_copy)
        token = asset.get("reference_token")
        if (
            asset.get("status") == "completed"
            and isinstance(token, str)
            and token.strip()
        ):
            tokens.add(token)
    return registered, tokens


def _active_asset(
    assets: list[dict[str, Any]],
    episode_number: int,
    *,
    owner_id: str | None = None,
    asset_type: str | None = None,
    asset_id: str | None = None,
) -> dict[str, Any] | None:
    candidates = [
        asset for asset in assets if asset.get("__episode_range") is not False
    ]
    return select_active_for_episode(
        candidates,
        episode_number,
        owner_id=owner_id,
        asset_type=asset_type,
        asset_id=asset_id,
        description=asset_type or asset_id or "asset",
    )


def _asset_identity(asset: dict[str, Any]) -> tuple[str, int]:
    version = asset.get("version")
    return (
        str(asset.get("asset_id")),
        int(version) if _is_positive_integer(version) else 0,
    )


def _scan_tokens(prompt: str, known_tokens: set[str]) -> list[_TokenOccurrence]:
    """Scan delimiter-free tokens while preserving adjacent Chinese prose.

    An exact registered ``_VNNN`` token ends before following CJK narrative;
    ASCII letters, digits, underscores, or hyphens continue the candidate and
    therefore make it unknown. Other unregistered ``@`` runs stay unknown.
    """
    occurrences: list[_TokenOccurrence] = []
    known_longest_first = sorted(known_tokens, key=lambda token: (-len(token), token))
    position = 0
    while True:
        start = prompt.find("@", position)
        if start < 0:
            break
        unknown_match = _UNKNOWN_TOKEN.match(prompt, start)
        candidate = unknown_match.group() if unknown_match is not None else "@"
        candidate_end = unknown_match.end() if unknown_match is not None else start + 1
        if candidate in known_tokens:
            occurrences.append(_TokenOccurrence(candidate, start, candidate_end, True))
            position = candidate_end
            continue

        narrative_prefix = next(
            (
                token
                for token in known_longest_first
                if candidate.startswith(token)
                and len(candidate) > len(token)
                and _CJK_IDEOGRAPH.fullmatch(candidate[len(token)])
            ),
            None,
        )
        if narrative_prefix is not None:
            token_end = start + len(narrative_prefix)
            occurrences.append(
                _TokenOccurrence(narrative_prefix, start, token_end, True)
            )
            position = token_end
            continue

        occurrences.append(_TokenOccurrence(candidate, start, candidate_end, False))
        position = candidate_end
    return occurrences


def _resolve_required_asset(
    assets: list[dict[str, Any]],
    episode_number: int,
    owner_id: str,
    asset_type: str,
    shot_path: str,
    errors: list[str],
) -> dict[str, Any] | None:
    try:
        active = _active_asset(
            assets,
            episode_number,
            owner_id=owner_id,
            asset_type=asset_type,
        )
    except StageSelectionError as error:
        errors.append(f"{shot_path}: {error}")
        return None
    if (
        active is None
        or active.get("status") != "completed"
        or not isinstance(active.get("reference_token"), str)
        or not active["reference_token"].strip()
    ):
        errors.append(
            f"{shot_path}: no active completed {asset_type} for {_single_line(owner_id)}"
        )
        return None
    return active


def _listed_ids(
    shot: dict[str, Any], field: str, shot_path: str, errors: list[str]
) -> list[str]:
    raw_ids = shot.get(field)
    if not isinstance(raw_ids, list):
        errors.append(f"{shot_path}.{field} must be a list")
        return []
    result = []
    for index, value in enumerate(raw_ids):
        if not isinstance(value, str) or not value:
            errors.append(f"{shot_path}.{field}[{index}] must be a nonempty string")
            continue
        result.append(value)
    return result


def _selected_character_extras(
    shot: dict[str, Any],
    assets: list[dict[str, Any]],
    episode_number: int,
    shot_path: str,
    errors: list[str],
) -> dict[str, list[str]]:
    selected: dict[str, list[str]] = {}
    for field, expected_type in (
        ("expression_asset_id", "expression_sheet"),
        ("action_asset_id", "action_sheet"),
    ):
        selected_id = shot.get(field)
        if selected_id is None:
            continue
        if not isinstance(selected_id, str) or not selected_id:
            errors.append(f"{shot_path}.{field} must be null or a nonempty string")
            continue
        try:
            active = _active_asset(assets, episode_number, asset_id=selected_id)
        except StageSelectionError as error:
            errors.append(f"{shot_path}.{field}: {error}")
            continue
        if (
            active is None
            or active.get("status") != "completed"
            or not isinstance(active.get("reference_token"), str)
            or not active["reference_token"].strip()
        ):
            errors.append(
                f"{shot_path}.{field}: no active completed asset for "
                f"{_single_line(selected_id)}"
            )
            continue
        if active.get("asset_type") != expected_type:
            errors.append(f"{shot_path}.{field} must select {expected_type}")
            continue
        owner_id = active.get("owner_id")
        token = active.get("reference_token")
        if not isinstance(owner_id, str) or not isinstance(token, str):
            continue
        listed_character_ids = shot.get("character_ids")
        if (
            not isinstance(listed_character_ids, list)
            or owner_id not in listed_character_ids
        ):
            selected.setdefault(owner_id, []).append(token)
            continue
        character_candidates = [
            candidate
            for candidate in assets
            if candidate.get("owner_id") == owner_id
            and candidate.get("asset_type") == "character_sheet"
            and candidate.get("__episode_range") is not False
        ]
        try:
            parent = select_parent_for_child(
                active,
                character_candidates,
                parent_kind="character_sheet",
            )
            episode_character = _active_asset(
                assets,
                episode_number,
                owner_id=owner_id,
                asset_type="character_sheet",
            )
        except StageSelectionError as error:
            errors.append(f"{shot_path}.{field}: {error}")
            continue
        if (
            episode_character is None
            or _asset_identity(parent) != _asset_identity(episode_character)
        ):
            errors.append(
                f"{shot_path}.{field}: {expected_type} parent does not match "
                f"active character for {_single_line(owner_id)}"
            )
            continue
        selected.setdefault(owner_id, []).append(token)
    return selected


def _required_references(
    shot: dict[str, Any],
    objects: dict[str, dict[str, dict[str, Any]]],
    assets: list[dict[str, Any]],
    episode_number: int,
    shot_path: str,
    errors: list[str],
) -> tuple[list[tuple[str, str, tuple[str, ...]]], set[str]]:
    required: list[tuple[str, str, tuple[str, ...]]] = []
    allowed_tokens: set[str] = set()
    character_ids = _listed_ids(shot, "character_ids", shot_path, errors)
    extras = _selected_character_extras(
        shot, assets, episode_number, shot_path, errors
    )

    for character_id in character_ids:
        character = objects["characters"].get(character_id)
        if character is None:
            errors.append(f"{shot_path}: unknown character id {_single_line(character_id)}")
            continue
        name = character.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{shot_path}: character {_single_line(character_id)} has no name")
            continue
        asset = _resolve_required_asset(
            assets,
            episode_number,
            character_id,
            "character_sheet",
            shot_path,
            errors,
        )
        if asset is None:
            continue
        base_token = asset["reference_token"]
        chain_tokens = [base_token, *extras.get(character_id, [])]
        required.append((character_id, name, tuple(chain_tokens)))
        allowed_tokens.update(chain_tokens)

    extra_owners = sorted(set(extras) - set(character_ids))
    for owner_id in extra_owners:
        errors.append(
            f"{shot_path}: {character_ids[0] if character_ids else 'character'} "
            f"reference chain cannot use asset for {_single_line(owner_id)}"
        )
        allowed_tokens.update(extras[owner_id])

    scene_id = shot.get("scene_id")
    if not isinstance(scene_id, str) or not scene_id:
        errors.append(f"{shot_path}.scene_id must be a nonempty string")
    else:
        scene = objects["scenes"].get(scene_id)
        if scene is None:
            errors.append(f"{shot_path}: unknown scene id {_single_line(scene_id)}")
        else:
            name = scene.get("name")
            asset = _resolve_required_asset(
                assets,
                episode_number,
                scene_id,
                "scene_sheet",
                shot_path,
                errors,
            )
            if isinstance(name, str) and name and asset is not None:
                token = asset["reference_token"]
                required.append((scene_id, name, (token,)))
                allowed_tokens.add(token)

    for field, collection, id_field, asset_type in _REFERENCE_SPECS:
        for object_id in _listed_ids(shot, field, shot_path, errors):
            item = objects[collection].get(object_id)
            if item is None:
                errors.append(
                    f"{shot_path}: unknown {id_field[:-3]} id {_single_line(object_id)}"
                )
                continue
            name = item.get("name")
            asset = _resolve_required_asset(
                assets,
                episode_number,
                object_id,
                asset_type,
                shot_path,
                errors,
            )
            if isinstance(name, str) and name and asset is not None:
                token = asset["reference_token"]
                required.append((object_id, name, (token,)))
                allowed_tokens.add(token)
    return required, allowed_tokens


def _check_prompt(
    prompt: object,
    prompt_path: str,
    required: list[tuple[str, str, tuple[str, ...]]],
    allowed_tokens: set[str],
    known_tokens: set[str],
    errors: list[str],
) -> set[str]:
    if not isinstance(prompt, str):
        errors.append(f"{prompt_path} must be a string")
        return set()

    occurrences = _scan_tokens(prompt, known_tokens)
    for occurrence in occurrences:
        if not occurrence.known:
            errors.append(
                f"{prompt_path}: unknown reference token: "
                f"{_single_line(occurrence.token)}"
            )

    known_values = {
        occurrence.token for occurrence in occurrences if occurrence.known
    }
    consumed: set[int] = set()
    for object_id, name, chain_tokens in required:
        base_token = chain_tokens[0]
        chain = "".join(chain_tokens)
        exact = f"{name}{chain}"
        match_start = 0
        matching_indices: list[int] | None = None
        while True:
            exact_start = prompt.find(exact, match_start)
            if exact_start < 0:
                break
            token_start = exact_start + len(name)
            candidate_indices: list[int] = []
            for token in chain_tokens:
                occurrence_index = next(
                    (
                        index
                        for index, occurrence in enumerate(occurrences)
                        if index not in consumed
                        and occurrence.known
                        and occurrence.token == token
                        and occurrence.start == token_start
                        and occurrence.end == token_start + len(token)
                    ),
                    None,
                )
                if occurrence_index is None:
                    break
                candidate_indices.append(occurrence_index)
                token_start += len(token)
            if len(candidate_indices) == len(chain_tokens):
                matching_indices = candidate_indices
                break
            match_start = exact_start + 1

        if matching_indices is not None:
            consumed.update(matching_indices)
            continue
        if base_token not in known_values:
            same_owner_tokens = [
                token
                for token in known_values
                if f"_{object_id}_" in token and token != base_token
            ]
            if same_owner_tokens:
                errors.append(
                    f"{prompt_path}: {object_id} active reference must be "
                    f"{_single_line(base_token)}"
                )
            else:
                errors.append(f"{prompt_path}: {object_id} missing inline reference")
        elif len(chain_tokens) > 1:
            errors.append(
                f"{prompt_path}: {object_id} reference chain must be exactly "
                f"{_single_line(exact)}"
            )
        else:
            errors.append(
                f"{prompt_path}: {object_id} reference must immediately follow "
                f"{_single_line(name)}"
            )

    for index, occurrence in enumerate(occurrences):
        if not occurrence.known or index in consumed:
            continue
        if occurrence.token in allowed_tokens:
            errors.append(
                f"{prompt_path}: detached or duplicate reference token: "
                f"{_single_line(occurrence.token)}"
            )
        else:
            errors.append(
                f"{prompt_path}: unrelated reference token: "
                f"{_single_line(occurrence.token)}"
            )
    return {occurrence.token for occurrence in occurrences}


def validate_references(data: dict[str, Any]) -> list[str]:
    """Validate sentence-inline asset references in every shot prompt."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["project root must be an object"]

    objects = {
        "characters": _object_map(
            data, "characters", "character_id", errors
        ),
        "scenes": _object_map(data, "scenes", "scene_id", errors),
        "props": _object_map(data, "props", "prop_id", errors),
        "foods": _object_map(data, "foods", "food_id", errors),
    }
    assets, known_tokens = _build_registry(data, errors)
    episodes = data.get("episodes")
    if not isinstance(episodes, list):
        errors.append("episodes must be a list")
        return errors

    for episode_index, episode in enumerate(episodes):
        episode_path = f"episodes[{episode_index}]"
        if not isinstance(episode, dict):
            errors.append(f"{episode_path} must be an object")
            continue
        episode_number = episode.get("episode_number")
        if not _is_positive_integer(episode_number):
            errors.append(f"{episode_path}.episode_number must be a positive integer")
            continue
        shots = episode.get("shots")
        if not isinstance(shots, list):
            errors.append(f"{episode_path}.shots must be a list")
            continue
        for shot_index, shot in enumerate(shots):
            shot_path = f"{episode_path}.shots[{shot_index}]"
            if not isinstance(shot, dict):
                errors.append(f"{shot_path} must be an object")
                continue
            required, allowed_tokens = _required_references(
                shot,
                objects,
                assets,
                episode_number,
                shot_path,
                errors,
            )
            zh_tokens = _check_prompt(
                shot.get("prompt_zh"),
                f"{shot_path}.prompt_zh",
                required,
                allowed_tokens,
                known_tokens,
                errors,
            )
            en_tokens = _check_prompt(
                shot.get("prompt_en"),
                f"{shot_path}.prompt_en",
                required,
                allowed_tokens,
                known_tokens,
                errors,
            )
            if zh_tokens != en_tokens:
                errors.append(
                    f"{shot_path}: prompt_zh and prompt_en reference token sets differ"
                )
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate sentence-inline asset references in a project JSON file."
    )
    parser.add_argument("project", type=Path, help="project JSON path")
    args = parser.parse_args(argv)
    try:
        data = load_json(args.project)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        print(f"invalid project JSON: {_single_line(error)}", file=sys.stderr)
        return 1

    errors = validate_references(data)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
