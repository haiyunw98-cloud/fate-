from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from validate_project import validate_project


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "valid-project.json"
SCRIPT_PATH = (
    ROOT / "skills" / "novel-to-ai-drama-pack" / "scripts" / "validate_project.py"
)


@pytest.fixture
def valid_project() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def assert_has_error(errors: list[str], expected: str) -> None:
    assert any(expected in error for error in errors), errors


def test_valid_project_has_no_errors(valid_project: dict[str, Any]) -> None:
    assert validate_project(valid_project) == []


def test_reports_sorted_missing_and_unexpected_top_level_keys(
    valid_project: dict[str, Any],
) -> None:
    del valid_project["source"]
    del valid_project["analysis"]
    valid_project["zeta"] = None
    valid_project["alpha"] = None

    assert validate_project(valid_project) == [
        "missing top-level key: analysis",
        "missing top-level key: source",
        "unexpected top-level key: alpha",
        "unexpected top-level key: zeta",
    ]


def test_duplicate_object_id_is_rejected(valid_project: dict[str, Any]) -> None:
    duplicate = copy.deepcopy(valid_project["characters"][0])
    duplicate["name"] = "另一个角色"
    valid_project["characters"].append(duplicate)

    assert_has_error(validate_project(valid_project), "duplicate object id: C001")


def test_asset_filename_version_must_match(valid_project: dict[str, Any]) -> None:
    valid_project["assets"][0]["file_name"] = "STYLE_PRJ001_V002.png"

    assert_has_error(
        validate_project(valid_project), "asset version does not match file name"
    )


def test_bool_is_not_a_valid_asset_version(valid_project: dict[str, Any]) -> None:
    valid_project["assets"][0]["version"] = True

    assert_has_error(
        validate_project(valid_project),
        "assets[0].version must be a positive integer",
    )


def test_duplicate_asset_key_and_reference_token_are_rejected(
    valid_project: dict[str, Any],
) -> None:
    duplicate = copy.deepcopy(valid_project["assets"][0])
    valid_project["assets"].append(duplicate)

    errors = validate_project(valid_project)

    assert_has_error(errors, "duplicate asset (asset_id, version): STYLE_PRJ001, 1")
    assert_has_error(
        errors,
        "duplicate asset reference_token: @项目_PRJ001_测试短剧_风格参考图_V001",
    )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("character_ids", ["C999"], "unknown character reference: C999"),
        ("scene_id", "S999", "unknown scene reference: S999"),
        ("prop_ids", ["P999"], "unknown prop reference: P999"),
        ("food_ids", ["F999"], "unknown food reference: F999"),
    ],
)
def test_broken_shot_references_are_rejected(
    valid_project: dict[str, Any], field: str, value: Any, expected: str
) -> None:
    valid_project["episodes"][0]["shots"][0][field] = value

    assert_has_error(validate_project(valid_project), expected)


def test_asset_owner_type_must_match_asset_type(valid_project: dict[str, Any]) -> None:
    valid_project["assets"][1]["owner_type"] = "scene"

    assert_has_error(
        validate_project(valid_project),
        "assets[1].owner_type must be character for character_sheet",
    )


def test_asset_owner_id_must_resolve(valid_project: dict[str, Any]) -> None:
    valid_project["assets"][1]["owner_id"] = "C999"

    assert_has_error(validate_project(valid_project), "unknown character owner_id: C999")


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("checksum", "ABC", "checksum must be 64 lowercase hexadecimal characters"),
        ("file_name", "STYLE_PRJ001_V001.wav", "file extension is invalid for style_reference"),
        ("relative_path", "", "relative_path must be nonempty when completed"),
        ("prompt", "", "prompt must be nonempty when completed"),
    ],
)
def test_completed_asset_requires_valid_output_metadata(
    valid_project: dict[str, Any], field: str, value: str, expected: str
) -> None:
    valid_project["assets"][0][field] = value

    assert_has_error(validate_project(valid_project), expected)


@pytest.mark.parametrize(
    ("asset_type", "owner_id", "expected"),
    [
        ("character_sheet", "C001", "missing completed character_sheet for C001"),
        ("expression_sheet", "C001", "missing completed expression_sheet for C001"),
        ("action_sheet", "C001", "missing completed action_sheet for C001"),
        ("voice_sample", "C001", "missing completed voice_sample for C001"),
        ("scene_sheet", "S001", "missing completed scene_sheet for S001"),
        ("prop_sheet", "P001", "missing completed prop_sheet for P001"),
        ("food_image", "F001", "missing completed food_image for F001"),
        ("shot_sample", "E001_SH001", "missing completed shot_sample for E001_SH001"),
    ],
)
def test_completed_project_requires_core_media(
    valid_project: dict[str, Any], asset_type: str, owner_id: str, expected: str
) -> None:
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if not (asset["asset_type"] == asset_type and asset["owner_id"] == owner_id)
    ]

    assert_has_error(validate_project(valid_project), expected)


def test_completed_project_requires_project_style_reference(
    valid_project: dict[str, Any],
) -> None:
    valid_project["assets"] = [
        asset
        for asset in valid_project["assets"]
        if asset["asset_type"] != "style_reference"
    ]

    assert_has_error(
        validate_project(valid_project),
        "missing completed style_reference for PRJ001",
    )


def test_only_sample_episodes_require_shot_samples(valid_project: dict[str, Any]) -> None:
    assert not any(
        asset["asset_type"] == "shot_sample"
        and asset["owner_id"] in {"E002_SH001", "E003_SH001"}
        for asset in valid_project["assets"]
    )

    assert validate_project(valid_project) == []


def test_target_episode_count_must_equal_episode_count(
    valid_project: dict[str, Any],
) -> None:
    valid_project["project"]["target_episode_count"] = 4

    assert_has_error(
        validate_project(valid_project),
        "project.target_episode_count must equal episodes count",
    )


def test_completed_project_may_have_episodes_beyond_script_scope(
    valid_project: dict[str, Any],
) -> None:
    episode = copy.deepcopy(valid_project["episodes"][2])
    episode["episode_id"] = "E004"
    episode["episode_number"] = 4
    episode["title"] = "后续待写"
    episode["script"] = ""
    episode["shots"][0]["shot_id"] = "E004_SH001"
    episode["shots"][0]["prompt_zh"] = ""
    episode["shots"][0]["prompt_en"] = ""
    episode["shots"][0]["negative_prompt"] = ""
    valid_project["episodes"].append(episode)
    valid_project["project"]["target_episode_count"] = 4

    assert validate_project(valid_project) == []


def test_script_episode_count_cannot_exceed_total_episodes(
    valid_project: dict[str, Any],
) -> None:
    valid_project["generation_settings"]["script_episode_count"] = 4

    assert_has_error(
        validate_project(valid_project),
        "generation_settings.script_episode_count cannot exceed episodes count",
    )


def test_production_requires_episodes_through_script_count(
    valid_project: dict[str, Any],
) -> None:
    valid_project["project"]["status"] = "production"
    valid_project["project"]["target_episode_count"] = 2
    valid_project["episodes"].pop()

    assert_has_error(
        validate_project(valid_project),
        "generation_settings.script_episode_count cannot exceed episodes count",
    )


@pytest.mark.parametrize(
    ("episode_index", "field", "value", "expected"),
    [
        (1, "episode_number", 3, "episodes[1].episode_number must be 2"),
        (1, "episode_id", "E003", "episodes[1].episode_id must be E002"),
        (2, "episode_id", "E002", "duplicate episode id: E002"),
    ],
)
def test_episode_numbers_and_ids_are_ordered_and_unique(
    valid_project: dict[str, Any],
    episode_index: int,
    field: str,
    value: Any,
    expected: str,
) -> None:
    valid_project["episodes"][episode_index][field] = value

    assert_has_error(validate_project(valid_project), expected)


def test_shot_ids_must_match_episode_and_be_unique(valid_project: dict[str, Any]) -> None:
    duplicate = copy.deepcopy(valid_project["episodes"][0]["shots"][0])
    valid_project["episodes"][1]["shots"].append(duplicate)

    errors = validate_project(valid_project)

    assert_has_error(errors, "duplicate shot id: E001_SH001")
    assert_has_error(errors, "shot id E001_SH001 does not belong to E002")


def test_malformed_nested_values_return_errors_instead_of_raising(
    valid_project: dict[str, Any],
) -> None:
    valid_project["project"] = None
    valid_project["characters"] = [None]
    valid_project["episodes"] = "bad"
    valid_project["assets"] = [None]

    errors = validate_project(valid_project)

    assert_has_error(errors, "project must be an object")
    assert_has_error(errors, "characters[0] must be an object")
    assert_has_error(errors, "episodes must be a list")
    assert_has_error(errors, "assets[0] must be an object")


def test_non_string_top_level_key_is_reported_without_raising(
    valid_project: dict[str, Any],
) -> None:
    valid_project[9] = "unexpected"
    valid_project["zeta"] = "unexpected"

    assert_has_error(validate_project(valid_project), "unexpected top-level key: 9")


def test_unhashable_nested_scalars_return_errors_without_raising(
    valid_project: dict[str, Any],
) -> None:
    valid_project["project"]["status"] = []
    valid_project["characters"][0]["importance"] = {}
    valid_project["assets"][0]["asset_type"] = []
    valid_project["assets"][1]["status"] = {}

    errors = validate_project(valid_project)

    assert_has_error(errors, "project.status is not allowed")
    assert_has_error(errors, "characters[0].importance must be a nonempty string")
    assert_has_error(errors, "assets[0].asset_type must be a nonempty string")
    assert_has_error(errors, "assets[1].status is not allowed")


def test_owner_id_must_be_exactly_delimited_in_file_name_and_token(
    valid_project: dict[str, Any],
) -> None:
    asset = valid_project["assets"][1]
    asset["file_name"] = "X_C0010_V001.png"
    asset["reference_token"] = "@角色_C0010_林岚_综合设定图_V001"

    errors = validate_project(valid_project)

    assert_has_error(errors, "assets[1].file_name must include owner_id C001")
    assert_has_error(errors, "assets[1].reference_token must include owner_id C001")


def test_existing_delimited_owner_id_patterns_remain_valid(
    valid_project: dict[str, Any],
) -> None:
    character_asset = valid_project["assets"][1]

    assert character_asset["file_name"] == "CHAR_C001_V001.png"
    assert character_asset["reference_token"] == "@角色_C001_林岚_综合设定图_V001"
    assert validate_project(valid_project) == []


def test_cli_prints_valid_and_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(FIXTURE_PATH)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout == "VALID\n"
    assert result.stderr == ""


def test_cli_prints_validation_errors_one_per_line(
    valid_project: dict[str, Any], tmp_path: Path
) -> None:
    valid_project["schema_version"] = "2.0.0"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(valid_project, ensure_ascii=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert result.stdout.splitlines() == ["schema_version must be 1.0.0"]
    assert result.stderr == ""


@pytest.mark.parametrize("content", ["[1, 2]", "{not json"])
def test_cli_reports_invalid_json_or_root_without_traceback(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert len(result.stderr.splitlines()) == 1
    assert "Traceback" not in result.stderr
