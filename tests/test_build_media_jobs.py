from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import build_media_jobs as media_jobs
from build_media_jobs import MediaJobError, build_media_jobs, sort_media_jobs, write_jobs
from workflow_guard import create_redo_asset


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "valid-project.json"
SCRIPT_PATH = (
    ROOT
    / "skills"
    / "novel-to-ai-drama-pack"
    / "scripts"
    / "build_media_jobs.py"
)


@pytest.fixture
def valid_project() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _mark_required_media_pending(project: dict[str, Any]) -> None:
    for asset in project["assets"]:
        asset["status"] = "confirmed"
        asset["checksum"] = ""


def _job(jobs: list[dict[str, Any]], kind: str, owner_id: str) -> dict[str, Any]:
    return next(
        job
        for job in jobs
        if job["kind"] == kind and job["owner_id"] == owner_id
    )


def _append_character_version(
    project: dict[str, Any], version: int
) -> dict[str, Any]:
    original = next(
        asset
        for asset in project["assets"]
        if asset["asset_id"] == "CHAR_C001" and asset["version"] == 1
    )
    asset = copy.deepcopy(original)
    asset.update(
        {
            "version": version,
            "file_name": f"CHAR_C001_V{version:03d}.png",
            "relative_path": (
                f"assets/characters/CHAR_C001_V{version:03d}.png"
            ),
            "reference_token": f"@角色_C001_林岚_综合设定图_V{version:03d}",
            "status": "confirmed",
        }
    )
    project["assets"].append(asset)
    return asset


def _replace_in_episode_prompts(
    project: dict[str, Any], episode_indexes: tuple[int, ...], old: str, new: str
) -> None:
    for episode_index in episode_indexes:
        for field in ("prompt_zh", "prompt_en"):
            shot = project["episodes"][episode_index]["shots"][0]
            shot[field] = shot[field].replace(old, new)


def test_completed_project_needs_no_media_jobs(
    valid_project: dict[str, Any],
) -> None:
    assert build_media_jobs(valid_project) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("asset_type", "expression_sheet"),
        ("owner_type", "scene"),
        ("owner_id", "C002"),
    ],
)
def test_media_jobs_reject_forged_asset_history_identity(
    valid_project: dict[str, Any], field: str, value: str
) -> None:
    forged = _append_character_version(valid_project, 2)
    forged[field] = value

    with pytest.raises(
        MediaJobError,
        match=(
            f"asset version history for CHAR_C001 has inconsistent {field}"
        ),
    ):
        build_media_jobs(valid_project)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("asset_type", None),
        ("asset_type", "   "),
        ("owner_type", None),
        ("owner_type", "   "),
        ("owner_id", None),
        ("owner_id", "   "),
    ],
)
def test_media_jobs_require_complete_identity_on_every_asset_version(
    valid_project: dict[str, Any], field: str, value: object
) -> None:
    valid_project["assets"][1][field] = value

    with pytest.raises(
        MediaJobError,
        match=rf"assets\[1\]\.{field} must be a nonempty string",
    ):
        build_media_jobs(valid_project)


def test_media_jobs_accept_legal_redo_v003_as_active_version(
    valid_project: dict[str, Any],
) -> None:
    redone = create_redo_asset(valid_project, "CHAR_C001", "first retry")
    redone = create_redo_asset(redone, "CHAR_C001", "second retry")
    _replace_in_episode_prompts(
        redone,
        (0, 1, 2),
        "@角色_C001_林岚_综合设定图_V001",
        "@角色_C001_林岚_综合设定图_V003",
    )
    redone["assets"] = [
        asset
        for asset in redone["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    jobs = build_media_jobs(redone)

    character_job = _job(jobs, "character_sheet", "C001")
    shot_job = _job(jobs, "shot_sample", "E001_SH001")
    assert (character_job["asset_id"], character_job["version"]) == (
        "CHAR_C001",
        3,
    )
    assert character_job["job_id"] in shot_job["depends_on"]


def test_media_jobs_follow_required_stage_order_and_dependencies(
    valid_project: dict[str, Any],
) -> None:
    _mark_required_media_pending(valid_project)

    jobs = build_media_jobs(valid_project)
    kinds = [job["kind"] for job in jobs]

    expected_order = [
        "style_reference",
        "character_sheet",
        "expression_sheet",
        "action_sheet",
        "scene_sheet",
        "prop_sheet",
        "food_image",
        "voice_sample",
        "shot_sample",
    ]
    assert [kind for kind in expected_order if kind in kinds] == expected_order
    assert kinds.index("style_reference") < kinds.index("character_sheet")
    assert kinds.index("character_sheet") < kinds.index("expression_sheet")
    assert kinds.index("character_sheet") < kinds.index("action_sheet")
    assert kinds.index("action_sheet") < kinds.index("scene_sheet")
    assert kinds.index("scene_sheet") < kinds.index("prop_sheet")
    assert kinds.index("food_image") < kinds.index("voice_sample")
    assert kinds.index("voice_sample") < kinds.index("shot_sample")

    style = _job(jobs, "style_reference", "PRJ001")
    character = _job(jobs, "character_sheet", "C001")
    expression = _job(jobs, "expression_sheet", "C001")
    action = _job(jobs, "action_sheet", "C001")
    shot = _job(jobs, "shot_sample", "E001_SH001")

    assert character["depends_on"] == [style["job_id"]]
    assert expression["depends_on"] == [character["job_id"]]
    assert action["depends_on"] == [character["job_id"]]
    assert set(shot["depends_on"]) == {
        character["job_id"],
        _job(jobs, "scene_sheet", "S001")["job_id"],
        _job(jobs, "prop_sheet", "P001")["job_id"],
        _job(jobs, "food_image", "F001")["job_id"],
    }


def test_only_first_episode_gets_one_sample_image_per_shot(
    valid_project: dict[str, Any],
) -> None:
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    jobs = build_media_jobs(valid_project)
    shot_jobs = [job for job in jobs if job["kind"] == "shot_sample"]

    assert [job["owner_id"] for job in shot_jobs] == ["E001_SH001"]
    assert all(job.get("episode_id") != "E002" for job in shot_jobs)


def test_each_first_episode_shot_gets_exactly_one_sample_job(
    valid_project: dict[str, Any],
) -> None:
    second_shot = copy.deepcopy(valid_project["episodes"][0]["shots"][0])
    second_shot["shot_id"] = "E001_SH002"
    valid_project["episodes"][0]["shots"].append(second_shot)
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    shot_jobs = [
        job for job in build_media_jobs(valid_project) if job["kind"] == "shot_sample"
    ]

    assert [job["owner_id"] for job in shot_jobs] == ["E001_SH001", "E001_SH002"]


def test_shot_job_collects_exact_active_inline_tokens_only(
    valid_project: dict[str, Any],
) -> None:
    shot = valid_project["episodes"][0]["shots"][0]
    shot["prompt_zh"] += "，旁边还有普通茶杯但不需要资产图"
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    job = _job(build_media_jobs(valid_project), "shot_sample", "E001_SH001")

    assert job["reference_tokens"] == [
        "@角色_C001_林岚_综合设定图_V001",
        "@场景_S001_酒楼包厢_场景设定图_V001",
        "@食物_F001_莲花酥_食物设定图_V001",
        "@道具_P001_青铜长剑_道具设定图_V001",
    ]
    assert all("茶杯" not in token for token in job["reference_tokens"])


def test_shot_job_collects_selected_expression_and_action_tokens(
    valid_project: dict[str, Any],
) -> None:
    shot = valid_project["episodes"][0]["shots"][0]
    character_token = "@角色_C001_林岚_综合设定图_V001"
    expression_token = "@角色_C001_林岚_表情设定图_V001"
    action_token = "@角色_C001_林岚_动作设定图_V001"
    shot["expression_asset_id"] = "EXPR_C001"
    shot["action_asset_id"] = "ACTION_C001"
    for field in ("prompt_zh", "prompt_en"):
        shot[field] = shot[field].replace(
            character_token,
            f"{character_token}{expression_token}{action_token}",
        )
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    job = _job(build_media_jobs(valid_project), "shot_sample", "E001_SH001")

    assert job["reference_tokens"][:3] == [
        character_token,
        expression_token,
        action_token,
    ]


def test_shot_job_uses_version_active_for_its_episode(
    valid_project: dict[str, Any],
) -> None:
    character_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character_v2 = copy.deepcopy(character_v1)
    character_v2.update(
        {
            "version": 2,
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
            "file_name": "CHAR_C001_V002.png",
            "relative_path": "assets/characters/CHAR_C001_V002.png",
            "episode_range": [2, 3],
        }
    )
    valid_project["assets"].append(character_v2)
    _replace_in_episode_prompts(
        valid_project,
        (1, 2),
        "@角色_C001_林岚_综合设定图_V001",
        "@角色_C001_林岚_综合设定图_V002",
    )
    for asset in valid_project["assets"]:
        if asset["asset_type"] in {"expression_sheet", "action_sheet"}:
            asset["episode_range"] = [1, 1]
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    job = _job(build_media_jobs(valid_project), "shot_sample", "E001_SH001")

    assert "@角色_C001_林岚_综合设定图_V001" in job["reference_tokens"]
    assert "@角色_C001_林岚_综合设定图_V002" not in job["reference_tokens"]


def test_first_episode_schedules_exact_pending_ranged_character_version(
    valid_project: dict[str, Any],
) -> None:
    character_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character_v1.update({"status": "confirmed", "episode_range": [1, 1]})
    character_v2 = copy.deepcopy(character_v1)
    character_v2.update(
        {
            "version": 2,
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
            "file_name": "CHAR_C001_V002.png",
            "relative_path": "assets/characters/CHAR_C001_V002.png",
            "status": "completed",
            "episode_range": [2, 3],
        }
    )
    valid_project["assets"].append(character_v2)
    _replace_in_episode_prompts(
        valid_project,
        (1, 2),
        "@角色_C001_林岚_综合设定图_V001",
        "@角色_C001_林岚_综合设定图_V002",
    )
    for asset in valid_project["assets"]:
        if asset["asset_type"] in {"expression_sheet", "action_sheet"}:
            asset["episode_range"] = [1, 1]
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    jobs = build_media_jobs(valid_project)
    character_job = next(
        job
        for job in jobs
        if job["kind"] == "character_sheet" and job["version"] == 1
    )
    shot_job = _job(jobs, "shot_sample", "E001_SH001")

    assert character_job["output"]["reference_token"].endswith("_V001")
    assert character_job["job_id"] in shot_job["depends_on"]


def test_expression_versions_bind_only_matching_character_stage(
    valid_project: dict[str, Any],
) -> None:
    character_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character_v1.update({"status": "confirmed", "episode_range": [1, 1]})
    character_v2 = copy.deepcopy(character_v1)
    character_v2.update(
        {
            "version": 2,
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
            "file_name": "CHAR_C001_V002.png",
            "relative_path": "assets/characters/CHAR_C001_V002.png",
            "episode_range": [2, 3],
        }
    )
    expression_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "expression_sheet"
    )
    expression_v1.update({"status": "confirmed", "episode_range": [1, 1]})
    expression_v2 = copy.deepcopy(expression_v1)
    expression_v2.update(
        {
            "version": 2,
            "reference_token": "@角色_C001_林岚_表情设定图_V002",
            "file_name": "EXPR_C001_V002.png",
            "relative_path": "assets/characters/EXPR_C001_V002.png",
            "episode_range": [2, 3],
        }
    )
    action_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "action_sheet"
    )
    action_v1["episode_range"] = [1, 1]
    valid_project["assets"].extend([character_v2, expression_v2])
    _replace_in_episode_prompts(
        valid_project,
        (1, 2),
        "@角色_C001_林岚_综合设定图_V001",
        "@角色_C001_林岚_综合设定图_V002",
    )
    shot = valid_project["episodes"][0]["shots"][0]
    shot["expression_asset_id"] = "EXPR_C001"
    for field in ("prompt_zh", "prompt_en"):
        shot[field] = shot[field].replace(
            "@角色_C001_林岚_综合设定图_V001",
            "@角色_C001_林岚_综合设定图_V001"
            "@角色_C001_林岚_表情设定图_V001",
        )

    jobs = build_media_jobs(valid_project)
    expression_jobs = {
        job["version"]: job
        for job in jobs
        if job["kind"] == "expression_sheet"
    }
    character_jobs = {
        job["version"]: job
        for job in jobs
        if job["kind"] == "character_sheet"
    }

    assert expression_jobs[1]["reference_tokens"] == [
        "@角色_C001_林岚_综合设定图_V001"
    ]
    assert expression_jobs[1]["depends_on"] == [character_jobs[1]["job_id"]]
    assert expression_jobs[2]["reference_tokens"] == [
        "@角色_C001_林岚_综合设定图_V002"
    ]
    assert expression_jobs[2]["depends_on"] == [character_jobs[2]["job_id"]]


def test_ambiguous_stage_parent_match_is_rejected(
    valid_project: dict[str, Any],
) -> None:
    character_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character_v1["episode_range"] = [1, 1]
    character_v2 = copy.deepcopy(character_v1)
    character_v2.update(
        {
            "version": 2,
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
            "file_name": "CHAR_C001_V002.png",
            "relative_path": "assets/characters/CHAR_C001_V002.png",
            "episode_range": [2, 3],
        }
    )
    expression = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "expression_sheet"
    )
    expression.update({"status": "confirmed"})
    valid_project["assets"].append(character_v2)

    with pytest.raises(MediaJobError, match="ambiguous.*character_sheet"):
        build_media_jobs(valid_project)


def test_character_stage_binds_only_matching_style_version(
    valid_project: dict[str, Any],
) -> None:
    valid_project["generation_settings"]["script_episode_count"] = 1
    style_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "style_reference"
    )
    style_v1.update({"status": "confirmed", "episode_range": [1, 1]})
    style_v2 = copy.deepcopy(style_v1)
    style_v2.update(
        {
            "version": 2,
            "reference_token": "@项目_PRJ001_测试短剧_风格参考图_V002",
            "file_name": "STYLE_PRJ001_V002.png",
            "relative_path": "assets/style/STYLE_PRJ001_V002.png",
            "status": "completed",
            "episode_range": [2, 3],
        }
    )
    valid_project["assets"].append(style_v2)
    for asset in valid_project["assets"]:
        if asset["asset_type"] in {
            "character_sheet",
            "expression_sheet",
            "action_sheet",
            "scene_sheet",
            "prop_sheet",
            "food_image",
        }:
            asset["episode_range"] = [1, 1]
            if asset["asset_type"] == "character_sheet":
                asset["status"] = "confirmed"

    jobs = build_media_jobs(valid_project)
    style_job = next(job for job in jobs if job["kind"] == "style_reference")
    character_job = next(job for job in jobs if job["kind"] == "character_sheet")

    assert character_job["reference_tokens"] == [
        "@项目_PRJ001_测试短剧_风格参考图_V001"
    ]
    assert character_job["depends_on"] == [style_job["job_id"]]


def test_same_global_stage_uses_only_latest_style_version(
    valid_project: dict[str, Any],
) -> None:
    style_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "style_reference"
    )
    style_v2 = copy.deepcopy(style_v1)
    style_v2.update(
        {
            "version": 2,
            "reference_token": "@项目_PRJ001_测试短剧_风格参考图_V002",
            "file_name": "STYLE_PRJ001_V002.png",
            "relative_path": "assets/style/STYLE_PRJ001_V002.png",
            "status": "confirmed",
        }
    )
    valid_project["assets"].append(style_v2)
    character = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character["status"] = "confirmed"

    jobs = build_media_jobs(valid_project)
    style_jobs = [job for job in jobs if job["kind"] == "style_reference"]
    character_job = _job(jobs, "character_sheet", "C001")

    assert [(job["asset_id"], job["version"]) for job in style_jobs] == [
        ("STYLE_PRJ001", 2)
    ]
    assert character_job["reference_tokens"] == [
        "@项目_PRJ001_测试短剧_风格参考图_V002"
    ]
    assert character_job["depends_on"] == [style_jobs[0]["job_id"]]


def test_same_global_stage_schedules_only_latest_character_and_expression_versions(
    valid_project: dict[str, Any],
) -> None:
    character_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character_v2 = copy.deepcopy(character_v1)
    character_v2.update(
        {
            "version": 2,
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
            "file_name": "CHAR_C001_V002.png",
            "relative_path": "assets/characters/CHAR_C001_V002.png",
            "status": "confirmed",
        }
    )
    expression_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "expression_sheet"
    )
    expression_v2 = copy.deepcopy(expression_v1)
    expression_v2.update(
        {
            "version": 2,
            "reference_token": "@角色_C001_林岚_表情设定图_V002",
            "file_name": "EXPR_C001_V002.png",
            "relative_path": "assets/characters/EXPR_C001_V002.png",
            "status": "confirmed",
        }
    )
    valid_project["assets"].extend([character_v2, expression_v2])
    _replace_in_episode_prompts(
        valid_project,
        (1, 2),
        "@角色_C001_林岚_综合设定图_V001",
        "@角色_C001_林岚_综合设定图_V002",
    )
    current_shot = valid_project["episodes"][0]["shots"][0]
    current_shot["expression_asset_id"] = "EXPR_C001"
    for field in ("prompt_zh", "prompt_en"):
        current_shot[field] = current_shot[field].replace(
            "@角色_C001_林岚_综合设定图_V001",
            "@角色_C001_林岚_综合设定图_V002"
            "@角色_C001_林岚_表情设定图_V002",
        )

    jobs = build_media_jobs(valid_project)
    character_jobs = [job for job in jobs if job["kind"] == "character_sheet"]
    expression_jobs = [job for job in jobs if job["kind"] == "expression_sheet"]

    assert [(job["asset_id"], job["version"]) for job in character_jobs] == [
        ("CHAR_C001", 2)
    ]
    assert [(job["asset_id"], job["version"]) for job in expression_jobs] == [
        ("EXPR_C001", 2)
    ]
    assert expression_jobs[0]["reference_tokens"] == [
        "@角色_C001_林岚_综合设定图_V002"
    ]
    assert expression_jobs[0]["depends_on"] == [character_jobs[0]["job_id"]]


def test_ranged_character_stage_overrides_completed_global_stage_for_episode_one(
    valid_project: dict[str, Any],
) -> None:
    character_ranged = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character_ranged.update({"status": "confirmed", "episode_range": [1, 1]})
    character_global = copy.deepcopy(character_ranged)
    character_global.pop("episode_range")
    character_global.update(
        {
            "version": 2,
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
            "file_name": "CHAR_C001_V002.png",
            "relative_path": "assets/characters/CHAR_C001_V002.png",
            "status": "completed",
        }
    )
    expression = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "expression_sheet"
    )
    expression.update({"status": "confirmed", "episode_range": [1, 1]})
    valid_project["assets"].append(character_global)
    _replace_in_episode_prompts(
        valid_project,
        (1, 2),
        "@角色_C001_林岚_综合设定图_V001",
        "@角色_C001_林岚_综合设定图_V002",
    )
    current_shot = valid_project["episodes"][0]["shots"][0]
    current_shot["expression_asset_id"] = "EXPR_C001"
    for field in ("prompt_zh", "prompt_en"):
        current_shot[field] = current_shot[field].replace(
            "@角色_C001_林岚_综合设定图_V001",
            "@角色_C001_林岚_综合设定图_V001"
            "@角色_C001_林岚_表情设定图_V001",
        )
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    jobs = build_media_jobs(valid_project)
    character_job = _job(jobs, "character_sheet", "C001")
    expression_job = _job(jobs, "expression_sheet", "C001")
    shot_job = _job(jobs, "shot_sample", "E001_SH001")

    assert character_job["version"] == 1
    assert expression_job["version"] == 1
    assert expression_job["reference_tokens"] == [
        "@角色_C001_林岚_综合设定图_V001"
    ]
    assert expression_job["depends_on"] == [character_job["job_id"]]
    assert character_job["job_id"] in shot_job["depends_on"]
    assert expression_job["job_id"] in shot_job["depends_on"]
    assert "@角色_C001_林岚_综合设定图_V001" in shot_job["reference_tokens"]
    assert "@角色_C001_林岚_综合设定图_V002" not in shot_job["reference_tokens"]


def test_overlapping_ranged_character_stages_are_rejected(
    valid_project: dict[str, Any],
) -> None:
    character_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character_v1["episode_range"] = [1, 2]
    character_v2 = copy.deepcopy(character_v1)
    character_v2.update(
        {
            "version": 2,
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
            "file_name": "CHAR_C001_V002.png",
            "relative_path": "assets/characters/CHAR_C001_V002.png",
            "episode_range": [1, 3],
        }
    )
    valid_project["assets"].append(character_v2)

    with pytest.raises(MediaJobError, match="ambiguous.*character_sheet"):
        build_media_jobs(valid_project)


def test_future_overlapping_character_stages_are_rejected_before_episode_use(
    valid_project: dict[str, Any],
) -> None:
    character_global = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character_v2 = copy.deepcopy(character_global)
    character_v2.update(
        {
            "version": 2,
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
            "file_name": "CHAR_C001_V002.png",
            "relative_path": "assets/characters/CHAR_C001_V002.png",
            "episode_range": [2, 3],
        }
    )
    character_v3 = copy.deepcopy(character_global)
    character_v3.update(
        {
            "version": 3,
            "reference_token": "@角色_C001_林岚_综合设定图_V003",
            "file_name": "CHAR_C001_V003.png",
            "relative_path": "assets/characters/CHAR_C001_V003.png",
            "episode_range": [3, 4],
        }
    )
    valid_project["assets"].extend([character_v2, character_v3])

    with pytest.raises(
        MediaJobError, match="ambiguous.*character_sheet.*overlap"
    ):
        build_media_jobs(valid_project)


@pytest.mark.parametrize(
    ("kind", "field", "asset_id", "token"),
    [
        (
            "expression_sheet",
            "expression_asset_id",
            "EXPR_C001",
            "@角色_C001_林岚_表情设定图_V001",
        ),
        (
            "action_sheet",
            "action_asset_id",
            "ACTION_C001",
            "@角色_C001_林岚_动作设定图_V001",
        ),
    ],
)
def test_global_character_extra_cannot_bind_to_ranged_episode_character(
    valid_project: dict[str, Any],
    kind: str,
    field: str,
    asset_id: str,
    token: str,
) -> None:
    character_ranged = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character_ranged["episode_range"] = [1, 1]
    character_global = copy.deepcopy(character_ranged)
    character_global.pop("episode_range")
    character_global.update(
        {
            "version": 2,
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
            "file_name": "CHAR_C001_V002.png",
            "relative_path": "assets/characters/CHAR_C001_V002.png",
        }
    )
    valid_project["assets"].append(character_global)
    current_shot = valid_project["episodes"][0]["shots"][0]
    current_shot[field] = asset_id
    for prompt_field in ("prompt_zh", "prompt_en"):
        current_shot[prompt_field] = current_shot[prompt_field].replace(
            "@角色_C001_林岚_综合设定图_V001",
            "@角色_C001_林岚_综合设定图_V001" + token,
        )

    with pytest.raises(MediaJobError, match=f"{kind}.*parent.*active character"):
        build_media_jobs(valid_project)


@pytest.mark.parametrize(
    ("kind", "field", "asset_id", "token"),
    [
        (
            "expression_sheet",
            "expression_asset_id",
            "EXPR_C001",
            "@角色_C001_林岚_表情设定图_V001",
        ),
        (
            "action_sheet",
            "action_asset_id",
            "ACTION_C001",
            "@角色_C001_林岚_动作设定图_V001",
        ),
    ],
)
def test_ranged_character_extra_can_bind_to_same_ranged_character(
    valid_project: dict[str, Any],
    kind: str,
    field: str,
    asset_id: str,
    token: str,
) -> None:
    character_ranged = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character_ranged["episode_range"] = [1, 1]
    character_global = copy.deepcopy(character_ranged)
    character_global.pop("episode_range")
    character_global.update(
        {
            "version": 2,
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
            "file_name": "CHAR_C001_V002.png",
            "relative_path": "assets/characters/CHAR_C001_V002.png",
        }
    )
    extra = next(
        asset for asset in valid_project["assets"] if asset["asset_type"] == kind
    )
    extra["episode_range"] = [1, 1]
    valid_project["assets"].append(character_global)
    _replace_in_episode_prompts(
        valid_project,
        (1, 2),
        "@角色_C001_林岚_综合设定图_V001",
        "@角色_C001_林岚_综合设定图_V002",
    )
    current_shot = valid_project["episodes"][0]["shots"][0]
    current_shot[field] = asset_id
    for prompt_field in ("prompt_zh", "prompt_en"):
        current_shot[prompt_field] = current_shot[prompt_field].replace(
            "@角色_C001_林岚_综合设定图_V001",
            "@角色_C001_林岚_综合设定图_V001" + token,
        )

    assert build_media_jobs(valid_project) == []


def test_distinct_incomplete_voice_stages_keep_distinct_jobs(
    valid_project: dict[str, Any],
) -> None:
    voice_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "voice_sample"
    )
    voice_v1["status"] = "confirmed"
    voice_v1["episode_range"] = [1, 1]
    voice_v2 = copy.deepcopy(voice_v1)
    voice_v2.update(
        {
            "version": 2,
            "reference_token": "@声音_AUD_C001_林岚_标准声音_V002",
            "file_name": "AUD_C001_V002.wav",
            "relative_path": "assets/audio/AUD_C001_V002.wav",
            "status": "redo",
            "episode_range": [2, 3],
        }
    )
    valid_project["assets"].append(voice_v2)

    voice_jobs = [
        job
        for job in build_media_jobs(valid_project)
        if job["kind"] == "voice_sample" and job["owner_id"] == "C001"
    ]

    assert [(job["asset_id"], job["version"]) for job in voice_jobs] == [
        ("AUD_C001", 1),
        ("AUD_C001", 2),
    ]
    assert len({job["job_id"] for job in voice_jobs}) == 2


def test_secondary_props_foods_and_scenes_do_not_create_jobs(
    valid_project: dict[str, Any],
) -> None:
    valid_project["scenes"].append(
        {"scene_id": "S002", "name": "小厨房", "importance": "secondary"}
    )
    valid_project["props"].append(
        {"prop_id": "P002", "name": "茶杯", "importance": "secondary"}
    )
    valid_project["foods"].append(
        {"food_id": "F002", "name": "白粥", "importance": "secondary"}
    )

    jobs = build_media_jobs(valid_project)

    assert not any(job["owner_id"] in {"S002", "P002", "F002"} for job in jobs)


@pytest.mark.parametrize(
    ("episode_index", "importance"), [(1, "minor"), (2, "cameo")]
)
def test_later_script_episode_first_appearance_gets_base_character_and_scene_jobs(
    valid_project: dict[str, Any], episode_index: int, importance: str
) -> None:
    valid_project["characters"].append(
        {
            "character_id": "C002",
            "name": "苏禾",
            "importance": importance,
            "role": "witness",
            "appearance": "灰衣短发，眼神警觉",
            "voice_profile": {
                "voice": "自然青年声",
                "tone": "警惕",
                "pace": "中速",
                "sample_text": "我只看见他进了旧巷。",
            },
        }
    )
    valid_project["scenes"].append(
        {"scene_id": "S002", "name": "旧巷", "importance": "secondary"}
    )
    shot = valid_project["episodes"][episode_index]["shots"][0]
    shot.update(
        {
            "character_ids": ["C002"],
            "scene_id": "S002",
            "prop_ids": [],
            "food_ids": [],
            "prompt_zh": (
                "苏禾@角色_C002_苏禾_综合设定图_V001走进"
                "旧巷@场景_S002_旧巷_场景设定图_V001。"
            ),
            "prompt_en": (
                "苏禾@角色_C002_苏禾_综合设定图_V001 enters "
                "旧巷@场景_S002_旧巷_场景设定图_V001."
            ),
            "negative_prompt": "换脸，场景结构变化",
            "expression_asset_id": None,
            "action_asset_id": None,
        }
    )
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    jobs = build_media_jobs(valid_project)

    assert _job(jobs, "character_sheet", "C002")
    assert _job(jobs, "scene_sheet", "S002")
    assert not any(
        job["owner_id"] == "C002"
        and job["kind"] in {"expression_sheet", "action_sheet", "voice_sample"}
        for job in jobs
    )
    assert [
        job["owner_id"] for job in jobs if job["kind"] == "shot_sample"
    ] == ["E001_SH001"]


def test_voice_job_carries_exact_profile_text_builtin_voice_and_directions(
    valid_project: dict[str, Any],
) -> None:
    voice_asset = next(
        asset for asset in valid_project["assets"] if asset["asset_type"] == "voice_sample"
    )
    voice_asset["status"] = "confirmed"

    job = _job(build_media_jobs(valid_project), "voice_sample", "C001")
    profile = valid_project["characters"][0]["voice_profile"]

    assert job["input"]["voice_profile"] == profile
    assert job["input"]["sample_text"] == profile["sample_text"]
    assert job["input"]["built_in_voice"] == "coral"
    directions = job["input"]["delivery_instructions"]
    assert 4 <= len(directions) <= 8
    assert all(isinstance(line, str) and line.strip() for line in directions)


def test_first_episode_dialogue_is_one_audio_job_per_line_and_depends_on_voice(
    valid_project: dict[str, Any],
) -> None:
    voice_asset = next(
        asset for asset in valid_project["assets"] if asset["asset_type"] == "voice_sample"
    )
    voice_asset["status"] = "confirmed"
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": "这一次，我不会再退。"},
        {"speaker_id": "C001", "text": "剑在哪里？"},
    ]
    valid_project["episodes"][1]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": "第二集不生成。"}
    ]

    jobs = build_media_jobs(valid_project)
    voice_job = _job(jobs, "voice_sample", "C001")
    dialogue_jobs = [job for job in jobs if job["kind"] == "dialogue_audio"]

    assert [job["input"]["text"] for job in dialogue_jobs] == [
        "这一次，我不会再退。",
        "剑在哪里？",
    ]
    assert all(job["depends_on"] == [voice_job["job_id"]] for job in dialogue_jobs)
    assert all(
        job["input"]["voice_asset_id"] == "AUD_C001" for job in dialogue_jobs
    )
    assert [job["output"]["file_name"] for job in dialogue_jobs] == [
        "DIALOGUE_E001_SH001_L001_V001.wav",
        "DIALOGUE_E001_SH001_L002_V001.wav",
    ]


def test_first_episode_dialogue_uses_voice_version_active_for_episode_one(
    valid_project: dict[str, Any],
) -> None:
    voice_v1 = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "voice_sample"
    )
    voice_v1.update({"status": "confirmed", "episode_range": [1, 1]})
    voice_v2 = copy.deepcopy(voice_v1)
    voice_v2.update(
        {
            "version": 2,
            "reference_token": "@声音_AUD_C001_林岚_标准声音_V002",
            "file_name": "AUD_C001_V002.wav",
            "relative_path": "assets/audio/AUD_C001_V002.wav",
            "status": "completed",
            "episode_range": [2, 3],
        }
    )
    valid_project["assets"].append(voice_v2)
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": "这一次，我不会再退。"}
    ]

    jobs = build_media_jobs(valid_project)
    voice_job = next(
        job
        for job in jobs
        if job["kind"] == "voice_sample" and job["version"] == 1
    )
    dialogue = _job(jobs, "dialogue_audio", "E001_SH001")

    assert dialogue["reference_tokens"] == [
        "@声音_AUD_C001_林岚_标准声音_V001"
    ]
    assert dialogue["depends_on"] == [voice_job["job_id"]]


def test_minor_speaker_gets_dialogue_audio_without_voice_sample_job(
    valid_project: dict[str, Any],
) -> None:
    minor_profile = {
        "voice": "年轻小二声",
        "tone": "热情",
        "pace": "稍快",
        "sample_text": "客官，您的菜齐了。",
    }
    valid_project["characters"].append(
        {
            "character_id": "C002",
            "name": "小二",
            "importance": "minor",
            "role": "waiter",
            "appearance": "灰色短打",
            "voice_profile": minor_profile,
        }
    )
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C002", "text": "客官，莲花酥来了。"}
    ]

    jobs = build_media_jobs(valid_project)
    dialogue = _job(jobs, "dialogue_audio", "E001_SH001")

    assert not any(
        job["kind"] == "voice_sample" and job["owner_id"] == "C002"
        for job in jobs
    )
    assert dialogue["depends_on"] == []
    assert dialogue["input"]["voice_profile"] == minor_profile
    assert dialogue["input"]["text"] == "客官，莲花酥来了。"
    assert dialogue["input"]["built_in_voice"] == "coral"
    assert 4 <= len(dialogue["input"]["delivery_instructions"]) <= 8
    assert dialogue["input"]["ai_generated"] is True


def test_completed_dialogue_audio_is_not_rebuilt(
    valid_project: dict[str, Any],
) -> None:
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": "我不会再退。"}
    ]
    valid_project["assets"].append(
        {
            "asset_id": "DIALOGUE_E001_SH001_L001",
            "version": 1,
            "asset_type": "dialogue_audio",
            "owner_type": "shot",
            "owner_id": "E001_SH001",
            "reference_token": "@声音_DIALOGUE_E001_SH001_L001_对白_V001",
            "file_name": "DIALOGUE_E001_SH001_L001_V001.wav",
            "relative_path": "assets/audio/dialogue/DIALOGUE_E001_SH001_L001_V001.wav",
            "checksum": "a" * 64,
            "prompt": "我不会再退。",
            "speaker_id": "C001",
            "parent_asset_ids": ["AUD_C001"],
            "status": "completed",
        }
    )

    assert not any(
        job["kind"] == "dialogue_audio" for job in build_media_jobs(valid_project)
    )


def _dialogue_asset(version: int, status: str) -> dict[str, Any]:
    return {
        "asset_id": "DIALOGUE_E001_SH001_L001",
        "version": version,
        "asset_type": "dialogue_audio",
        "owner_type": "shot",
        "owner_id": "E001_SH001",
        "reference_token": f"@声音_DIALOGUE_E001_SH001_L001_对白_V{version:03d}",
        "file_name": f"DIALOGUE_E001_SH001_L001_V{version:03d}.wav",
        "relative_path": (
            "assets/audio/dialogue/"
            f"DIALOGUE_E001_SH001_L001_V{version:03d}.wav"
        ),
        "checksum": "a" * 64 if status == "completed" else "",
        "prompt": "我不会再退。",
        "speaker_id": "C001",
        "parent_asset_ids": ["AUD_C001"],
        "status": status,
    }


def _dialogue_fingerprint(speaker_id: str, text: str) -> str:
    payload = json.dumps(
        {"speaker_id": speaker_id, "text": text},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def test_discarded_dialogue_history_advances_to_next_unique_version(
    valid_project: dict[str, Any],
) -> None:
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": "我不会再退。"}
    ]
    valid_project["assets"].append(_dialogue_asset(1, "discarded"))

    dialogue = _job(build_media_jobs(valid_project), "dialogue_audio", "E001_SH001")

    assert dialogue["version"] == 2
    assert dialogue["job_id"] == "JOB_DIALOGUE_E001_SH001_L001_V002"
    assert dialogue["output"]["file_name"] == (
        "DIALOGUE_E001_SH001_L001_V002.wav"
    )
    assert dialogue["output"]["reference_token"].endswith("_V002")


def test_latest_incomplete_dialogue_version_is_resumed_not_incremented(
    valid_project: dict[str, Any],
) -> None:
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": "我不会再退。"}
    ]
    valid_project["assets"].extend(
        [_dialogue_asset(1, "discarded"), _dialogue_asset(2, "confirmed")]
    )

    dialogue = _job(build_media_jobs(valid_project), "dialogue_audio", "E001_SH001")

    assert dialogue["version"] == 2
    assert dialogue["output"]["file_name"].endswith("_V002.wav")


def test_latest_completed_dialogue_version_skips_even_with_older_history(
    valid_project: dict[str, Any],
) -> None:
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": "我不会再退。"}
    ]
    valid_project["assets"].extend(
        [_dialogue_asset(1, "discarded"), _dialogue_asset(2, "completed")]
    )

    assert not any(
        job["kind"] == "dialogue_audio" for job in build_media_jobs(valid_project)
    )


def test_dialogue_job_carries_stable_content_fingerprint_in_input_and_output(
    valid_project: dict[str, Any],
) -> None:
    text = "这一次，我不会再退。"
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": text}
    ]

    dialogue = _job(build_media_jobs(valid_project), "dialogue_audio", "E001_SH001")
    expected = _dialogue_fingerprint("C001", text)

    assert dialogue["input"]["content_fingerprint"] == expected
    assert dialogue["output"]["content_fingerprint"] == expected


def test_changed_dialogue_text_advances_past_completed_asset(
    valid_project: dict[str, Any],
) -> None:
    old = _dialogue_asset(1, "completed")
    old["content_fingerprint"] = _dialogue_fingerprint("C001", old["prompt"])
    valid_project["assets"].append(old)
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": "这一次，我绝不后退。"}
    ]

    dialogue = _job(build_media_jobs(valid_project), "dialogue_audio", "E001_SH001")

    assert dialogue["version"] == 2
    assert dialogue["input"]["content_fingerprint"] != old["content_fingerprint"]


def test_changed_dialogue_speaker_does_not_resume_incomplete_old_audio(
    valid_project: dict[str, Any],
) -> None:
    old = _dialogue_asset(1, "confirmed")
    old["content_fingerprint"] = _dialogue_fingerprint("C001", old["prompt"])
    valid_project["assets"].append(old)
    valid_project["characters"].append(
        {
            "character_id": "C002",
            "name": "小二",
            "importance": "cameo",
            "role": "waiter",
            "appearance": "灰色短打",
            "voice_profile": {
                "voice": "年轻男声",
                "tone": "热情",
                "pace": "稍快",
                "sample_text": "客官请慢用。",
            },
        }
    )
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C002", "text": old["prompt"]}
    ]

    dialogue = _job(build_media_jobs(valid_project), "dialogue_audio", "E001_SH001")

    assert dialogue["version"] == 2
    assert dialogue["input"]["speaker_id"] == "C002"


@pytest.mark.parametrize(
    "fingerprint",
    ["not-a-sha256", "A" * 64, "0" * 63],
)
def test_existing_dialogue_fingerprint_format_is_validated(
    valid_project: dict[str, Any], fingerprint: str
) -> None:
    old = _dialogue_asset(1, "completed")
    old["content_fingerprint"] = fingerprint
    valid_project["assets"].append(old)
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": old["prompt"]}
    ]

    with pytest.raises(MediaJobError, match="content_fingerprint"):
        build_media_jobs(valid_project)


def test_existing_dialogue_fingerprint_conflicting_metadata_is_rejected(
    valid_project: dict[str, Any],
) -> None:
    current_text = "我不会再退。"
    old = _dialogue_asset(1, "completed")
    old.update(
        {
            "speaker_id": "C999",
            "prompt": "旧台词",
            "content_fingerprint": _dialogue_fingerprint("C001", current_text),
        }
    )
    valid_project["assets"].append(old)
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": current_text}
    ]

    with pytest.raises(MediaJobError, match="metadata conflict"):
        build_media_jobs(valid_project)


def test_invalid_fingerprint_in_older_dialogue_history_is_rejected(
    valid_project: dict[str, Any],
) -> None:
    old_v1 = _dialogue_asset(1, "discarded")
    old_v1["content_fingerprint"] = "forged"
    current_v2 = _dialogue_asset(2, "completed")
    current_v2["content_fingerprint"] = _dialogue_fingerprint(
        "C001", current_v2["prompt"]
    )
    valid_project["assets"].extend([old_v1, current_v2])
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": current_v2["prompt"]}
    ]

    with pytest.raises(MediaJobError, match="content_fingerprint"):
        build_media_jobs(valid_project)


def test_sort_media_jobs_rejects_dependency_cycles() -> None:
    jobs = [
        {"job_id": "JOB_A", "kind": "character_sheet", "depends_on": ["JOB_B"]},
        {"job_id": "JOB_B", "kind": "expression_sheet", "depends_on": ["JOB_A"]},
    ]

    with pytest.raises(MediaJobError, match=r"JOB_A.*JOB_B|JOB_B.*JOB_A"):
        sort_media_jobs(jobs)


def test_build_is_deterministic_and_does_not_mutate_input(
    valid_project: dict[str, Any],
) -> None:
    _mark_required_media_pending(valid_project)
    original = copy.deepcopy(valid_project)

    first = build_media_jobs(valid_project)
    second = build_media_jobs(valid_project)

    assert first == second
    assert valid_project == original


def test_missing_assets_receive_canonical_pending_outputs(
    valid_project: dict[str, Any],
) -> None:
    valid_project["assets"] = []

    jobs = build_media_jobs(valid_project)
    character = _job(jobs, "character_sheet", "C001")
    shot = _job(jobs, "shot_sample", "E001_SH001")

    assert character["asset_id"] == "CHAR_C001"
    assert character["version"] == 1
    assert character["output"] == {
        "file_name": "CHAR_C001_V001.png",
        "relative_path": "assets/characters/CHAR_C001_V001.png",
        "reference_token": "@角色_C001_林岚_综合设定图_V001",
    }
    assert shot["input"]["sample_count"] == 1
    assert shot["status"] == "pending"


def test_write_jobs_defaults_beside_project_and_cli_writes_manifest(
    tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    project_path = tmp_path / "project.json"
    project_path.write_text(
        json.dumps(valid_project, ensure_ascii=False), encoding="utf-8"
    )

    output_path = write_jobs(project_path)
    manifest = json.loads(output_path.read_text(encoding="utf-8"))

    assert output_path == tmp_path / "media-jobs.json"
    assert manifest == {
        "schema_version": "1.0.0",
        "project_id": "PRJ001",
        "jobs": [],
    }

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(project_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert str(output_path) in result.stdout


def test_write_jobs_propagates_atomic_write_errors(
    tmp_path: Path,
    valid_project: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_path = tmp_path / "project.json"
    project_path.write_text(json.dumps(valid_project), encoding="utf-8")

    def fail_write(path: Path, data: dict[str, Any]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(media_jobs, "atomic_write_json", fail_write)

    with pytest.raises(OSError, match="disk full"):
        write_jobs(project_path)
    assert not (tmp_path / "media-jobs.json").exists()


def test_write_jobs_rejects_same_input_and_output_before_reading(tmp_path: Path) -> None:
    project_path = tmp_path / "project.json"
    original = b"{not-json-but-must-not-be-read"
    project_path.write_bytes(original)

    with pytest.raises(MediaJobError, match="same file"):
        write_jobs(project_path, project_path)

    assert project_path.read_bytes() == original


@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
def test_write_jobs_rejects_alias_of_project_file(
    tmp_path: Path,
    valid_project: dict[str, Any],
    link_kind: str,
) -> None:
    project_path = tmp_path / "project.json"
    project_path.write_text(json.dumps(valid_project), encoding="utf-8")
    original = project_path.read_bytes()
    alias = tmp_path / "media-jobs.json"
    if link_kind == "symlink":
        alias.symlink_to(project_path)
    else:
        os.link(project_path, alias)

    with pytest.raises(MediaJobError, match="same file"):
        write_jobs(project_path, alias)

    assert project_path.read_bytes() == original


@pytest.mark.parametrize(
    "relative_path",
    [
        "/tmp/CHAR_C001_V001.png",
        "../../CHAR_C001_V001.png",
        r"C:\\assets\\CHAR_C001_V001.png",
        r"\\\\server\\share\\CHAR_C001_V001.png",
        "assets/scenes/CHAR_C001_V001.png",
    ],
)
def test_scheduled_output_path_must_be_confined_to_kind_folder(
    valid_project: dict[str, Any], relative_path: str
) -> None:
    character = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character.update({"status": "confirmed", "relative_path": relative_path})

    with pytest.raises(MediaJobError, match="output"):
        build_media_jobs(valid_project)


@pytest.mark.parametrize(
    ("file_name", "relative_path"),
    [
        ("nested/CHAR_C001_V001.png", "assets/characters/nested/CHAR_C001_V001.png"),
        ("CHAR_C001_V001.png", "assets/characters/OTHER_V001.png"),
        ("CHAR_C001_V001.wav", "assets/characters/CHAR_C001_V001.wav"),
    ],
)
def test_scheduled_output_filename_and_extension_are_validated(
    valid_project: dict[str, Any], file_name: str, relative_path: str
) -> None:
    character = next(
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] == "character_sheet"
    )
    character.update(
        {
            "status": "confirmed",
            "file_name": file_name,
            "relative_path": relative_path,
        }
    )

    with pytest.raises(MediaJobError, match="output"):
        build_media_jobs(valid_project)


@pytest.mark.parametrize(
    ("collection", "bad_value"),
    [
        ("characters", "bad character"),
        ("scenes", "bad scene"),
        ("props", "bad prop"),
        ("foods", "bad food"),
        ("assets", "bad asset"),
    ],
)
def test_malformed_collection_member_is_rejected_with_json_path(
    valid_project: dict[str, Any], collection: str, bad_value: str
) -> None:
    valid_project[collection].append(bad_value)
    index = len(valid_project[collection]) - 1

    with pytest.raises(MediaJobError, match=rf"{collection}\[{index}\].*object"):
        build_media_jobs(valid_project)


def test_non_object_episode_and_shot_are_rejected_with_json_path(
    valid_project: dict[str, Any],
) -> None:
    valid_project["episodes"].append("bad episode")
    with pytest.raises(MediaJobError, match=r"episodes\[3\].*object"):
        build_media_jobs(valid_project)

    valid_project["episodes"].pop()
    valid_project["episodes"][0]["shots"].append("bad shot")
    with pytest.raises(MediaJobError, match=r"episodes\[0\]\.shots\[1\].*object"):
        build_media_jobs(valid_project)


def test_noncanonical_episode_order_is_rejected_without_sampling_e002(
    valid_project: dict[str, Any],
) -> None:
    valid_project["episodes"][0], valid_project["episodes"][1] = (
        valid_project["episodes"][1],
        valid_project["episodes"][0],
    )
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    with pytest.raises(MediaJobError, match="E001"):
        build_media_jobs(valid_project)


def test_duplicate_e001_is_rejected(valid_project: dict[str, Any]) -> None:
    duplicate = copy.deepcopy(valid_project["episodes"][0])
    valid_project["episodes"].append(duplicate)

    with pytest.raises(MediaJobError, match="E001"):
        build_media_jobs(valid_project)


@pytest.mark.parametrize("prompt_field", ["prompt_zh", "prompt_en"])
def test_shot_missing_required_active_token_is_rejected(
    valid_project: dict[str, Any], prompt_field: str
) -> None:
    shot = valid_project["episodes"][0]["shots"][0]
    shot[prompt_field] = shot[prompt_field].replace(
        "@角色_C001_林岚_综合设定图_V001", ""
    )
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    with pytest.raises(MediaJobError, match=prompt_field):
        build_media_jobs(valid_project)


def test_shot_unknown_or_wrong_version_token_is_rejected(
    valid_project: dict[str, Any],
) -> None:
    shot = valid_project["episodes"][0]["shots"][0]
    shot["prompt_en"] = shot["prompt_en"].replace(
        "@角色_C001_林岚_综合设定图_V001",
        "@角色_C001_林岚_综合设定图_V999",
    )
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    with pytest.raises(MediaJobError, match="unknown reference token|token set"):
        build_media_jobs(valid_project)


@pytest.mark.parametrize("episode_index", [1, 2])
@pytest.mark.parametrize(
    ("corruption", "expected_error"),
    [
        ("unknown", "unknown reference token"),
        ("wrong_version", "unknown reference token"),
        ("detached", "reference must immediately follow"),
        ("duplicate", "detached or duplicate reference token"),
    ],
)
def test_all_scripted_episode_prompts_reject_invalid_inline_character_references(
    valid_project: dict[str, Any],
    episode_index: int,
    corruption: str,
    expected_error: str,
) -> None:
    shot = valid_project["episodes"][episode_index]["shots"][0]
    token = "@角色_C001_林岚_综合设定图_V001"
    if corruption == "unknown":
        for field in ("prompt_zh", "prompt_en"):
            shot[field] += " @角色_C999_陌生人_综合设定图_V001"
    elif corruption == "wrong_version":
        for field in ("prompt_zh", "prompt_en"):
            shot[field] = shot[field].replace(
                token, "@角色_C001_林岚_综合设定图_V999"
            )
    elif corruption == "detached":
        for field in ("prompt_zh", "prompt_en"):
            shot[field] = shot[field].replace(f"林岚{token}", f"{token} 林岚")
    else:
        for field in ("prompt_zh", "prompt_en"):
            shot[field] += f" 游离引用 {token}"

    with pytest.raises(MediaJobError) as exc_info:
        build_media_jobs(valid_project)

    message = str(exc_info.value)
    assert f"episodes[{episode_index}].shots[0]" in message
    assert expected_error in message


@pytest.mark.parametrize("episode_index", [1, 2])
@pytest.mark.parametrize(
    ("field", "object_id", "expected_error"),
    [
        ("prop_ids", "P001", "P001 missing inline reference"),
        ("food_ids", "F001", "F001 missing inline reference"),
    ],
)
def test_all_scripted_episode_prompts_require_important_prop_and_food_references(
    valid_project: dict[str, Any],
    episode_index: int,
    field: str,
    object_id: str,
    expected_error: str,
) -> None:
    valid_project["episodes"][episode_index]["shots"][0][field] = [object_id]

    with pytest.raises(MediaJobError) as exc_info:
        build_media_jobs(valid_project)

    message = str(exc_info.value)
    assert f"episodes[{episode_index}].shots[0]" in message
    assert expected_error in message


def test_legal_three_episode_prompts_create_only_e001_shot_and_dialogue_jobs(
    valid_project: dict[str, Any],
) -> None:
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]
    for episode in valid_project["episodes"][1:3]:
        episode["shots"][0]["dialogue_lines"] = [
            {"speaker_id": "C001", "text": "这句不应进入首集声音任务。"}
        ]

    jobs = build_media_jobs(valid_project)

    assert [
        job["owner_id"] for job in jobs if job["kind"] == "shot_sample"
    ] == ["E001_SH001"]
    assert not any(
        job["kind"] in {"shot_sample", "dialogue_audio"}
        and job.get("episode_id") in {"E002", "E003"}
        for job in jobs
    )


def test_cli_reports_load_error_without_creating_manifest(tmp_path: Path) -> None:
    project_path = tmp_path / "project.json"
    project_path.write_text("{not json", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(project_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "error:" in result.stderr
    assert not (tmp_path / "media-jobs.json").exists()


@pytest.mark.parametrize("settings", [None, [], "invalid"])
def test_generation_settings_must_be_an_object(
    valid_project: dict[str, Any], settings: object
) -> None:
    valid_project["generation_settings"] = settings

    with pytest.raises(MediaJobError, match="generation_settings.*object"):
        build_media_jobs(valid_project)


def test_bool_sample_episode_count_is_rejected(valid_project: dict[str, Any]) -> None:
    valid_project["generation_settings"]["sample_episode_count"] = True

    with pytest.raises(MediaJobError, match="sample_episode_count"):
        build_media_jobs(valid_project)


def test_cli_reports_invalid_generation_settings_without_traceback(
    tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    valid_project["generation_settings"] = None
    project_path = tmp_path / "project.json"
    project_path.write_text(json.dumps(valid_project), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(project_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "generation_settings" in result.stderr
    assert "Traceback" not in result.stderr


def test_detached_inline_reference_is_rejected_by_shared_grammar(
    valid_project: dict[str, Any],
) -> None:
    shot = valid_project["episodes"][0]["shots"][0]
    for field in ("prompt_zh", "prompt_en"):
        shot[field] = shot[field].replace(
            "林岚@角色_C001_林岚_综合设定图_V001",
            "林岚并离开，@角色_C001_林岚_综合设定图_V001",
        )

    with pytest.raises(MediaJobError, match="must immediately follow"):
        build_media_jobs(valid_project)


def test_duplicate_detached_reference_chain_is_rejected_by_shared_grammar(
    valid_project: dict[str, Any],
) -> None:
    token = "@角色_C001_林岚_综合设定图_V001"
    shot = valid_project["episodes"][0]["shots"][0]
    for field in ("prompt_zh", "prompt_en"):
        shot[field] += f" 游离引用 {token}"

    with pytest.raises(MediaJobError, match="detached or duplicate"):
        build_media_jobs(valid_project)
