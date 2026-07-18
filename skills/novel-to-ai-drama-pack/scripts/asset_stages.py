from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class StageSelectionError(ValueError):
    """Raised when active asset stages cannot be selected deterministically."""


def _positive_version(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def normalized_stage_key(
    asset: dict[str, Any], label: str
) -> tuple[int, int] | None:
    value = asset.get("episode_range")
    if value is None:
        return None
    if not (
        isinstance(value, list)
        and len(value) == 2
        and all(
            isinstance(item, int) and not isinstance(item, bool) and item > 0
            for item in value
        )
        and value[0] <= value[1]
    ):
        raise StageSelectionError(f"{label} has invalid episode_range")
    return value[0], value[1]


def _asset_label(asset: dict[str, Any]) -> str:
    version = _positive_version(asset.get("version")) or 0
    return f"{asset.get('asset_id')} V{version:03d}"


def active_assets_by_stage(
    assets: Iterable[dict[str, Any]],
    *,
    owner_id: str | None = None,
    asset_type: str | None = None,
    asset_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return only the highest non-discarded version per logical stage."""
    selected: dict[tuple[str, tuple[int, int] | None], dict[str, Any]] = {}
    for asset in assets:
        if asset.get("status") == "discarded":
            continue
        if owner_id is not None and asset.get("owner_id") != owner_id:
            continue
        if asset_type is not None and asset.get("asset_type") != asset_type:
            continue
        if asset_id is not None and asset.get("asset_id") != asset_id:
            continue
        version = _positive_version(asset.get("version"))
        current_asset_id = asset.get("asset_id")
        if version is None or not isinstance(current_asset_id, str) or not current_asset_id:
            continue
        stage = normalized_stage_key(asset, _asset_label(asset))
        key = (current_asset_id, stage)
        previous = selected.get(key)
        if previous is None or version > int(previous["version"]):
            selected[key] = asset
    return sorted(
        selected.values(),
        key=lambda item: (
            str(item.get("asset_id", "")),
            normalized_stage_key(item, _asset_label(item)) is not None,
            normalized_stage_key(item, _asset_label(item)) or (0, 0),
            int(item["version"]),
        ),
    )


def _unique_candidate(
    candidates: list[dict[str, Any]], description: str
) -> dict[str, Any] | None:
    if len(candidates) > 1:
        raise StageSelectionError(f"ambiguous {description}")
    return candidates[0] if candidates else None


def select_active_for_episode(
    assets: Iterable[dict[str, Any]],
    episode_number: int,
    *,
    owner_id: str | None = None,
    asset_type: str | None = None,
    asset_id: str | None = None,
    description: str | None = None,
) -> dict[str, Any] | None:
    active = active_assets_by_stage(
        assets,
        owner_id=owner_id,
        asset_type=asset_type,
        asset_id=asset_id,
    )
    label = description or asset_type or asset_id or "asset"
    ranged = [
        asset
        for asset in active
        if (stage := normalized_stage_key(asset, _asset_label(asset))) is not None
        and stage[0] <= episode_number <= stage[1]
    ]
    if ranged:
        stages = {
            normalized_stage_key(asset, _asset_label(asset)) for asset in ranged
        }
        if len(stages) > 1:
            raise StageSelectionError(
                f"ambiguous {label} ranged stages for episode {episode_number}"
            )
        return _unique_candidate(
            ranged, f"{label} active stage for episode {episode_number}"
        )
    global_assets = [
        asset
        for asset in active
        if normalized_stage_key(asset, _asset_label(asset)) is None
    ]
    return _unique_candidate(
        global_assets, f"{label} global stage for episode {episode_number}"
    )


def select_parent_for_child(
    child: dict[str, Any],
    parents: Iterable[dict[str, Any]],
    *,
    parent_kind: str,
) -> dict[str, Any]:
    child_label = _asset_label(child)
    child_stage = normalized_stage_key(child, child_label)
    active = active_assets_by_stage(parents, asset_type=parent_kind)
    if not active:
        raise StageSelectionError(
            f"{child_label} has no matching {parent_kind} parent"
        )

    def unique_or_error(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        return _unique_candidate(
            candidates, f"{parent_kind} parent for {child_label}"
        )

    if child_stage is not None:
        exact = [
            parent
            for parent in active
            if normalized_stage_key(parent, _asset_label(parent)) == child_stage
        ]
        selected = unique_or_error(exact)
        if selected is not None:
            return selected

        covering = [
            parent
            for parent in active
            if (stage := normalized_stage_key(parent, _asset_label(parent)))
            is not None
            and stage[0] <= child_stage[0]
            and stage[1] >= child_stage[1]
        ]
        selected = unique_or_error(covering)
        if selected is not None:
            return selected

        global_assets = [
            parent
            for parent in active
            if normalized_stage_key(parent, _asset_label(parent)) is None
        ]
        selected = unique_or_error(global_assets)
        if selected is not None:
            return selected
        raise StageSelectionError(
            f"{child_label} has no matching {parent_kind} parent"
        )

    global_assets = [
        parent
        for parent in active
        if normalized_stage_key(parent, _asset_label(parent)) is None
    ]
    selected = unique_or_error(global_assets)
    if selected is not None:
        return selected
    selected = unique_or_error(active)
    if selected is not None:
        return selected
    raise StageSelectionError(
        f"{child_label} has no matching {parent_kind} parent"
    )
