import json
from pathlib import Path

import pytest

from project_io import atomic_write_json, load_json, next_version, sha256_file


def test_atomic_write_json_round_trips_utf8_and_removes_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "project.json"
    payload = {"title": "未命名项目", "characters": ["小明"]}

    atomic_write_json(path, payload)

    assert load_json(path) == payload
    assert not list(tmp_path.rglob("*.tmp"))


def test_sha256_file_hashes_abc(tmp_path: Path) -> None:
    path = tmp_path / "source.txt"
    path.write_bytes(b"abc")

    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_next_version_starts_at_one_for_missing_asset() -> None:
    assert next_version([], "char_img_c001") == 1


def test_next_version_increments_matching_asset_version() -> None:
    assert next_version([{"asset_id": "char_img_c001", "version": 2}], "char_img_c001") == 3


def test_load_json_rejects_non_object_root(tmp_path: Path) -> None:
    path = tmp_path / "list.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="project root must be an object"):
        load_json(path)


def test_atomic_write_json_removes_temp_file_when_serialization_fails(tmp_path: Path) -> None:
    path = tmp_path / "project.json"

    with pytest.raises(TypeError):
        atomic_write_json(path, {"invalid": {1, 2}})

    assert not list(tmp_path.glob("*.tmp"))
    assert not path.exists()


def test_project_template_has_canonical_shape() -> None:
    template_path = (
        Path(__file__).resolve().parents[1]
        / "skills"
        / "novel-to-ai-drama-pack"
        / "assets"
        / "project-template.json"
    )

    template = json.loads(template_path.read_text(encoding="utf-8"))

    assert list(template) == [
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
    ]
    assert template["generation_settings"]["sample_episode_count"] == 1
    assert template["generation_settings"]["script_episode_count"] == 3
