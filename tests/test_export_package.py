from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

import export_package as exporter
from export_package import ExportError, export_package


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "valid-project.json"
SCRIPT_PATH = (
    ROOT
    / "skills"
    / "novel-to-ai-drama-pack"
    / "scripts"
    / "export_package.py"
)
EXPECTED_KEYS = {
    "project_md",
    "shots_xlsx",
    "prompts",
    "media_manifest",
    "validation_report",
}
EXPECTED_MANAGED_FILES = sorted(
    [
        "media-manifest.json",
        "project.md",
        "shots.xlsx",
        "validation-report.txt",
        "video-prompts.txt",
    ]
)
MARKER_NAME = ".novel-to-ai-drama-pack-export.json"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


@pytest.fixture
def valid_project() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _materialize_completed_assets(root: Path, data: dict[str, Any]) -> None:
    for asset in data["assets"]:
        if asset.get("status") != "completed":
            continue
        content = (
            f"{asset['asset_id']}:V{asset['version']:03d}:formal-media\n"
        ).encode("utf-8")
        media_path = root / asset["relative_path"]
        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.write_bytes(content)
        asset["checksum"] = hashlib.sha256(content).hexdigest()


def _write_formal_project(path: Path, data: dict[str, Any]) -> None:
    _materialize_completed_assets(path.parent, data)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def project_path(tmp_path: Path, valid_project: dict[str, Any]) -> Path:
    path = tmp_path / "project.json"
    _write_formal_project(path, valid_project)
    return path


def _sheet_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows: list[list[str]] = []
    for row in root.findall(f".//{{{MAIN_NS}}}row"):
        rows.append(
            [
                "".join(cell.itertext())
                for cell in row.findall(f"{{{MAIN_NS}}}c")
            ]
        )
    return rows


def _export_bytes(outputs: dict[str, Path]) -> dict[str, bytes]:
    return {key: path.read_bytes() for key, path in outputs.items()}


def test_export_contains_all_five_outputs_and_production_content(
    project_path: Path, tmp_path: Path
) -> None:
    outputs = export_package(project_path, tmp_path / "export")

    assert set(outputs) == EXPECTED_KEYS
    assert all(path.is_file() for path in outputs.values())
    project_md = outputs["project_md"].read_text(encoding="utf-8")
    assert "架空古代江湖" in project_md
    assert "E001" in project_md and "E002" in project_md and "E003" in project_md
    assert "清亮女声" in project_md
    assert "completed" in project_md
    prompts = outputs["prompts"].read_text(encoding="utf-8")
    assert "只导出视频提示词，不生成视频" in prompts
    assert "林岚@角色_C001_林岚_综合设定图_V001" in prompts
    assert "莲花酥@食物_F001_莲花酥_食物设定图_V001" in prompts
    report = outputs["validation_report"].read_text(encoding="utf-8")
    assert "Errors: 0" in report


def test_manifest_has_every_asset_version_and_does_not_copy_media(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    outputs = export_package(project_path, tmp_path / "export")
    manifest = json.loads(outputs["media_manifest"].read_text(encoding="utf-8"))

    assert len(manifest["assets"]) == len(valid_project["assets"])
    first = manifest["assets"][0]
    assert set(first) >= {
        "asset_id",
        "version",
        "checksum",
        "relative_path",
        "status",
        "reference_token",
    }
    assert not (outputs["project_md"].parent / "assets").exists()


def test_exports_three_script_episodes_but_only_episode_one_has_shot_sample(
    project_path: Path, tmp_path: Path
) -> None:
    outputs = export_package(project_path, tmp_path / "export")
    prompts = outputs["prompts"].read_text(encoding="utf-8")
    manifest = json.loads(outputs["media_manifest"].read_text(encoding="utf-8"))

    assert all(f"## E{number:03d}" in prompts for number in (1, 2, 3))
    sample_owners = [
        asset["owner_id"]
        for asset in manifest["assets"]
        if asset["asset_type"] == "shot_sample"
    ]
    assert sample_owners == ["E001_SH001"]


def test_xlsx_is_valid_deterministic_ooxml_with_one_row_per_shot(
    project_path: Path, tmp_path: Path
) -> None:
    outputs = export_package(project_path, tmp_path / "export")
    xlsx = outputs["shots_xlsx"]

    assert xlsx.read_bytes().startswith(b"PK")
    assert zipfile.is_zipfile(xlsx)
    with zipfile.ZipFile(xlsx) as archive:
        assert "xl/worksheets/sheet1.xml" in archive.namelist()
        ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    rows = _sheet_rows(xlsx)
    assert len(rows) == 4
    assert rows[0][:4] == ["剧集ID", "镜头ID", "角色ID", "场景ID"]
    assert [row[1] for row in rows[1:]] == [
        "E001_SH001",
        "E002_SH001",
        "E003_SH001",
    ]


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
def test_xlsx_stores_formula_like_user_text_as_inline_text(
    project_path: Path,
    tmp_path: Path,
    valid_project: dict[str, Any],
    prefix: str,
) -> None:
    valid_project["episodes"][0]["shots"][0]["negative_prompt"] = (
        prefix + "SUM(1,1)"
    )
    project_path.write_text(
        json.dumps(valid_project, ensure_ascii=False), encoding="utf-8"
    )

    outputs = export_package(project_path, tmp_path / "export")
    with zipfile.ZipFile(outputs["shots_xlsx"]) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml")

    assert b"<f>" not in sheet
    assert b't="inlineStr"' in sheet
    assert (prefix + "SUM(1,1)") in "\n".join(
        value for row in _sheet_rows(outputs["shots_xlsx"]) for value in row
    )


def test_xlsx_sanitizes_illegal_xml_control_characters(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    valid_project["episodes"][0]["shots"][0]["negative_prompt"] = "bad\u0001text"
    project_path.write_text(
        json.dumps(valid_project, ensure_ascii=False), encoding="utf-8"
    )

    outputs = export_package(project_path, tmp_path / "export")
    rows = _sheet_rows(outputs["shots_xlsx"])
    assert "bad�text" in "\n".join(value for row in rows for value in row)


def test_validation_failure_aggregates_errors_without_creating_output(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    valid_project["project"]["project_id"] = "bad"
    valid_project["episodes"][0]["shots"][0]["prompt_zh"] = "缺少引用"
    project_path.write_text(json.dumps(valid_project), encoding="utf-8")
    output = tmp_path / "export"

    with pytest.raises(ExportError) as caught:
        export_package(project_path, output)

    assert "project validation failed" in str(caught.value)
    assert "project.project_id must match PRJ" in str(caught.value)
    assert "missing inline reference" in str(caught.value)
    assert not output.exists()
    assert list(tmp_path.glob(".export.staging-*")) == []


def test_formal_export_blocks_missing_first_episode_dialogue_audio(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": "你终于来了。"}
    ]
    project_path.write_text(
        json.dumps(valid_project, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ExportError, match="pending required media jobs.*DIALOGUE"):
        export_package(project_path, tmp_path / "export")

    assert not (tmp_path / "export").exists()


def test_formal_export_requires_explicit_dialogue_lines_on_every_e001_shot(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    del valid_project["episodes"][0]["shots"][0]["dialogue_lines"]
    project_path.write_text(
        json.dumps(valid_project, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ExportError, match=r"E001_SH001\.dialogue_lines must be explicitly present"):
        export_package(project_path, tmp_path / "export")

    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize(
    "dialogue_lines",
    [
        {},
        ["not an object"],
        [{"speaker_id": "", "text": "台词"}],
        [{"speaker_id": "C999", "text": "台词"}],
        [{"speaker_id": "C001", "text": "  "}],
    ],
)
def test_formal_export_rejects_malformed_e001_dialogue_contract(
    project_path: Path,
    tmp_path: Path,
    valid_project: dict[str, Any],
    dialogue_lines: object,
) -> None:
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = dialogue_lines
    project_path.write_text(
        json.dumps(valid_project, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ExportError, match="dialogue_lines"):
        export_package(project_path, tmp_path / "export")


def test_empty_dialogue_lines_is_an_explicit_no_dialogue_contract(
    project_path: Path, tmp_path: Path
) -> None:
    outputs = export_package(project_path, tmp_path / "export")
    rows = _sheet_rows(outputs["shots_xlsx"])
    assert rows[1][4] == ""


def test_completed_dialogue_audio_exports_canonical_dialogue_to_xlsx(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    text = "你终于来了。"
    valid_project["episodes"][0]["shots"][0]["dialogue_lines"] = [
        {"speaker_id": "C001", "text": text}
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
            "checksum": "0" * 64,
            "prompt": text,
            "speaker_id": "C001",
            "parent_asset_ids": ["AUD_C001"],
            "status": "completed",
        }
    )
    _write_formal_project(project_path, valid_project)

    outputs = export_package(project_path, tmp_path / "export")
    rows = _sheet_rows(outputs["shots_xlsx"])

    assert rows[1][4] == f"C001：{text}"
    assert f"C001：{text}" in outputs["prompts"].read_text(encoding="utf-8")
    assert f"C001：{text}" in outputs["project_md"].read_text(encoding="utf-8")


def test_formal_export_aggregates_missing_and_checksum_mismatched_media(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    first, second = valid_project["assets"][:2]
    (tmp_path / first["relative_path"]).unlink()
    (tmp_path / second["relative_path"]).write_bytes(b"tampered")

    with pytest.raises(ExportError) as caught:
        export_package(project_path, tmp_path / "export")

    message = str(caught.value)
    assert first["asset_id"] in message and "missing" in message
    assert second["asset_id"] in message and "checksum mismatch" in message
    assert not (tmp_path / "export").exists()


def test_formal_export_rejects_media_path_that_is_a_directory(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    asset = valid_project["assets"][0]
    media_path = tmp_path / asset["relative_path"]
    media_path.unlink()
    media_path.mkdir()

    with pytest.raises(ExportError, match=f"{asset['asset_id']}.*regular file"):
        export_package(project_path, tmp_path / "export")


def test_formal_export_rejects_symlink_media_file_even_when_bytes_match(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    asset = valid_project["assets"][0]
    media_path = tmp_path / asset["relative_path"]
    target = tmp_path / "outside-media.bin"
    target.write_bytes(media_path.read_bytes())
    media_path.unlink()
    media_path.symlink_to(target)

    with pytest.raises(ExportError, match=f"{asset['asset_id']}.*symbolic link"):
        export_package(project_path, tmp_path / "export")


def test_formal_export_rejects_symlink_media_path_component(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    asset = next(
        item for item in valid_project["assets"] if item["asset_type"] == "scene_sheet"
    )
    media_path = tmp_path / asset["relative_path"]
    real_directory = tmp_path / "outside-scenes"
    real_directory.mkdir()
    moved = real_directory / media_path.name
    media_path.replace(moved)
    media_path.parent.rmdir()
    media_path.parent.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(ExportError, match=f"{asset['asset_id']}.*symbolic link component"):
        export_package(project_path, tmp_path / "export")


@pytest.mark.parametrize(
    "unsafe_path",
    ["../outside.png", "/tmp/outside.png", r"C:\outside.png"],
)
def test_formal_export_rejects_unconfined_media_metadata(
    project_path: Path,
    tmp_path: Path,
    valid_project: dict[str, Any],
    unsafe_path: str,
) -> None:
    asset = valid_project["assets"][0]
    asset["relative_path"] = unsafe_path
    project_path.write_text(
        json.dumps(valid_project, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ExportError) as caught:
        export_package(project_path, tmp_path / "export")

    assert f"{asset['asset_id']} media relative_path is not confined" in str(caught.value)
    assert not (tmp_path / "export").exists()


def test_formal_export_fails_closed_when_supports_dir_fd_is_missing(
    project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(exporter.os, "supports_dir_fd")

    with pytest.raises(ExportError, match="platform cannot safely verify media"):
        export_package(project_path, tmp_path / "export")

    assert not (tmp_path / "export").exists()
    assert not list(tmp_path.glob(".export.staging-*"))


def test_formal_export_fails_closed_when_supports_dir_fd_is_not_iterable(
    project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exporter.os, "supports_dir_fd", None)

    with pytest.raises(ExportError, match="platform cannot safely verify media"):
        export_package(project_path, tmp_path / "export")

    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize("flag", ["O_NOFOLLOW", "O_DIRECTORY"])
def test_formal_export_fails_closed_without_required_open_flag(
    project_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
) -> None:
    monkeypatch.setattr(exporter.os, flag, 0)

    with pytest.raises(ExportError, match="platform cannot safely verify media"):
        export_package(project_path, tmp_path / "export")

    assert not (tmp_path / "export").exists()


def test_formal_export_fails_closed_when_no_follow_flag_is_missing(
    project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delattr(exporter.os, "O_NOFOLLOW")

    with pytest.raises(ExportError, match="platform cannot safely verify media"):
        export_package(project_path, tmp_path / "export")

    assert not (tmp_path / "export").exists()


def test_runtime_dir_fd_type_error_fails_closed_and_closes_open_fds(
    project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = exporter.os.open
    real_close = exporter.os.close
    opened: list[int] = []
    closed: list[int] = []

    def failing_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if dir_fd is not None:
            raise TypeError("dir_fd unsupported at runtime")
        descriptor = real_open(path, flags, mode)
        opened.append(descriptor)
        return descriptor

    def recording_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(exporter.os, "open", failing_open)
    monkeypatch.setattr(exporter.os, "close", recording_close)
    monkeypatch.setattr(exporter.os, "supports_dir_fd", {failing_open})

    with pytest.raises(ExportError, match="platform cannot safely verify media"):
        export_package(project_path, tmp_path / "export")

    assert opened
    assert sorted(opened) == sorted(closed)
    assert not (tmp_path / "export").exists()
    assert not list(tmp_path.glob(".export.staging-*"))


def test_cli_fails_closed_on_unsafe_media_platform_without_traceback(
    project_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(exporter.os, "O_NOFOLLOW", 0)

    result = exporter.main(
        [str(project_path), "--output-dir", str(tmp_path / "export")]
    )
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert "platform cannot safely verify media" in captured.err
    assert "Traceback" not in captured.err
    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize("script_count", [1, 2, 4])
def test_formal_export_requires_exactly_three_script_episodes(
    project_path: Path,
    tmp_path: Path,
    valid_project: dict[str, Any],
    script_count: int,
) -> None:
    valid_project["generation_settings"]["script_episode_count"] = script_count
    project_path.write_text(
        json.dumps(valid_project, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ExportError, match="script_episode_count must be exactly 3"):
        export_package(project_path, tmp_path / "export")


def test_formal_export_requires_first_three_canonical_episodes(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    valid_project["episodes"] = valid_project["episodes"][:2]
    valid_project["project"]["target_episode_count"] = 2
    valid_project["generation_settings"]["script_episode_count"] = 2
    project_path.write_text(
        json.dumps(valid_project, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ExportError, match="first three episodes must be E001, E002, E003"):
        export_package(project_path, tmp_path / "export")

    assert not (tmp_path / "export").exists()


def test_validation_failure_does_not_change_existing_managed_export(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    destination = tmp_path / "export"
    outputs = export_package(project_path, destination)
    before = _export_bytes(outputs)
    valid_project["episodes"][0]["shots"][0]["prompt_zh"] = "无引用"
    project_path.write_text(
        json.dumps(valid_project, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ExportError, match="project validation failed"):
        export_package(project_path, destination)

    assert _export_bytes(outputs) == before


def test_mid_write_failure_preserves_existing_managed_export(
    project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "export"
    first = export_package(project_path, destination)
    before = _export_bytes(first)

    def fail_write(data: dict[str, Any], stage: Path) -> dict[str, Path]:
        (stage / "project.md").write_text("partial", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr(exporter, "_write_all", fail_write)
    with pytest.raises(ExportError, match="disk full"):
        export_package(project_path, destination)

    assert _export_bytes(first) == before
    assert list(tmp_path.glob(".export.staging-*")) == []


def test_publish_failure_restores_existing_managed_export(
    project_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "export"
    first = export_package(project_path, destination)
    before = _export_bytes(first)
    real_replace = exporter.os.replace
    calls = 0

    def fail_stage_publish(source: object, target: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("publish interrupted")
        real_replace(source, target)

    monkeypatch.setattr(exporter.os, "replace", fail_stage_publish)
    with pytest.raises(ExportError, match="publish interrupted"):
        export_package(project_path, destination)

    assert _export_bytes(first) == before
    assert not list(tmp_path.glob(".export.backup-*"))
    assert not list(tmp_path.glob(".export.previous-*"))
    assert not list(tmp_path.glob(".export.staging-*"))


def test_fsync_failure_preserves_failed_directory_and_restores_previous(
    project_path: Path,
    tmp_path: Path,
    valid_project: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "export"
    export_package(project_path, destination)
    before = {
        path.name: path.read_bytes()
        for path in destination.iterdir()
        if path.is_file()
    }
    valid_project["analysis"]["world_bible"] = "未完成新版"
    _write_formal_project(project_path, valid_project)
    real_sync = exporter._fsync_directory
    failed_once = False

    def injecting_failed_sync(path: Path) -> None:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            (destination / "user-during-fsync.txt").write_text(
                "keep", encoding="utf-8"
            )
            raise OSError("fsync interrupted")
        real_sync(path)

    monkeypatch.setattr(exporter, "_fsync_directory", injecting_failed_sync)

    with pytest.raises(ExportError, match="failed export preserved"):
        export_package(project_path, destination)

    assert {
        path.name: path.read_bytes()
        for path in destination.iterdir()
        if path.is_file()
    } == before
    failed = list(tmp_path.glob(".export.failed-*"))
    assert len(failed) == 1
    assert (failed[0] / "user-during-fsync.txt").read_text(encoding="utf-8") == "keep"
    assert not list(tmp_path.glob(".export.previous-*"))
    assert not list(tmp_path.glob(".export.staging-*"))


def test_managed_export_is_replaced_at_same_destination(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    destination = tmp_path / "export"
    first = export_package(project_path, destination)
    before = {
        path.name: path.read_bytes()
        for path in destination.iterdir()
        if path.is_file()
    }
    valid_project["analysis"]["world_bible"] = "新世界观"
    project_path.write_text(
        json.dumps(valid_project, ensure_ascii=False), encoding="utf-8"
    )

    second = export_package(project_path, destination)

    assert all(path.parent == destination for path in second.values())
    assert "新世界观" in second["project_md"].read_text(encoding="utf-8")
    assert first["project_md"] == second["project_md"]
    previous = list(tmp_path.glob(".export.previous-*"))
    assert len(previous) == 1
    assert {
        path.name: path.read_bytes()
        for path in previous[0].iterdir()
        if path.is_file()
    } == before


def test_managed_marker_binds_project_and_exact_file_whitelist(
    project_path: Path, tmp_path: Path
) -> None:
    outputs = export_package(project_path, tmp_path / "export")
    marker = json.loads(
        (outputs["project_md"].parent / MARKER_NAME).read_text(encoding="utf-8")
    )

    assert marker["export_format"] == "novel-to-ai-drama-pack"
    assert marker["format_version"] == 1
    assert marker["project_id"] == "PRJ001"
    assert list(marker["managed_files"]) == EXPECTED_MANAGED_FILES
    export_dir = outputs["project_md"].parent
    for name in EXPECTED_MANAGED_FILES:
        payload = (export_dir / name).read_bytes()
        assert marker["managed_files"][name] == {
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }


def test_old_static_marker_cannot_authorize_replacement(
    project_path: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "export"
    destination.mkdir()
    for name in EXPECTED_MANAGED_FILES:
        (destination / name).write_bytes(f"user:{name}".encode("utf-8"))
    (destination / MARKER_NAME).write_text(
        json.dumps(
            {
                "export_format": "novel-to-ai-drama-pack",
                "format_version": 1,
            }
        ),
        encoding="utf-8",
    )
    before = {path.name: path.read_bytes() for path in destination.iterdir()}

    outputs = export_package(project_path, destination)

    assert outputs["project_md"].parent == tmp_path / "export-v001"
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == before


def test_marker_for_different_project_cannot_authorize_replacement(
    project_path: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "export"
    export_package(project_path, destination)
    marker_path = destination / MARKER_NAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["project_id"] = "PRJ999"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in destination.iterdir()}

    outputs = export_package(project_path, destination)

    assert outputs["project_md"].parent == tmp_path / "export-v001"
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == before


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "directory",
        "symlink",
        "marker_directory",
        "marker_symlink",
    ],
)
def test_nonexact_managed_directory_is_preserved_and_uses_sibling(
    project_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    destination = tmp_path / "export"
    export_package(project_path, destination)
    target = destination / "project.md"
    if mutation == "missing":
        target.unlink()
    elif mutation == "extra":
        (destination / "user-sentinel.txt").write_text("keep", encoding="utf-8")
    elif mutation == "directory":
        target.unlink()
        target.mkdir()
    elif mutation == "symlink":
        outside = tmp_path / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        target.unlink()
        target.symlink_to(outside)
    elif mutation == "marker_directory":
        marker = destination / MARKER_NAME
        marker.unlink()
        marker.mkdir()
    else:
        outside = tmp_path / "outside-marker.json"
        outside.write_text("{}", encoding="utf-8")
        marker = destination / MARKER_NAME
        marker.unlink()
        marker.symlink_to(outside)
    before_names = sorted(path.name for path in destination.iterdir())
    sentinel_bytes = {
        path.name: path.read_bytes()
        for path in destination.iterdir()
        if path.is_file() and not path.is_symlink()
    }

    outputs = export_package(project_path, destination)

    assert outputs["project_md"].parent == tmp_path / "export-v001"
    assert sorted(path.name for path in destination.iterdir()) == before_names
    assert {
        path.name: path.read_bytes()
        for path in destination.iterdir()
        if path.is_file() and not path.is_symlink()
    } == sentinel_bytes


def test_unknown_injected_after_backup_rename_is_restored_and_published_to_sibling(
    project_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "export"
    export_package(project_path, destination)
    real_replace = exporter.os.replace
    injected = False

    def injecting_replace(source: object, target: object) -> None:
        nonlocal injected
        real_replace(source, target)
        source_path = Path(source)
        target_path = Path(target)
        if source_path == destination and ".previous-" in target_path.name:
            (target_path / "user-after-rename.txt").write_text(
                "keep", encoding="utf-8"
            )
            injected = True

    monkeypatch.setattr(exporter.os, "replace", injecting_replace)

    outputs = export_package(project_path, destination)

    assert injected
    assert (destination / "user-after-rename.txt").read_text(encoding="utf-8") == "keep"
    assert outputs["project_md"].parent == tmp_path / "export-v001"


def test_same_named_file_replaced_after_rename_is_restored_without_data_loss(
    project_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "export"
    export_package(project_path, destination)
    real_replace = exporter.os.replace
    replacement = b"user replaced project.md after rename"

    def replacing_after_rename(source: object, target: object) -> None:
        real_replace(source, target)
        source_path = Path(source)
        target_path = Path(target)
        if source_path == destination and ".previous-" in target_path.name:
            (target_path / "project.md").write_bytes(replacement)

    monkeypatch.setattr(exporter.os, "replace", replacing_after_rename)

    outputs = export_package(project_path, destination)

    assert outputs["project_md"].parent == tmp_path / "export-v001"
    assert (destination / "project.md").read_bytes() == replacement
    assert not list(tmp_path.glob(".export.previous-*"))


@pytest.mark.parametrize("field", ["sha256", "size"])
def test_marker_file_record_mismatch_makes_directory_unknown(
    project_path: Path,
    tmp_path: Path,
    field: str,
) -> None:
    destination = tmp_path / "export"
    export_package(project_path, destination)
    marker_path = destination / MARKER_NAME
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["managed_files"]["project.md"][field] = (
        "0" * 64 if field == "sha256" else marker["managed_files"]["project.md"][field] + 1
    )
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in destination.iterdir()}

    outputs = export_package(project_path, destination)

    assert outputs["project_md"].parent == tmp_path / "export-v001"
    assert {path.name: path.read_bytes() for path in destination.iterdir()} == before


def test_repeated_managed_updates_preserve_unique_previous_histories(
    project_path: Path,
    tmp_path: Path,
    valid_project: dict[str, Any],
) -> None:
    destination = tmp_path / "export"
    export_package(project_path, destination)
    valid_project["analysis"]["world_bible"] = "第二版"
    _write_formal_project(project_path, valid_project)
    export_package(project_path, destination)
    valid_project["analysis"]["world_bible"] = "第三版"
    _write_formal_project(project_path, valid_project)
    export_package(project_path, destination)

    previous = sorted(tmp_path.glob(".export.previous-*"))
    assert len(previous) == 2
    assert all((path / MARKER_NAME).is_file() for path in previous)


def test_unknown_existing_directory_gets_versioned_sibling(
    project_path: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "export"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("user data", encoding="utf-8")

    outputs = export_package(project_path, destination)

    published = outputs["project_md"].parent
    assert published == tmp_path / "export-v001"
    assert sentinel.read_text(encoding="utf-8") == "user data"
    assert all(path.parent == published for path in outputs.values())


def test_versioned_sibling_never_overwrites_an_existing_sibling(
    project_path: Path, tmp_path: Path
) -> None:
    (tmp_path / "export").mkdir()
    (tmp_path / "export-v001").mkdir()
    sentinel = tmp_path / "export-v001" / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    outputs = export_package(project_path, tmp_path / "export")

    assert outputs["project_md"].parent == tmp_path / "export-v002"
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_stale_predictable_staging_directory_is_not_deleted(
    project_path: Path, tmp_path: Path
) -> None:
    stale = tmp_path / ".export.staging"
    stale.mkdir()
    sentinel = stale / "keep.txt"
    sentinel.write_text("not ours", encoding="utf-8")

    export_package(project_path, tmp_path / "export")

    assert sentinel.read_text(encoding="utf-8") == "not ours"


def test_rejects_output_that_would_replace_source_parent(
    project_path: Path, tmp_path: Path
) -> None:
    with pytest.raises(ExportError, match="contains the source project"):
        export_package(project_path, tmp_path)
    assert project_path.exists()


def test_rejects_output_containing_a_symlinked_source_path(
    project_path: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "export"
    destination.mkdir()
    source_link = destination / "project.json"
    source_link.symlink_to(project_path)

    with pytest.raises(ExportError, match="contains the source project path"):
        export_package(source_link, destination)

    assert source_link.is_symlink()
    assert project_path.exists()


def test_rejects_symlink_destination_without_touching_target(
    project_path: Path, tmp_path: Path
) -> None:
    target = tmp_path / "real"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    link = tmp_path / "export"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ExportError, match="symbolic link"):
        export_package(project_path, link)
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_rejects_destination_hardlinked_to_source_project(
    project_path: Path, tmp_path: Path
) -> None:
    alias = tmp_path / "export"
    os.link(project_path, alias)
    before = project_path.read_bytes()

    with pytest.raises(ExportError, match="aliases the source project"):
        export_package(project_path, alias)

    assert project_path.read_bytes() == before


def test_export_is_deterministic_and_does_not_modify_project_bytes(
    project_path: Path, tmp_path: Path
) -> None:
    source_before = project_path.read_bytes()
    first = export_package(project_path, tmp_path / "one")
    second = export_package(project_path, tmp_path / "two")

    assert _export_bytes(first) == _export_bytes(second)
    assert project_path.read_bytes() == source_before


def test_cli_runs_from_arbitrary_cwd_and_prints_only_absolute_outputs(
    project_path: Path, tmp_path: Path
) -> None:
    output = tmp_path / "cli-export"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            str(project_path),
            "--output-dir",
            str(output),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 5
    assert all(Path(line).is_absolute() and Path(line).is_file() for line in lines)
    assert result.stderr == ""


def test_cli_failure_uses_stderr_without_traceback(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            str(tmp_path / "missing.json"),
            "--output-dir",
            str(tmp_path / "export"),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert "missing.json" in result.stderr


def test_manifest_preserves_the_physically_verified_checksum(
    project_path: Path, tmp_path: Path
) -> None:
    outputs = export_package(project_path, tmp_path / "export")
    manifest_bytes = outputs["media_manifest"].read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    style = next(
        asset for asset in manifest["assets"] if asset["asset_id"] == "STYLE_PRJ001"
    )
    media = project_path.parent / style["relative_path"]
    assert style["checksum"] == hashlib.sha256(media.read_bytes()).hexdigest()
