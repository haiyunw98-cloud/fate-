import json
from pathlib import Path

import pytest

import project_io
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


def test_atomic_write_json_preserves_original_and_removes_temp_file_on_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "project.json"
    original = {"title": "原始项目"}
    path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

    def partially_write_then_interrupt(*args: object, **kwargs: object) -> None:
        target = args[1]
        target.write('{"incomplete":')
        raise KeyboardInterrupt

    monkeypatch.setattr(project_io.json, "dump", partially_write_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        atomic_write_json(path, {"replacement": "不会写入"})

    assert not list(tmp_path.glob("*.tmp"))
    assert load_json(path) == original


def test_atomic_write_json_removes_temp_file_when_json_serialization_fails(
    tmp_path: Path,
) -> None:
    path = tmp_path / "project.json"

    with pytest.raises(TypeError):
        atomic_write_json(path, {"invalid": {1, 2}})

    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(project_io.os.name != "posix", reason="directory fsync is POSIX-specific")
def test_atomic_write_json_fsyncs_parent_directory_after_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    original_replace = project_io.os.replace
    original_fsync = project_io.os.fsync

    def record_replace(source: object, destination: object) -> None:
        events.append("replace")
        original_replace(source, destination)

    def record_fsync(file_descriptor: int) -> None:
        events.append("fsync")
        original_fsync(file_descriptor)

    monkeypatch.setattr(project_io.os, "replace", record_replace)
    monkeypatch.setattr(project_io.os, "fsync", record_fsync)

    atomic_write_json(tmp_path / "project.json", {"title": "已保存"})

    assert events.index("replace") < len(events) - 1
    assert events[-1] == "fsync"


@pytest.mark.skipif(project_io.os.name != "posix", reason="directory fsync is POSIX-specific")
def test_atomic_write_json_propagates_directory_fsync_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fsync_calls = 0
    original_fsync = project_io.os.fsync

    def reject_directory_fsync(file_descriptor: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls == 2:
            raise PermissionError(project_io.errno.EPERM, "permission denied")
        original_fsync(file_descriptor)

    monkeypatch.setattr(project_io.os, "fsync", reject_directory_fsync)

    with pytest.raises(PermissionError):
        atomic_write_json(tmp_path / "project.json", {"title": "已保存"})

    assert not list(tmp_path.glob("*.tmp"))


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
    assert template == {
        "schema_version": "1.0.0",
        "project": {
            "project_id": "PRJ001",
            "title": "未命名项目",
            "status": "draft",
            "target_episode_count": 1,
        },
        "source": {
            "input_type": "pasted_text",
            "file_name": None,
            "sha256": "uninitialized",
            "character_count": 0,
            "chapter_index": [],
            "coverage": 0.0,
        },
        "analysis": {
            "world_bible": "未生成",
            "timeline": [],
            "story_arc": [],
            "adaptation_decisions": [],
        },
        "characters": [],
        "scenes": [],
        "props": [],
        "foods": [],
        "episodes": [],
        "assets": [],
        "generation_settings": {
            "aspect_ratio": "9:16",
            "image_provider": "built_in_image_gen",
            "voice_provider": "openai_speech",
            "sample_episode_count": 1,
            "script_episode_count": 3,
        },
        "generation_runs": [],
    }
