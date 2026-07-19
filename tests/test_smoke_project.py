import hashlib
import json
import shutil
from pathlib import Path

import pytest

from build_media_jobs import build_media_jobs
from export_package import ExportError, export_package
from validate_project import validate_project
from validate_references import validate_references


ROOT = Path(__file__).resolve().parents[1]
PROJECT_PATH = ROOT / "smoke-projects" / "sample-drama" / "project.json"


def test_smoke_project_plans_only_the_five_real_images_and_one_voice_sample():
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))

    assert project["project"] == {
        "project_id": "PRJ900",
        "title": "听雨楼",
        "status": "production",
        "target_episode_count": 3,
    }
    assets = project["assets"]
    assert [asset["asset_id"] for asset in assets] == [
        "STYLE_PRJ900",
        "CHAR_C001",
        "SCENE_S001",
        "FOOD_F001",
        "SHOT_E001_SH001",
        "AUD_C001",
    ]
    image_assets = [asset for asset in assets if asset["asset_type"] != "voice_sample"]
    assert len(image_assets) == 5
    assert all(asset["status"] == "completed" for asset in image_assets)
    for asset in image_assets:
        path = PROJECT_PATH.parent / asset["relative_path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == asset["checksum"]
    voice = next(asset for asset in assets if asset["asset_type"] == "voice_sample")
    assert voice["status"] == "confirmed"
    assert "checksum" not in voice
    assert not (PROJECT_PATH.parent / voice["relative_path"]).exists()

    jobs = build_media_jobs(project)
    expected_pending = {
        asset["asset_id"] for asset in assets if asset["status"] != "completed"
    }
    assert {job["asset_id"] for job in jobs} == expected_pending


def test_smoke_project_uses_exact_production_paths_tokens_and_dependencies():
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    by_id = {asset["asset_id"]: asset for asset in project["assets"]}

    assert {
        asset_id: (asset["relative_path"], asset["reference_token"])
        for asset_id, asset in by_id.items()
    } == {
        "STYLE_PRJ900": (
            "assets/style/STYLE_PRJ900_V001.png",
            "@项目_PRJ900_听雨楼_风格参考图_V001",
        ),
        "CHAR_C001": (
            "assets/characters/CHAR_C001_V001.png",
            "@角色_C001_林岚_综合设定图_V001",
        ),
        "SCENE_S001": (
            "assets/scenes/SCENE_S001_V001.png",
            "@场景_S001_听雨楼包厢_场景设定图_V001",
        ),
        "FOOD_F001": (
            "assets/foods/FOOD_F001_V001.png",
            "@食物_F001_莲花酥_食物设定图_V001",
        ),
        "SHOT_E001_SH001": (
            "assets/shots/SHOT_E001_SH001_V001.png",
            "@镜头_E001_SH001_样片图_V001",
        ),
        "AUD_C001": (
            "assets/audio/AUD_C001_V001.wav",
            "@声音_AUD_C001_林岚_标准声音_V001",
        ),
    }
    assert by_id["STYLE_PRJ900"]["parent_asset_ids"] == []
    assert by_id["CHAR_C001"]["parent_asset_ids"] == ["STYLE_PRJ900"]
    assert by_id["SCENE_S001"]["parent_asset_ids"] == ["STYLE_PRJ900"]
    assert by_id["FOOD_F001"]["parent_asset_ids"] == ["STYLE_PRJ900"]
    assert by_id["SHOT_E001_SH001"]["parent_asset_ids"] == [
        "STYLE_PRJ900",
        "CHAR_C001",
        "SCENE_S001",
        "FOOD_F001",
    ]


def test_smoke_project_records_credentialless_voice_dry_run_without_fake_audio():
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    voice = next(asset for asset in project["assets"] if asset["asset_id"] == "AUD_C001")

    assert voice["status"] == "confirmed"
    assert voice["ai_generated"] is True
    assert voice["ai_disclosure"] == "本项目角色声音由 AI 生成，使用内置合成声线，未使用真人声音克隆。"
    assert not (PROJECT_PATH.parent / voice["relative_path"]).exists()
    assert project["generation_runs"] == [
        {
            "run_id": "RUN_SMOKE_VOICE_DRY_001",
            "operation": "voice_sample_dry_run",
            "asset_id": "AUD_C001",
            "version": 1,
            "status": "dry_run_completed",
            "reason": "OPENAI_API_KEY absent",
            "output_created": False,
        }
    ]


def test_smoke_formal_export_is_blocked_only_by_the_unfinished_voice(tmp_path: Path):
    project = json.loads(PROJECT_PATH.read_text(encoding="utf-8"))

    assert validate_project(project) == []
    assert validate_references(project) == []
    jobs = build_media_jobs(project)
    assert [(job["job_id"], job["asset_id"], job["kind"]) for job in jobs] == [
        ("JOB_AUD_C001_V001", "AUD_C001", "voice_sample")
    ]
    delivery_project = tmp_path / "sample-drama"
    delivery_project.mkdir()
    project["project"]["status"] = "completed"
    for asset in project["assets"]:
        if asset["asset_type"] == "voice_sample":
            continue
        source = PROJECT_PATH.parent / asset["relative_path"]
        destination = delivery_project / asset["relative_path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    delivery_path = delivery_project / "project.json"
    delivery_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ExportError) as caught:
        export_package(delivery_path, tmp_path / "export")
    assert str(caught.value) == (
        "project validation failed:\n"
        "formal export has pending required media jobs: JOB_AUD_C001_V001"
    )
    assert not (tmp_path / "export").exists()
