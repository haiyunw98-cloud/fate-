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
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


@pytest.fixture
def valid_project() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def project_path(tmp_path: Path, valid_project: dict[str, Any]) -> Path:
    path = tmp_path / "project.json"
    path.write_text(
        json.dumps(valid_project, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
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
    assert not list(tmp_path.glob(".export.staging-*"))


def test_managed_export_is_replaced_at_same_destination(
    project_path: Path, tmp_path: Path, valid_project: dict[str, Any]
) -> None:
    destination = tmp_path / "export"
    first = export_package(project_path, destination)
    (destination / "obsolete.txt").write_text("old", encoding="utf-8")
    valid_project["analysis"]["world_bible"] = "新世界观"
    project_path.write_text(
        json.dumps(valid_project, ensure_ascii=False), encoding="utf-8"
    )

    second = export_package(project_path, destination)

    assert all(path.parent == destination for path in second.values())
    assert "新世界观" in second["project_md"].read_text(encoding="utf-8")
    assert not (destination / "obsolete.txt").exists()
    assert first["project_md"] == second["project_md"]


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


def test_manifest_checksum_is_not_recomputed_or_forged(
    project_path: Path, tmp_path: Path
) -> None:
    outputs = export_package(project_path, tmp_path / "export")
    manifest_bytes = outputs["media_manifest"].read_bytes()
    assert hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    style = next(
        asset for asset in manifest["assets"] if asset["asset_id"] == "STYLE_PRJ001"
    )
    assert style["checksum"] == "1" * 64
