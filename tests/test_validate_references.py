from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from validate_references import validate_references


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "valid-project.json"
SCRIPT_PATH = (
    ROOT
    / "skills"
    / "novel-to-ai-drama-pack"
    / "scripts"
    / "validate_references.py"
)

CHAR_V1 = "@角色_C001_林岚_综合设定图_V001"
SCENE_V1 = "@场景_S001_酒楼包厢_场景设定图_V001"
PROP_V1 = "@道具_P001_青铜长剑_道具设定图_V001"
FOOD_V1 = "@食物_F001_莲花酥_食物设定图_V001"
EXPR_V1 = "@角色_C001_林岚_表情设定图_V001"
ACTION_V1 = "@角色_C001_林岚_动作设定图_V001"


@pytest.fixture
def valid_project() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def assert_has_error(errors: list[str], expected: str) -> None:
    assert any(expected in error for error in errors), errors


def shot(project: dict[str, Any], episode_index: int = 0) -> dict[str, Any]:
    return project["episodes"][episode_index]["shots"][0]


def completed_asset(
    *,
    asset_id: str,
    version: int,
    asset_type: str,
    owner_type: str,
    owner_id: str,
    token: str,
    episode_range: object | None = None,
) -> dict[str, Any]:
    asset: dict[str, Any] = {
        "asset_id": asset_id,
        "version": version,
        "asset_type": asset_type,
        "owner_type": owner_type,
        "owner_id": owner_id,
        "reference_token": token,
        "file_name": f"{asset_id}_V{version:03d}.png",
        "relative_path": f"assets/test/{asset_id}_V{version:03d}.png",
        "checksum": "a" * 64,
        "prompt": "test asset",
        "parent_asset_ids": [],
        "status": "completed",
    }
    if episode_range is not None:
        asset["episode_range"] = episode_range
    return asset


def replace_in_both(shot_data: dict[str, Any], old: str, new: str) -> None:
    for field in ("prompt_zh", "prompt_en"):
        shot_data[field] = shot_data[field].replace(old, new)


def test_valid_fixture_has_no_reference_errors(valid_project: dict[str, Any]) -> None:
    assert validate_references(valid_project) == []


def test_detached_token_before_character_name_is_rejected(
    valid_project: dict[str, Any],
) -> None:
    current = shot(valid_project)
    replace_in_both(current, f"林岚{CHAR_V1}", f"{CHAR_V1} 林岚")

    assert_has_error(
        validate_references(valid_project),
        "C001 reference must immediately follow 林岚",
    )


def test_whitespace_between_name_and_reference_is_rejected(
    valid_project: dict[str, Any],
) -> None:
    current = shot(valid_project)
    replace_in_both(current, f"林岚{CHAR_V1}", f"林岚\n{CHAR_V1}")

    errors = validate_references(valid_project)

    assert_has_error(errors, "C001 reference must immediately follow 林岚")
    assert all("\n" not in error and "\r" not in error for error in errors)


def test_missing_food_inline_reference_is_rejected(
    valid_project: dict[str, Any],
) -> None:
    current = shot(valid_project)
    replace_in_both(current, f"莲花酥{FOOD_V1}", "莲花酥")

    assert_has_error(
        validate_references(valid_project), "F001 missing inline reference"
    )


def test_unknown_reference_token_is_rejected(valid_project: dict[str, Any]) -> None:
    current = shot(valid_project)
    current["prompt_zh"] += " @角色_C999_陌生人_综合设定图_V001"
    current["prompt_en"] += " @角色_C999_陌生人_综合设定图_V001"

    assert_has_error(
        validate_references(valid_project),
        "unknown reference token: @角色_C999_陌生人_综合设定图_V001",
    )


@pytest.mark.parametrize(
    "separator",
    [
        "、",
        "/",
        "—",
        "“",
        "”",
        "‘",
        "’",
        '"',
        "'",
        "【",
        "】",
        "[",
        "]",
        "（",
        "）",
        "(",
        ")",
        "，",
        ",",
        "。",
        ".",
        "：",
        ":",
        "；",
        ";",
        "？",
        "?",
        "！",
        "!",
        "\n",
    ],
)
def test_unknown_reference_token_stops_before_any_non_token_character(
    valid_project: dict[str, Any], separator: str
) -> None:
    unknown_token = "@未知-token_123"
    trailing_narrative = "后续叙事"
    for field in ("prompt_zh", "prompt_en"):
        shot(valid_project)[field] += (
            f" {unknown_token}{separator}{trailing_narrative}"
        )

    errors = validate_references(valid_project)
    unknown_errors = [
        error for error in errors if "unknown reference token:" in error
    ]

    assert len(unknown_errors) == 2
    assert all(
        error.endswith(f"unknown reference token: {unknown_token}")
        for error in unknown_errors
    )
    assert all(trailing_narrative not in error for error in unknown_errors)


def test_registered_reference_tokens_may_contain_hyphens(
    valid_project: dict[str, Any],
) -> None:
    hyphenated = "@角色_C001_林-岚_综合设定图_V001"
    valid_project["assets"][1]["reference_token"] = hyphenated
    for episode_index in range(3):
        replace_in_both(shot(valid_project, episode_index), CHAR_V1, hyphenated)

    assert validate_references(valid_project) == []


def test_registered_token_may_be_immediately_followed_by_cjk_narrative(
    valid_project: dict[str, Any],
) -> None:
    replace_in_both(
        shot(valid_project),
        f"林岚{CHAR_V1}",
        f"林岚{CHAR_V1}推门进入",
    )

    assert validate_references(valid_project) == []


def test_cjk_word_after_registered_token_is_narrative_not_forgery(
    valid_project: dict[str, Any],
) -> None:
    replace_in_both(
        shot(valid_project),
        f"林岚{CHAR_V1}",
        f"林岚{CHAR_V1}伪造",
    )

    assert validate_references(valid_project) == []


@pytest.mark.parametrize("suffix", ["FORGED", "123", "_FORGED", "-FORGED"])
def test_registered_token_prefix_with_allowed_suffix_is_unknown(
    valid_project: dict[str, Any], suffix: str
) -> None:
    forged = f"{CHAR_V1}{suffix}"
    replace_in_both(shot(valid_project), CHAR_V1, forged)

    errors = validate_references(valid_project)

    assert_has_error(errors, f"unknown reference token: {forged}")


def test_valid_inline_reference_plus_detached_duplicate_is_rejected(
    valid_project: dict[str, Any],
) -> None:
    for field in ("prompt_zh", "prompt_en"):
        shot(valid_project)[field] += f" 游离引用 {CHAR_V1}"

    assert_has_error(
        validate_references(valid_project),
        f"detached or duplicate reference token: {CHAR_V1}",
    )


@pytest.mark.parametrize("duplicate_token", [EXPR_V1, ACTION_V1])
def test_duplicate_expression_or_action_occurrence_is_rejected(
    valid_project: dict[str, Any], duplicate_token: str
) -> None:
    current = shot(valid_project)
    current["expression_asset_id"] = "EXPR_C001"
    current["action_asset_id"] = "ACTION_C001"
    replace_in_both(
        current,
        f"林岚{CHAR_V1}",
        f"林岚{CHAR_V1}{EXPR_V1}{ACTION_V1}",
    )
    for field in ("prompt_zh", "prompt_en"):
        current[field] += f" 重复 {duplicate_token}"

    assert_has_error(
        validate_references(valid_project),
        f"detached or duplicate reference token: {duplicate_token}",
    )


def test_two_distinct_character_chains_are_both_valid(
    valid_project: dict[str, Any],
) -> None:
    valid_project["characters"].append(
        {
            "character_id": "C002",
            "name": "沈砺",
            "importance": "major",
            "role": "ally",
            "appearance": "黑衣持刀",
            "voice_profile": {
                "voice": "低沉男声",
                "tone": "冷静",
                "pace": "中速",
                "sample_text": "跟我走。",
            },
        }
    )
    char_c002 = "@角色_C002_沈砺_综合设定图_V001"
    valid_project["assets"].append(
        completed_asset(
            asset_id="CHAR_C002",
            version=1,
            asset_type="character_sheet",
            owner_type="character",
            owner_id="C002",
            token=char_c002,
        )
    )
    current = shot(valid_project)
    current["character_ids"].append("C002")
    for field in ("prompt_zh", "prompt_en"):
        current[field] += f" 沈砺{char_c002}走进包厢"

    assert validate_references(valid_project) == []


def test_chinese_and_english_token_sets_must_match(
    valid_project: dict[str, Any],
) -> None:
    current = shot(valid_project)
    current["prompt_en"] = current["prompt_en"].replace(f"莲花酥{FOOD_V1}", "莲花酥")

    assert_has_error(
        validate_references(valid_project),
        "prompt_zh and prompt_en reference token sets differ",
    )


def test_registered_old_character_version_does_not_satisfy_active_reference(
    valid_project: dict[str, Any],
) -> None:
    char_v2 = "@角色_C001_林岚_综合设定图_V002"
    valid_project["assets"].append(
        completed_asset(
            asset_id="CHAR_C001",
            version=2,
            asset_type="character_sheet",
            owner_type="character",
            owner_id="C001",
            token=char_v2,
        )
    )

    assert_has_error(
        validate_references(valid_project),
        f"C001 active reference must be {char_v2}",
    )


def test_episode_ranged_version_is_active_only_within_inclusive_boundaries(
    valid_project: dict[str, Any],
) -> None:
    char_v2 = "@角色_C001_林岚_综合设定图_V002"
    valid_project["assets"].append(
        completed_asset(
            asset_id="CHAR_C001",
            version=2,
            asset_type="character_sheet",
            owner_type="character",
            owner_id="C001",
            token=char_v2,
            episode_range=[2, 3],
        )
    )
    for episode_index in (1, 2):
        replace_in_both(shot(valid_project, episode_index), CHAR_V1, char_v2)

    assert validate_references(valid_project) == []


def test_ranged_character_stage_takes_precedence_over_newer_global_stage(
    valid_project: dict[str, Any],
) -> None:
    ranged = valid_project["assets"][1]
    ranged["episode_range"] = [1, 1]
    global_v2 = completed_asset(
        asset_id="CHAR_C001",
        version=2,
        asset_type="character_sheet",
        owner_type="character",
        owner_id="C001",
        token="@角色_C001_林岚_综合设定图_V002",
    )
    valid_project["assets"].append(global_v2)
    valid_project["episodes"] = [valid_project["episodes"][0]]

    assert validate_references(valid_project) == []

    replace_in_both(
        shot(valid_project),
        CHAR_V1,
        "@角色_C001_林岚_综合设定图_V002",
    )

    assert_has_error(
        validate_references(valid_project),
        f"C001 active reference must be {CHAR_V1}",
    )


def test_overlapping_ranged_character_stages_report_ambiguity(
    valid_project: dict[str, Any],
) -> None:
    valid_project["assets"][1]["episode_range"] = [1, 2]
    valid_project["assets"].append(
        completed_asset(
            asset_id="CHAR_C001",
            version=2,
            asset_type="character_sheet",
            owner_type="character",
            owner_id="C001",
            token="@角色_C001_林岚_综合设定图_V002",
            episode_range=[1, 3],
        )
    )
    valid_project["episodes"] = [valid_project["episodes"][0]]

    assert_has_error(validate_references(valid_project), "ambiguous character_sheet")


@pytest.mark.parametrize(
    "episode_range",
    ["2-3", [2], [3, 2], [True, 3], [2, "3"]],
)
def test_malformed_episode_range_reports_an_error_without_raising(
    valid_project: dict[str, Any], episode_range: object
) -> None:
    valid_project["assets"][1]["episode_range"] = episode_range

    assert_has_error(
        validate_references(valid_project),
        "assets[1].episode_range must be [start, end] positive integers",
    )


def test_expression_and_action_chain_is_valid_when_exact(
    valid_project: dict[str, Any],
) -> None:
    current = shot(valid_project)
    current["expression_asset_id"] = "EXPR_C001"
    current["action_asset_id"] = "ACTION_C001"
    replace_in_both(current, f"林岚{CHAR_V1}", f"林岚{CHAR_V1}{EXPR_V1}{ACTION_V1}")

    assert validate_references(valid_project) == []


@pytest.mark.parametrize(
    "replacement",
    [
        f"林岚{CHAR_V1}{ACTION_V1}{EXPR_V1}",
        f"林岚{CHAR_V1} {EXPR_V1}{ACTION_V1}",
    ],
)
def test_reversed_or_detached_expression_action_chain_is_rejected(
    valid_project: dict[str, Any], replacement: str
) -> None:
    current = shot(valid_project)
    current["expression_asset_id"] = "EXPR_C001"
    current["action_asset_id"] = "ACTION_C001"
    replace_in_both(current, f"林岚{CHAR_V1}", replacement)

    assert_has_error(
        validate_references(valid_project), "C001 reference chain must be exactly"
    )


def test_character_chain_cannot_use_another_characters_selected_asset(
    valid_project: dict[str, Any],
) -> None:
    valid_project["characters"].append(
        {
            "character_id": "C002",
            "name": "沈砺",
            "importance": "major",
            "role": "ally",
            "appearance": "黑衣持刀",
            "voice_profile": {
                "voice": "低沉男声",
                "tone": "冷静",
                "pace": "中速",
                "sample_text": "跟我走。",
            },
        }
    )
    other_expr = "@角色_C002_沈砺_表情设定图_V001"
    valid_project["assets"].append(
        completed_asset(
            asset_id="EXPR_C002",
            version=1,
            asset_type="expression_sheet",
            owner_type="character",
            owner_id="C002",
            token=other_expr,
        )
    )
    current = shot(valid_project)
    current["expression_asset_id"] = "EXPR_C002"
    replace_in_both(current, f"林岚{CHAR_V1}", f"林岚{CHAR_V1}{other_expr}")

    assert_has_error(
        validate_references(valid_project),
        "C001 reference chain cannot use asset for C002",
    )


def test_extra_unrelated_registered_token_is_rejected(
    valid_project: dict[str, Any],
) -> None:
    for field in ("prompt_zh", "prompt_en"):
        shot(valid_project)[field] += f" 额外{EXPR_V1}"

    assert_has_error(
        validate_references(valid_project), f"unrelated reference token: {EXPR_V1}"
    )


def test_multiple_characters_each_require_their_own_chain(
    valid_project: dict[str, Any],
) -> None:
    valid_project["characters"].append(
        {
            "character_id": "C002",
            "name": "沈砺",
            "importance": "major",
            "role": "ally",
            "appearance": "黑衣持刀",
            "voice_profile": {
                "voice": "低沉男声",
                "tone": "冷静",
                "pace": "中速",
                "sample_text": "跟我走。",
            },
        }
    )
    char_c002 = "@角色_C002_沈砺_综合设定图_V001"
    valid_project["assets"].append(
        completed_asset(
            asset_id="CHAR_C002",
            version=1,
            asset_type="character_sheet",
            owner_type="character",
            owner_id="C002",
            token=char_c002,
        )
    )
    current = shot(valid_project)
    current["character_ids"].append("C002")
    for field in ("prompt_zh", "prompt_en"):
        current[field] += " 沈砺"

    errors = validate_references(valid_project)

    assert_has_error(errors, "C002 missing inline reference")
    assert not any("C001" in error for error in errors)


def test_ordinary_unregistered_object_needs_no_reference(
    valid_project: dict[str, Any],
) -> None:
    for field in ("prompt_zh", "prompt_en"):
        shot(valid_project)[field] += " 桌上有一只普通茶杯"

    assert validate_references(valid_project) == []


def test_malformed_project_structures_return_errors_instead_of_raising() -> None:
    errors = validate_references(
        {
            "characters": "bad",
            "scenes": None,
            "props": {},
            "foods": [],
            "assets": [None],
            "episodes": [{"episode_number": "one", "shots": [None]}],
        }
    )

    assert errors
    assert all(isinstance(error, str) for error in errors)


def test_cli_prints_valid_for_valid_project() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(FIXTURE_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "VALID\n"
    assert result.stderr == ""


def test_cli_prints_each_validation_error_on_its_own_line(
    valid_project: dict[str, Any], tmp_path: Path
) -> None:
    current = shot(valid_project)
    replace_in_both(current, f"林岚{CHAR_V1}", "林岚")
    project_path = tmp_path / "invalid.json"
    project_path.write_text(json.dumps(valid_project, ensure_ascii=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(project_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "C001 missing inline reference" in result.stdout
    assert result.stderr == ""
    assert all(line for line in result.stdout.splitlines())


def test_cli_reports_invalid_json_without_traceback(tmp_path: Path) -> None:
    project_path = tmp_path / "invalid.json"
    project_path.write_text("{not json", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), str(project_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "invalid project JSON" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""
