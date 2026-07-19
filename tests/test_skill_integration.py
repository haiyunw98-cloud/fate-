from __future__ import annotations

import copy
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from build_media_jobs import build_media_jobs
from export_package import export_package
from extract_source import extract_text
from project_io import atomic_write_json, sha256_file
from validate_project import validate_project
from validate_references import validate_references
from workflow_guard import create_redo_asset, resumable_jobs, transition_asset


ROOT = Path(__file__).resolve().parents[1]
NOVEL_FIXTURE = ROOT / "tests" / "fixtures" / "sample-novel.md"
PROJECT_FIXTURE = ROOT / "tests" / "fixtures" / "valid-project.json"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
FORMAL_OUTPUT_KEYS = {
    "project_md",
    "shots_xlsx",
    "prompts",
    "media_manifest",
    "validation_report",
}
OWNER_TYPES = {
    "style_reference": "project",
    "character_sheet": "character",
    "expression_sheet": "character",
    "action_sheet": "character",
    "voice_sample": "character",
    "scene_sheet": "scene",
    "prop_sheet": "prop",
    "food_image": "food",
    "shot_sample": "shot",
    "dialogue_audio": "shot",
}


def _asset(
    asset_id: str,
    asset_type: str,
    owner_id: str,
    token: str,
    relative_path: str,
    prompt: str,
    parent_asset_ids: list[str],
) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "version": 1,
        "asset_type": asset_type,
        "owner_type": OWNER_TYPES[asset_type],
        "owner_id": owner_id,
        "reference_token": token,
        "file_name": Path(relative_path).name,
        "relative_path": relative_path,
        "prompt": prompt,
        "parent_asset_ids": parent_asset_ids,
        "status": "confirmed",
    }


def _derived_project(source_text: str) -> dict[str, Any]:
    project = json.loads(PROJECT_FIXTURE.read_text(encoding="utf-8"))
    project["project"] = {
        "project_id": "PRJ001",
        "title": "回声剑宴",
        "status": "production",
        "target_episode_count": 3,
    }
    project["source"] = {
        "input_type": "markdown",
        "file_name": NOVEL_FIXTURE.name,
        "sha256": sha256_file(NOVEL_FIXTURE),
        "character_count": len(source_text),
        "chapter_index": [
            {"chapter": "第一章", "offset": source_text.index("## 第一章")},
            {"chapter": "第二章", "offset": source_text.index("## 第二章")},
        ],
        "coverage": 1.0,
    }
    project["analysis"] = {
        "world_bible": "架空古城，听雨楼包厢的暗格与双生回声剑推动谜案。",
        "timeline": ["雨夜得剑", "重返包厢", "双剑现身"],
        "story_arc": ["相遇", "查证", "背叛悬念"],
        "adaptation_decisions": ["两章拆成三集，每集保留追看钩子"],
    }
    project["characters"] = [
        {
            "character_id": "C001",
            "name": "林岚",
            "importance": "lead",
            "role": "protagonist",
            "appearance": "青衣束发，目光坚毅",
            "voice_profile": {
                "voice": "清亮女声",
                "tone": "沉稳警觉",
                "pace": "中速",
                "sample_text": "这柄剑在告诉我们，房间里还有秘密。",
            },
        },
        {
            "character_id": "C002",
            "name": "苏遥",
            "importance": "major",
            "role": "ally",
            "appearance": "赭红短袄，长发编辫，神情机敏",
            "voice_profile": {
                "voice": "温润女声",
                "tone": "克制而急切",
                "pace": "稍快",
                "sample_text": "我父亲失踪前，只留下这柄剑。",
            },
        },
        {
            "character_id": "C003",
            "name": "韩策",
            "importance": "minor",
            "role": "suspected_antagonist",
            "appearance": "玄色巡夜甲，鬓角微白，神情冷峻",
            "voice_profile": {
                "voice": "低沉男声",
                "tone": "威严",
                "pace": "缓慢",
                "sample_text": "把剑交出来。",
            },
        },
    ]
    project["scenes"] = [
        {"scene_id": "S001", "name": "听雨楼包厢", "importance": "important"}
    ]
    project["props"] = [
        {"prop_id": "P001", "name": "青铜回声剑", "importance": "important"},
        {"prop_id": "P002", "name": "普通木杯", "importance": "secondary"},
    ]
    project["foods"] = [
        {"food_id": "F001", "name": "月影桂花鱼", "importance": "important"}
    ]

    char_1 = "@角色_C001_林岚_综合设定图_V001"
    char_2 = "@角色_C002_苏遥_综合设定图_V001"
    char_3 = "@角色_C003_韩策_综合设定图_V001"
    scene = "@场景_S001_听雨楼包厢_场景设定图_V001"
    prop = "@道具_P001_青铜回声剑_道具设定图_V001"
    food = "@食物_F001_月影桂花鱼_食物设定图_V001"
    project["episodes"] = [
        {
            "episode_id": "E001",
            "episode_number": 1,
            "title": "雨夜剑鸣",
            "script": "林岚在听雨楼包厢接过回声剑，与苏遥遭遇突袭。",
            "shots": [
                {
                    "shot_id": "E001_SH001",
                    "character_ids": ["C001", "C002"],
                    "scene_id": "S001",
                    "prop_ids": ["P001"],
                    "food_ids": ["F001"],
                    "dialogue_lines": [
                        {
                            "speaker_id": "C002",
                            "text": "我父亲说，剑鸣时就把它交给你。",
                        },
                        {
                            "speaker_id": "C001",
                            "text": "先别碰那只普通木杯，墙后有声音。",
                        },
                    ],
                    "prompt_zh": (
                        f"林岚{char_1}推门进入听雨楼包厢{scene}，"
                        f"苏遥{char_2}守在桌旁，青铜回声剑{prop}压着旧布，"
                        f"月影桂花鱼{food}仍冒热气，普通木杯倒扣窗边"
                    ),
                    "prompt_en": (
                        f"林岚{char_1} enters 听雨楼包厢{scene}; "
                        f"苏遥{char_2} guards the table, 青铜回声剑{prop} lies "
                        f"beside 月影桂花鱼{food}, with an ordinary wooden cup by the window"
                    ),
                    "negative_prompt": "换脸，多余肢体，物品变形，文字水印",
                    "expression_asset_id": None,
                    "action_asset_id": None,
                }
            ],
        },
        {
            "episode_id": "E002",
            "episode_number": 2,
            "title": "暗格名单",
            "script": "二人重返包厢，从剑柄中找到内应名单。",
            "shots": [
                {
                    "shot_id": "E002_SH001",
                    "character_ids": ["C001", "C002"],
                    "scene_id": "S001",
                    "prop_ids": ["P001"],
                    "food_ids": [],
                    "prompt_zh": (
                        f"林岚{char_1}把青铜回声剑{prop}贴近墙砖，"
                        f"苏遥{char_2}在听雨楼包厢{scene}"
                        "按下暗格，普通木杯保持倒扣且不使用参考图"
                    ),
                    "prompt_en": (
                        f"林岚{char_1} holds 青铜回声剑{prop} against the wall "
                        f"while 苏遥{char_2} opens the hidden compartment in "
                        f"听雨楼包厢{scene}; the ordinary wooden cup has no reference asset"
                    ),
                    "negative_prompt": "场景结构变化，普通物品被强化，多余人物",
                    "expression_asset_id": None,
                    "action_asset_id": None,
                }
            ],
        },
        {
            "episode_id": "E003",
            "episode_number": 3,
            "title": "双剑悬局",
            "script": "韩策索剑，暗格里却出现第二柄一模一样的回声剑。",
            "shots": [
                {
                    "shot_id": "E003_SH001",
                    "character_ids": ["C001", "C002", "C003"],
                    "scene_id": "S001",
                    "prop_ids": ["P001"],
                    "food_ids": [],
                    "prompt_zh": (
                        f"韩策{char_3}站在听雨楼包厢{scene}门外伸手，"
                        f"林岚{char_1}握住青铜回声剑{prop}，"
                        f"苏遥{char_2}被暗格中的陌生手拉向阴影，"
                        "镜头停在第二柄剑上"
                    ),
                    "prompt_en": (
                        f"韩策{char_3} reaches into 听雨楼包厢{scene}; "
                        f"林岚{char_1} grips 青铜回声剑{prop}, while 苏遥{char_2} "
                        "is pulled toward the hidden compartment, ending on a second "
                        "identical sword"
                    ),
                    "negative_prompt": (
                        "角色混脸，武器数量错误，镜头闪烁，文字水印"
                    ),
                    "expression_asset_id": None,
                    "action_asset_id": None,
                }
            ],
        },
    ]

    project["assets"] = [
        _asset(
            "STYLE_PRJ001",
            "style_reference",
            "PRJ001",
            "@项目_PRJ001_回声剑宴_风格参考图_V001",
            "assets/style/STYLE_PRJ001_V001.png",
            "国风写实动画，雨夜青灰与暖灯统一光影",
            [],
        ),
        _asset(
            "CHAR_C001",
            "character_sheet",
            "C001",
            char_1,
            "assets/characters/CHAR_C001_V001.png",
            "林岚综合角色测试设定图",
            ["STYLE_PRJ001"],
        ),
        _asset(
            "CHAR_C002",
            "character_sheet",
            "C002",
            char_2,
            "assets/characters/CHAR_C002_V001.png",
            "苏遥综合角色测试设定图",
            ["STYLE_PRJ001"],
        ),
        _asset(
            "CHAR_C003",
            "character_sheet",
            "C003",
            char_3,
            "assets/characters/CHAR_C003_V001.png",
            "韩策综合角色测试设定图",
            ["STYLE_PRJ001"],
        ),
        _asset(
            "EXPR_C001",
            "expression_sheet",
            "C001",
            "@角色_C001_林岚_表情设定图_V001",
            "assets/characters/EXPR_C001_V001.png",
            "林岚表情测试设定图",
            ["CHAR_C001"],
        ),
        _asset(
            "EXPR_C002",
            "expression_sheet",
            "C002",
            "@角色_C002_苏遥_表情设定图_V001",
            "assets/characters/EXPR_C002_V001.png",
            "苏遥表情测试设定图",
            ["CHAR_C002"],
        ),
        _asset(
            "ACTION_C001",
            "action_sheet",
            "C001",
            "@角色_C001_林岚_动作设定图_V001",
            "assets/characters/ACTION_C001_V001.png",
            "林岚动作测试设定图",
            ["CHAR_C001"],
        ),
        _asset(
            "ACTION_C002",
            "action_sheet",
            "C002",
            "@角色_C002_苏遥_动作设定图_V001",
            "assets/characters/ACTION_C002_V001.png",
            "苏遥动作测试设定图",
            ["CHAR_C002"],
        ),
        _asset(
            "SCENE_S001",
            "scene_sheet",
            "S001",
            scene,
            "assets/scenes/SCENE_S001_V001.png",
            "听雨楼包厢测试场景设定图",
            ["STYLE_PRJ001"],
        ),
        _asset(
            "PROP_P001",
            "prop_sheet",
            "P001",
            prop,
            "assets/props/PROP_P001_V001.png",
            "青铜回声剑测试道具设定图",
            ["STYLE_PRJ001"],
        ),
        _asset(
            "FOOD_F001",
            "food_image",
            "F001",
            food,
            "assets/foods/FOOD_F001_V001.png",
            "月影桂花鱼测试菜肴图",
            ["STYLE_PRJ001"],
        ),
        _asset(
            "AUD_C001",
            "voice_sample",
            "C001",
            "@声音_AUD_C001_林岚_标准声音_V001",
            "assets/audio/AUD_C001_V001.wav",
            "林岚标准测试样音",
            [],
        ),
        _asset(
            "AUD_C002",
            "voice_sample",
            "C002",
            "@声音_AUD_C002_苏遥_标准声音_V001",
            "assets/audio/AUD_C002_V001.wav",
            "苏遥标准测试样音",
            [],
        ),
        _asset(
            "SHOT_E001_SH001",
            "shot_sample",
            "E001_SH001",
            "@镜头_E001_SH001_样片图_V001",
            "assets/shots/SHOT_E001_SH001_V001.png",
            "第一集第一镜测试样片图",
            [
                "CHAR_C001",
                "CHAR_C002",
                "SCENE_S001",
                "PROP_P001",
                "FOOD_F001",
            ],
        ),
    ]
    project["generation_runs"] = []
    return project


def _asset_from_job(
    job: dict[str, Any], jobs_by_id: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    output = job["output"]
    asset = {
        "asset_id": job["asset_id"],
        "version": job["version"],
        "asset_type": job["kind"],
        "owner_type": OWNER_TYPES[job["kind"]],
        "owner_id": job["owner_id"],
        "reference_token": output["reference_token"],
        "file_name": output["file_name"],
        "relative_path": output["relative_path"],
        "prompt": job["prompt"],
        "parent_asset_ids": [jobs_by_id[parent]["asset_id"] for parent in job["depends_on"]],
        "status": "confirmed",
        "ai_generated": True,
        "ai_disclosure": "deterministic integration test fixture; not generated media",
    }
    if job["kind"] == "dialogue_audio":
        asset.update(
            {
                "speaker_id": job["input"]["speaker_id"],
                "text": job["input"]["text"],
                "content_fingerprint": job["input"]["content_fingerprint"],
            }
        )
    return asset


def _materialize_jobs(
    project: dict[str, Any],
    project_root: Path,
    jobs: list[dict[str, Any]],
) -> None:
    jobs_by_id = {job["job_id"]: job for job in jobs}
    assets_by_identity = {
        (asset["asset_id"], asset["version"]): index
        for index, asset in enumerate(project["assets"])
    }
    for job in jobs:
        assert all(jobs_by_id[parent]["status"] == "completed" for parent in job["depends_on"])
        identity = (job["asset_id"], job["version"])
        if identity in assets_by_identity:
            index = assets_by_identity[identity]
            asset = copy.deepcopy(project["assets"][index])
            asset.update(
                {
                    "file_name": job["output"]["file_name"],
                    "relative_path": job["output"]["relative_path"],
                    "reference_token": job["output"]["reference_token"],
                    "parent_asset_ids": [
                        jobs_by_id[parent]["asset_id"]
                        for parent in job["depends_on"]
                    ],
                }
            )
        else:
            asset = _asset_from_job(job, jobs_by_id)
            index = len(project["assets"])
            project["assets"].append(asset)
            assets_by_identity[identity] = index

        if asset["status"] == "redo":
            asset = transition_asset(asset, "confirmed")
        asset = transition_asset(asset, "generating")
        job["status"] = "generating"
        payload = (
            "DETERMINISTIC TEST FIXTURE ONLY; NOT REAL GENERATED MEDIA\n"
            f"{job['job_id']}\n"
        ).encode("utf-8")
        media_path = project_root / job["output"]["relative_path"]
        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.write_bytes(payload)
        asset["checksum"] = hashlib.sha256(payload).hexdigest()
        asset["relative_path"] = job["output"]["relative_path"]
        asset = transition_asset(asset, "completed")
        project["assets"][index] = asset
        job["status"] = "completed"


def _xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    return [
        ["".join(cell.itertext()) for cell in row.findall(f"{{{MAIN_NS}}}c")]
        for row in sheet.findall(f".//{{{MAIN_NS}}}row")
    ]


def test_extracts_the_complete_original_novel_fixture() -> None:
    raw = NOVEL_FIXTURE.read_bytes()
    extracted = extract_text(NOVEL_FIXTURE)

    assert extracted == raw.decode("utf-8").rstrip()
    assert extracted.startswith("# 《回声剑宴》")
    assert "## 第一章　雨夜来客" in extracted
    assert "## 第二章　空房回声" in extracted
    assert extracted.endswith(
        "悬念：真正的回声剑有两柄，而藏在房间里的人究竟是谁？"
    )
    assert extracted.index("## 第一章") < extracted.index("## 第二章")
    assert len(raw) == NOVEL_FIXTURE.stat().st_size
    assert len(raw) > 1_000
    assert sha256_file(NOVEL_FIXTURE) == hashlib.sha256(raw).hexdigest()
    assert len(extracted) > 600


def test_runs_the_complete_deterministic_skill_workflow(tmp_path: Path) -> None:
    extracted = extract_text(NOVEL_FIXTURE)
    project = _derived_project(extracted)
    project_root = tmp_path / "fixture-project"
    project_root.mkdir()
    project_path = project_root / "project.json"
    assert project["source"]["sha256"] == hashlib.sha256(
        NOVEL_FIXTURE.read_bytes()
    ).hexdigest()
    assert project["source"]["character_count"] == len(extracted)
    assert project["source"]["coverage"] == 1.0

    jobs = build_media_jobs(project)
    repeated_jobs = build_media_jobs(copy.deepcopy(project))
    canonical_jobs = json.dumps(jobs, ensure_ascii=False, sort_keys=True).encode("utf-8")
    repeated_bytes = json.dumps(repeated_jobs, ensure_ascii=False, sort_keys=True).encode("utf-8")
    assert repeated_jobs == jobs
    assert repeated_bytes == canonical_jobs
    assert [job["kind"] for job in jobs] == [
        "style_reference",
        "character_sheet",
        "character_sheet",
        "character_sheet",
        "expression_sheet",
        "expression_sheet",
        "action_sheet",
        "action_sheet",
        "scene_sheet",
        "prop_sheet",
        "food_image",
        "voice_sample",
        "voice_sample",
        "shot_sample",
        "dialogue_audio",
        "dialogue_audio",
    ]
    assert {job["owner_id"] for job in jobs if job["kind"] == "character_sheet"} == {
        "C001",
        "C002",
        "C003",
    }
    first_episode_media = {
        job.get("episode_id")
        for job in jobs
        if job["kind"] in {"shot_sample", "dialogue_audio"}
    }
    assert first_episode_media == {"E001"}
    assert len(resumable_jobs(jobs)) == len(jobs)
    interrupted_jobs = copy.deepcopy(jobs)
    interrupted_jobs[0]["status"] = "completed"
    assert interrupted_jobs[0]["job_id"] not in {
        job["job_id"] for job in resumable_jobs(interrupted_jobs)
    }

    _materialize_jobs(project, project_root, jobs)
    assert resumable_jobs(jobs) == []
    assert build_media_jobs(project) == []

    project = create_redo_asset(project, "AUD_C001", "集成测试局部重做")
    redo_jobs = build_media_jobs(project)
    assert [(job["kind"], job["asset_id"], job["version"]) for job in redo_jobs] == [
        ("voice_sample", "AUD_C001", 2)
    ]
    _materialize_jobs(project, project_root, redo_jobs)
    project["generation_runs"][-1]["status"] = "completed"
    project["project"]["status"] = "completed"

    assert build_media_jobs(project) == []
    assert project["project"]["status"] == "completed"
    assert all(asset["status"] == "completed" for asset in project["assets"])
    assert validate_project(project) == []
    assert validate_references(project) == []
    assert len(project["episodes"]) == 3
    assert all(episode["shots"] for episode in project["episodes"])
    assert {
        asset["owner_id"]
        for asset in project["assets"]
        if asset["asset_type"] == "shot_sample"
    } == {"E001_SH001"}
    assert {
        asset["owner_id"]
        for asset in project["assets"]
        if asset["asset_type"] == "voice_sample" and asset["status"] == "completed"
    } == {"C001", "C002"}
    dialogue_assets = [
        asset for asset in project["assets"] if asset["asset_type"] == "dialogue_audio"
    ]
    assert len(dialogue_assets) == 2
    assert {asset["speaker_id"] for asset in dialogue_assets} == {"C001", "C002"}
    assert all(asset["owner_id"].startswith("E001_SH") for asset in dialogue_assets)
    assert len({asset["relative_path"] for asset in dialogue_assets}) == 2
    assert not any(asset["owner_id"] == "P002" for asset in project["assets"])
    assert "普通木杯@" not in "\n".join(
        shot["prompt_zh"]
        for episode in project["episodes"]
        for shot in episode["shots"]
    )
    assert {"P001", "F001"} <= {
        asset["owner_id"]
        for asset in project["assets"]
        if asset["asset_type"] in {"prop_sheet", "food_image"}
    }

    for asset in project["assets"]:
        if asset["status"] != "completed":
            continue
        media_path = project_root / asset["relative_path"]
        assert media_path.is_file()
        assert sha256_file(media_path) == asset["checksum"]

    atomic_write_json(project_path, project)
    source_project_bytes = project_path.read_bytes()
    export_dir = tmp_path / "formal-export"
    outputs = export_package(project_path, export_dir)
    first_export_bytes = {key: path.read_bytes() for key, path in outputs.items()}

    assert set(outputs) == FORMAL_OUTPUT_KEYS
    assert len(first_export_bytes) == 5
    prompts = outputs["prompts"].read_text(encoding="utf-8")
    assert "林岚@角色_C001_林岚_综合设定图_V001推门进入" in prompts
    assert all(f"## E{number:03d}" in prompts for number in (1, 2, 3))
    rows = _xlsx_rows(outputs["shots_xlsx"])
    assert rows[0][:4] == ["剧集ID", "镜头ID", "角色ID", "场景ID"]
    assert [row[1] for row in rows[1:]] == ["E001_SH001", "E002_SH001", "E003_SH001"]
    manifest = json.loads(outputs["media_manifest"].read_text(encoding="utf-8"))
    assert len(manifest["assets"]) == len(project["assets"])
    for record in manifest["assets"]:
        identity = (record["asset_id"], record["version"])
        asset = next(
            item
            for item in project["assets"]
            if (item["asset_id"], item["version"]) == identity
        )
        assert record["checksum"] == asset["checksum"]
        assert record["relative_path"] == asset["relative_path"]
    report = outputs["validation_report"].read_text(encoding="utf-8")
    assert "Errors: 0" in report
    assert not any(
        path.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}
        for path in tmp_path.rglob("*")
    )

    second_outputs = export_package(project_path, export_dir)
    second_export_bytes = {key: path.read_bytes() for key, path in second_outputs.items()}
    assert second_export_bytes == first_export_bytes
    assert list(tmp_path.glob(".formal-export.previous-*"))
    assert project_path.read_bytes() == source_project_bytes
