from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import build_media_jobs as media_jobs
from build_media_jobs import MediaJobError, build_media_jobs, sort_media_jobs, write_jobs


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


def test_completed_project_needs_no_media_jobs(
    valid_project: dict[str, Any],
) -> None:
    assert build_media_jobs(valid_project) == []


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
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    job = _job(build_media_jobs(valid_project), "shot_sample", "E001_SH001")

    assert "@角色_C001_林岚_综合设定图_V001" in job["reference_tokens"]
    assert "@角色_C001_林岚_综合设定图_V002" not in job["reference_tokens"]


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
        "parent_asset_ids": ["AUD_C001"],
        "status": status,
    }


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
