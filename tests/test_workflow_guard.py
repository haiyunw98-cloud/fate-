from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from build_media_jobs import build_media_jobs
from workflow_guard import (
    WorkflowGuardError,
    create_redo_asset,
    resumable_jobs,
    transition_asset,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "valid-project.json"


@pytest.fixture
def valid_project() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _asset(data: dict[str, Any], asset_id: str, version: int = 1) -> dict[str, Any]:
    return next(
        item
        for item in data["assets"]
        if item["asset_id"] == asset_id and item["version"] == version
    )


def _replace_version(value: str, old_version: int, new_version: int) -> str:
    return value.replace(f"_V{old_version:03d}", f"_V{new_version:03d}")


@pytest.mark.parametrize(
    ("current", "target"),
    [
        ("draft", "confirmed"),
        ("draft", "discarded"),
        ("confirmed", "generating"),
        ("confirmed", "discarded"),
        ("generating", "completed"),
        ("generating", "redo"),
        ("redo", "confirmed"),
        ("redo", "discarded"),
    ],
)
def test_legal_asset_transitions_return_deep_copy(
    current: str, target: str
) -> None:
    asset = {"status": current, "metadata": {"tags": ["keep"]}}

    updated = transition_asset(asset, target)

    assert updated == {"status": target, "metadata": {"tags": ["keep"]}}
    assert updated is not asset
    updated["metadata"]["tags"].append("new")
    assert asset == {"status": current, "metadata": {"tags": ["keep"]}}


def test_completed_asset_requires_new_version() -> None:
    with pytest.raises(
        WorkflowGuardError,
        match="completed assets are immutable; create a new version",
    ):
        transition_asset({"status": "completed"}, "generating")


def test_discarded_asset_is_terminal() -> None:
    with pytest.raises(WorkflowGuardError, match="discarded assets are terminal"):
        transition_asset({"status": "discarded"}, "confirmed")


@pytest.mark.parametrize(
    ("asset", "target", "message"),
    [
        ({}, "confirmed", "asset.status must be a known status"),
        ({"status": "mystery"}, "confirmed", "unknown current asset status"),
        ({"status": "draft"}, "mystery", "unknown target asset status"),
        ({"status": "draft"}, "generating", "illegal asset transition"),
    ],
)
def test_transition_rejects_unknown_or_illegal_statuses(
    asset: dict[str, Any], target: str, message: str
) -> None:
    with pytest.raises(WorkflowGuardError, match=message):
        transition_asset(asset, target)


def test_resume_returns_only_pending_or_redo_jobs_as_copies() -> None:
    jobs = [
        {"job_id": "A", "status": "completed", "input": {"x": 1}},
        {"job_id": "B", "status": "pending", "input": {"x": 2}},
        {"job_id": "C", "status": "generating", "input": {"x": 3}},
        {"job_id": "D", "status": "redo", "input": {"x": 4}},
    ]
    before = copy.deepcopy(jobs)

    resumable = resumable_jobs(jobs)

    assert [job["job_id"] for job in resumable] == ["B", "D"]
    assert jobs == before
    resumable[0]["input"]["x"] = 99
    assert jobs == before


@pytest.mark.parametrize(
    ("jobs", "message"),
    [
        ({"job_id": "A"}, "jobs must be a list"),
        (["bad"], r"jobs\[0\] must be an object"),
        ([{"status": "pending"}], r"jobs\[0\].job_id must be a nonempty string"),
        ([{"job_id": "A"}], r"jobs\[0\].status must be a known status"),
        (
            [{"job_id": "A", "status": "unknown"}],
            r"jobs\[0\] has unknown status",
        ),
        (
            [
                {"job_id": "A", "status": "pending"},
                {"job_id": "A", "status": "redo"},
            ],
            "duplicate media job id",
        ),
    ],
)
def test_resume_rejects_malformed_jobs(
    jobs: object, message: str
) -> None:
    with pytest.raises(WorkflowGuardError, match=message):
        resumable_jobs(jobs)  # type: ignore[arg-type]


def test_redo_v001_to_v002_preserves_metadata_and_clears_output(
    valid_project: dict[str, Any],
) -> None:
    original = _asset(valid_project, "CHAR_C001")
    original.update(
        {
            "parent_asset_ids": ["STYLE_PRJ001"],
            "absolute_path": "/tmp/CHAR_C001_V001.png",
            "file_path": "/tmp/CHAR_C001_V001.png",
            "output_path": "/tmp/CHAR_C001_V001.png",
            "output_url": "https://example.invalid/CHAR_C001_V001.png",
            "episode_range": [1, 3],
            "ai_generated": True,
            "ai_disclosure": "AI generated image",
            "stage_metadata": {"costume": "blue"},
            "completed_at": "2026-07-18T08:00:00Z",
            "provider_output": {"request_id": "old"},
            "provider_job_id": "job-old",
            "failure_reason": "old failure",
            "width": 1024,
            "height": 1536,
            "duration_seconds": 3.5,
            "negative_prompt": "bad hands",
            "content_fingerprint": "a" * 64,
            "speaker_id": "C001",
            "text": "stable dialogue identity",
            "stage": "opening",
            "unknown_provider_blob": {"must": "drop"},
        }
    )
    before = copy.deepcopy(valid_project)

    updated = create_redo_asset(valid_project, "CHAR_C001", "face drift")

    assert valid_project == before
    assert len(updated["assets"]) == len(before["assets"]) + 1
    assert _asset(updated, "CHAR_C001", 1) == original
    redo = _asset(updated, "CHAR_C001", 2)
    assert redo["asset_id"] == "CHAR_C001"
    assert redo["version"] == 2
    assert redo["asset_type"] == "character_sheet"
    assert redo["owner_type"] == "character"
    assert redo["owner_id"] == "C001"
    assert redo["episode_range"] == [1, 3]
    assert redo["ai_generated"] is True
    assert redo["ai_disclosure"] == "AI generated image"
    assert redo["stage_metadata"] == {"costume": "blue"}
    assert redo["prompt"] == original["prompt"]
    assert redo["status"] == "redo"
    assert redo["parent_asset_ids"] == ["STYLE_PRJ001"]
    assert redo["redo_parent"] == {"asset_id": "CHAR_C001", "version": 1}
    assert redo["negative_prompt"] == "bad hands"
    assert redo["content_fingerprint"] == "a" * 64
    assert redo["speaker_id"] == "C001"
    assert redo["text"] == "stable dialogue identity"
    assert redo["stage"] == "opening"
    assert redo["file_name"] == "CHAR_C001_V002.png"
    assert redo["reference_token"] == "@角色_C001_林岚_综合设定图_V002"
    for field in (
        "relative_path",
        "absolute_path",
        "file_path",
        "output_path",
        "output_url",
        "checksum",
        "completed_at",
        "provider_output",
        "provider_job_id",
        "failure_reason",
        "width",
        "height",
        "duration_seconds",
        "unknown_provider_blob",
    ):
        assert field not in redo


def test_redo_v009_to_v010_uses_contiguous_history(
    valid_project: dict[str, Any],
) -> None:
    original = _asset(valid_project, "CHAR_C001")
    history = []
    for version in range(1, 10):
        item = copy.deepcopy(original)
        item["version"] = version
        item["file_name"] = _replace_version(
            original["file_name"], 1, version
        )
        item["reference_token"] = _replace_version(
            original["reference_token"], 1, version
        )
        history.append(item)
    valid_project["assets"] = [
        asset for asset in valid_project["assets"] if asset["asset_id"] != "CHAR_C001"
    ] + history

    updated = create_redo_asset(valid_project, "CHAR_C001", "new direction")

    redo = _asset(updated, "CHAR_C001", 10)
    assert redo["file_name"] == "CHAR_C001_V010.png"
    assert redo["reference_token"].endswith("_V010")


def test_redo_appends_unique_run_with_exact_reason_and_old_prompt(
    valid_project: dict[str, Any],
) -> None:
    first = create_redo_asset(valid_project, "CHAR_C001", "  exact reason  ")
    second = create_redo_asset(first, "CHAR_C001", "second reason")

    first_run, second_run = second["generation_runs"]
    assert first_run["run_id"] != second_run["run_id"]
    assert first_run["operation"] == "redo_asset"
    assert first_run["asset_id"] == "CHAR_C001"
    assert first_run["from_version"] == 1
    assert first_run["to_version"] == 2
    assert first_run["reason"] == "  exact reason  "
    assert first_run["old_prompt"] == _asset(
        valid_project, "CHAR_C001"
    )["prompt"]
    assert first_run["status"] == "pending"
    assert isinstance(first_run["created_at"], str) and first_run["created_at"]
    assert _asset(second, "CHAR_C001", 2)["redo_parent"] == {
        "asset_id": "CHAR_C001",
        "version": 1,
    }
    assert _asset(second, "CHAR_C001", 3)["redo_parent"] == {
        "asset_id": "CHAR_C001",
        "version": 2,
    }


@pytest.mark.parametrize("reason", ["", "   ", None, 3])
def test_redo_requires_nonempty_string_reason(
    valid_project: dict[str, Any], reason: object
) -> None:
    with pytest.raises(WorkflowGuardError, match="reason must be a nonempty string"):
        create_redo_asset(valid_project, "CHAR_C001", reason)  # type: ignore[arg-type]


def test_redo_does_not_modify_unrelated_assets_or_episodes(
    valid_project: dict[str, Any],
) -> None:
    unrelated_before = [
        copy.deepcopy(asset)
        for asset in valid_project["assets"]
        if asset["asset_id"] != "CHAR_C001"
    ]
    episodes_before = copy.deepcopy(valid_project["episodes"])

    updated = create_redo_asset(valid_project, "CHAR_C001", "retry")

    assert [
        asset for asset in updated["assets"] if asset["asset_id"] != "CHAR_C001"
    ] == unrelated_before
    assert updated["episodes"] == episodes_before


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda data: data["assets"].append(
                copy.deepcopy(_asset(data, "CHAR_C001"))
            ),
            "duplicate version",
        ),
        (
            lambda data: data["assets"].append(
                {
                    **copy.deepcopy(_asset(data, "CHAR_C001")),
                    "version": 3,
                    "file_name": "CHAR_C001_V003.png",
                    "reference_token": "@角色_C001_林岚_综合设定图_V003",
                }
            ),
            "version history.*contiguous",
        ),
        (
            lambda data: data["assets"].append(
                {
                    **copy.deepcopy(_asset(data, "CHAR_C001")),
                    "version": 2,
                    "asset_type": "expression_sheet",
                    "file_name": "CHAR_C001_V002.png",
                    "reference_token": "@角色_C001_林岚_综合设定图_V002",
                }
            ),
            "inconsistent asset_type",
        ),
        (
            lambda data: data["assets"].append(
                {
                    **copy.deepcopy(_asset(data, "CHAR_C001")),
                    "version": 2,
                    "owner_id": "C999",
                    "file_name": "CHAR_C001_V002.png",
                    "reference_token": "@角_C999_林岚_综合设定图_V002",
                }
            ),
            "inconsistent owner_id",
        ),
    ],
)
def test_redo_rejects_ambiguous_or_malformed_history(
    valid_project: dict[str, Any], mutator: Any, message: str
) -> None:
    mutator(valid_project)

    with pytest.raises(WorkflowGuardError, match=message):
        create_redo_asset(valid_project, "CHAR_C001", "retry")


def test_redo_rejects_missing_assets_collection_or_item(
    valid_project: dict[str, Any],
) -> None:
    with pytest.raises(WorkflowGuardError, match="assets must be a list"):
        create_redo_asset({**valid_project, "assets": None}, "CHAR_C001", "retry")
    with pytest.raises(WorkflowGuardError, match="asset_id not found"):
        create_redo_asset(valid_project, "CHAR_C999", "retry")
    valid_project["assets"][0] = "bad"
    with pytest.raises(WorkflowGuardError, match=r"assets\[0\] must be an object"):
        create_redo_asset(valid_project, "CHAR_C001", "retry")


def test_redo_rejects_discarded_latest_version(
    valid_project: dict[str, Any],
) -> None:
    _asset(valid_project, "CHAR_C001")["status"] = "discarded"

    with pytest.raises(WorkflowGuardError, match="latest asset version is discarded"):
        create_redo_asset(valid_project, "CHAR_C001", "retry")


def test_redo_rejects_latest_version_without_reusable_prompt(
    valid_project: dict[str, Any],
) -> None:
    _asset(valid_project, "CHAR_C001")["prompt"] = "   "

    with pytest.raises(WorkflowGuardError, match="latest asset prompt"):
        create_redo_asset(valid_project, "CHAR_C001", "retry")


@pytest.mark.parametrize("field", ["file_name", "reference_token"])
def test_redo_rejects_names_that_cannot_be_safely_versioned(
    valid_project: dict[str, Any], field: str
) -> None:
    _asset(valid_project, "CHAR_C001")[field] = "unsafe-name"

    with pytest.raises(WorkflowGuardError, match=field):
        create_redo_asset(valid_project, "CHAR_C001", "retry")


@pytest.mark.parametrize("field", ["file_name", "reference_token"])
def test_redo_rejects_version_mismatch_in_older_history(
    valid_project: dict[str, Any], field: str
) -> None:
    original = _asset(valid_project, "CHAR_C001")
    second = copy.deepcopy(original)
    second.update(
        {
            "version": 2,
            "file_name": "CHAR_C001_V002.png",
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
        }
    )
    original[field] = _replace_version(original[field], 1, 999)
    valid_project["assets"].append(second)

    with pytest.raises(WorkflowGuardError, match=f"history.*{field}.*version"):
        create_redo_asset(valid_project, "CHAR_C001", "retry")


@pytest.mark.parametrize("field", ["file_name", "reference_token"])
def test_redo_rejects_duplicate_historical_names(
    valid_project: dict[str, Any], field: str
) -> None:
    original = _asset(valid_project, "CHAR_C001")
    second = copy.deepcopy(original)
    second.update(
        {
            "version": 2,
            "file_name": "CHAR_C001_V002.png",
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
        }
    )
    second[field] = original[field]
    valid_project["assets"].append(second)

    with pytest.raises(WorkflowGuardError, match=f"history.*{field}"):
        create_redo_asset(valid_project, "CHAR_C001", "retry")


def test_redo_rejects_malformed_existing_redo_lineage(
    valid_project: dict[str, Any],
) -> None:
    original = _asset(valid_project, "CHAR_C001")
    second = copy.deepcopy(original)
    second.update(
        {
            "version": 2,
            "file_name": "CHAR_C001_V002.png",
            "reference_token": "@角色_C001_林岚_综合设定图_V002",
            "redo_parent": {"asset_id": "CHAR_C001", "version": 2},
        }
    )
    valid_project["assets"].append(second)

    with pytest.raises(WorkflowGuardError, match="redo_parent"):
        create_redo_asset(valid_project, "CHAR_C001", "retry")


def test_build_media_jobs_selects_redo_as_active_version(
    valid_project: dict[str, Any],
) -> None:
    updated = create_redo_asset(valid_project, "CHAR_C001", "fix face")
    script_count = updated["generation_settings"]["script_episode_count"]
    for episode in updated["episodes"][:script_count]:
        shot = episode["shots"][0]
        for field in ("prompt_zh", "prompt_en"):
            shot[field] = shot[field].replace(
                "@角色_C001_林岚_综合设定图_V001",
                "@角色_C001_林岚_综合设定图_V002",
            )
    updated["assets"] = [
        asset
        for asset in updated["assets"]
        if asset["asset_type"] != "shot_sample"
    ]

    jobs = build_media_jobs(updated)
    character_jobs = [job for job in jobs if job["kind"] == "character_sheet"]
    shot_job = next(job for job in jobs if job["kind"] == "shot_sample")

    assert [(job["asset_id"], job["version"]) for job in character_jobs] == [
        ("CHAR_C001", 2)
    ]
    assert "@角色_C001_林岚_综合设定图_V002" in shot_job["reference_tokens"]
    assert "JOB_CHAR_C001_V002" in shot_job["depends_on"]
