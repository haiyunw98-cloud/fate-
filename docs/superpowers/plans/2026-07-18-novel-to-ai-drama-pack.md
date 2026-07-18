# Novel to AI Drama Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and install a Codex Skill that converts a complete novel into a validated AI short-drama production package, generates core image and voice assets, and produces three episodes of scripts and inline-`@` shot prompts without generating video.

**Architecture:** A concise `SKILL.md` orchestrates Codex, the built-in image generator, and the existing speech CLI. Python standard-library scripts own deterministic work: source extraction, canonical `project.json` I/O, validation, media-job ordering, resume guards, and export. `project.json` remains the only fact source; generated Markdown, Excel, prompts, images, and audio are derived artifacts.

**Tech Stack:** Python 3.11+, pytest, JSON, ZIP/XML/HTML parsing from the Python standard library, optional `pypdf`, built-in `image_gen`, OpenAI speech CLI, Git.

---

## File map

Create the following focused files:

```text
skills/novel-to-ai-drama-pack/
├── SKILL.md                              # Orchestration workflow and hard gates
├── agents/openai.yaml                    # Codex UI metadata
├── assets/project-template.json          # Canonical empty project shape
├── references/output-contract.md         # Canonical field contract
├── references/adaptation-rules.md        # Full-book and short-drama rules
├── references/image-prompt-rules.md      # Character/scene/prop/food image rules
├── references/shot-prompt-rules.md       # Inline @ syntax and shot prompt rules
├── references/voice-rules.md              # Voice profiles and TTS rules
├── scripts/project_io.py                 # JSON load, atomic write, hashes, IDs
├── scripts/extract_source.py             # TXT/MD/DOCX/EPUB/PDF extraction
├── scripts/validate_project.py           # Canonical structure and asset validation
├── scripts/validate_references.py        # Inline @ reference validation
├── scripts/build_media_jobs.py           # Dependency-ordered image/audio job manifest
├── scripts/workflow_guard.py             # State transitions, resume, scoped redo
├── scripts/xlsx_writer.py                # Reused dependency-free XLSX writer
└── scripts/export_package.py             # Markdown/XLSX/prompt export
tests/
├── conftest.py
├── fixtures/sample-novel.md
├── fixtures/valid-project.json
├── test_project_io.py
├── test_extract_source.py
├── test_validate_project.py
├── test_validate_references.py
├── test_build_media_jobs.py
├── test_workflow_guard.py
├── test_export_package.py
└── test_skill_integration.py
```

## Task 1: Initialize the Skill package

**Files:**
- Create: `skills/novel-to-ai-drama-pack/SKILL.md`
- Create: `skills/novel-to-ai-drama-pack/agents/openai.yaml`
- Create: `skills/novel-to-ai-drama-pack/assets/`
- Create: `skills/novel-to-ai-drama-pack/references/`
- Create: `skills/novel-to-ai-drama-pack/scripts/`

- [ ] **Step 1: Initialize with the official scaffold**

Run:

```bash
python /Users/why/.codex/skills/.system/skill-creator/scripts/init_skill.py \
  novel-to-ai-drama-pack \
  --path /Users/why/Documents/小说转ai视频/skills \
  --resources scripts,references,assets \
  --interface display_name="小说转 AI 短剧素材包" \
  --interface short_description="将完整小说转换为短剧剧本、资产图片、声音和分镜提示词" \
  --interface default_prompt="读取完整小说，建立统一资产库，生成短剧素材包并校验所有引用。"
```

Expected: the command creates `skills/novel-to-ai-drama-pack/` and exits with code 0.

- [ ] **Step 2: Replace the generated frontmatter with a valid trigger description**

Write the beginning of `SKILL.md` exactly as:

```markdown
---
name: novel-to-ai-drama-pack
description: 将 TXT、Markdown、DOCX、EPUB、PDF 或粘贴的完整小说转换为可生产的 AI 短剧素材包，实际生成核心人物、场景、重要物品、菜肴和主要角色声音资产，并输出全剧规划、前三集剧本、句内 @参考图 的分镜提示词及第一集示范画面。用户提出小说转短剧、AI 漫剧、角色场景资产、小说视频化素材包或短剧分镜提示词时使用；不生成视频文件。
---
```

- [ ] **Step 3: Validate the scaffold**

Run:

```bash
python /Users/why/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/novel-to-ai-drama-pack
```

Expected: `Skill is valid!`

- [ ] **Step 4: Commit**

```bash
git add skills/novel-to-ai-drama-pack
git commit -m "feat: scaffold novel drama pack skill"
```

## Task 2: Add canonical project I/O and template

**Files:**
- Create: `skills/novel-to-ai-drama-pack/scripts/project_io.py`
- Create: `skills/novel-to-ai-drama-pack/assets/project-template.json`
- Create: `tests/conftest.py`
- Create: `tests/test_project_io.py`

- [ ] **Step 1: Make the hyphenated Skill scripts importable in tests**

Create `tests/conftest.py`:

```python
import sys
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "novel-to-ai-drama-pack" / "scripts"
sys.path.insert(0, str(SCRIPTS))
```

- [ ] **Step 2: Write failing I/O tests**

Create `tests/test_project_io.py`:

```python
import json
from pathlib import Path

from project_io import atomic_write_json, next_version, sha256_file


def test_atomic_write_round_trip(tmp_path: Path):
    path = tmp_path / "project.json"
    atomic_write_json(path, {"schema_version": "1.0.0", "title": "测试"})
    assert json.loads(path.read_text(encoding="utf-8"))["title"] == "测试"
    assert not list(tmp_path.glob("*.tmp"))


def test_sha256_and_version(tmp_path: Path):
    source = tmp_path / "source.txt"
    source.write_text("abc", encoding="utf-8")
    assert sha256_file(source) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert next_version([], "char_img_c001") == 1
    assert next_version([{"asset_id": "char_img_c001", "version": 2}], "char_img_c001") == 3
```

- [ ] **Step 3: Run tests and verify failure**

Run: `pytest tests/test_project_io.py -v`

Expected: FAIL because `project_io` does not exist.

- [ ] **Step 4: Implement project I/O**

Create `project_io.py`:

```python
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("project root must be an object")
    return value


def next_version(assets: list[dict[str, Any]], asset_id: str) -> int:
    versions = [item["version"] for item in assets if item.get("asset_id") == asset_id]
    return max(versions, default=0) + 1
```

- [ ] **Step 5: Add the complete empty canonical template**

Create `project-template.json` with exactly these top-level keys and defaults:

```json
{
  "schema_version": "1.0.0",
  "project": {"project_id": "PRJ001", "title": "未命名项目", "status": "draft", "target_episode_count": 1},
  "source": {"input_type": "pasted_text", "file_name": null, "sha256": "uninitialized", "character_count": 0, "chapter_index": [], "coverage": 0.0},
  "analysis": {"world_bible": "未生成", "timeline": [], "story_arc": [], "adaptation_decisions": []},
  "characters": [],
  "scenes": [],
  "props": [],
  "foods": [],
  "episodes": [],
  "assets": [],
  "generation_settings": {"aspect_ratio": "9:16", "image_provider": "built_in_image_gen", "voice_provider": "openai_speech", "sample_episode_count": 1, "script_episode_count": 3},
  "generation_runs": []
}
```

- [ ] **Step 6: Run tests and commit**

Run: `pytest tests/test_project_io.py -v`

Expected: 2 tests PASS.

```bash
git add skills/novel-to-ai-drama-pack/scripts/project_io.py skills/novel-to-ai-drama-pack/assets/project-template.json tests/conftest.py tests/test_project_io.py
git commit -m "feat: add canonical project storage"
```

## Task 3: Implement complete source extraction

**Files:**
- Create: `skills/novel-to-ai-drama-pack/scripts/extract_source.py`
- Create: `tests/test_extract_source.py`

- [ ] **Step 1: Write failing extraction tests**

Test UTF-8 TXT, Markdown, minimal DOCX, and minimal EPUB. Build DOCX/EPUB fixtures inside the test with `zipfile.ZipFile`; assert normalized text contains the first and last chapter markers. Add a test asserting unsupported extensions raise `SourceExtractionError("unsupported source type: .rtf")`.

Core assertions:

```python
def test_extracts_markdown(tmp_path):
    path = tmp_path / "novel.md"
    path.write_text("# 第一章\n开端\n# 第二章\n结局", encoding="utf-8")
    assert extract_text(path).endswith("结局")


def test_rejects_unknown_extension(tmp_path):
    path = tmp_path / "novel.rtf"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(SourceExtractionError, match=r"unsupported source type: \.rtf"):
        extract_text(path)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/test_extract_source.py -v`

Expected: FAIL because `extract_source` does not exist.

- [ ] **Step 3: Implement extraction**

Implement these exact public interfaces:

```python
class SourceExtractionError(RuntimeError):
    pass


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    handlers = {
        ".txt": _extract_plain,
        ".md": _extract_plain,
        ".markdown": _extract_plain,
        ".docx": _extract_docx,
        ".epub": _extract_epub,
        ".pdf": _extract_pdf,
    }
    if suffix not in handlers:
        raise SourceExtractionError(f"unsupported source type: {suffix}")
    text = _normalize(handlers[suffix](path))
    if not text.strip():
        raise SourceExtractionError("extracted source is empty")
    return text
```

Implementation requirements:

- `_extract_plain` tries `utf-8-sig`, `utf-8`, then `gb18030` and reports all failures as an encoding error.
- `_extract_docx` reads `word/document.xml`, converts paragraph boundaries to newlines, and strips XML tags with `xml.etree.ElementTree`.
- `_extract_epub` reads `META-INF/container.xml`, resolves the OPF spine, then converts each referenced XHTML body to text in spine order.
- `_extract_pdf` imports `pypdf.PdfReader` lazily. If unavailable, raise `SourceExtractionError("PDF extraction requires pypdf")`; if all pages are blank, raise `SourceExtractionError("PDF contains no extractable text; OCR is required")`.
- `_normalize` converts CRLF/CR to LF, trims trailing spaces, collapses more than three blank lines to two, and preserves Chinese punctuation.
- CLI usage is `python extract_source.py INPUT --output OUTPUT`; refuse when input and output resolve to the same file.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_extract_source.py -v`

Expected: all extraction tests PASS.

```bash
git add skills/novel-to-ai-drama-pack/scripts/extract_source.py tests/test_extract_source.py
git commit -m "feat: extract supported novel formats"
```

## Task 4: Validate canonical projects and asset versions

**Files:**
- Create: `skills/novel-to-ai-drama-pack/scripts/validate_project.py`
- Create: `tests/fixtures/valid-project.json`
- Create: `tests/test_validate_project.py`

- [ ] **Step 1: Create a minimal valid fixture**

The fixture must contain one lead character `C001`, one scene `S001`, one important prop `P001`, one food `F001`, three episodes `E001`–`E003`, and at least one shot per episode. Register completed character-sheet, expression-sheet, action-sheet, scene, prop, food, episode-1 shot-sample, and `AUD_C001` voice-sample assets. Use registered `reference_token` values without file extensions, for example `@角色_C001_林岚_综合设定图_V001`. Episodes 2 and 3 contain scripts and prompts but no shot-sample images.

- [ ] **Step 2: Write failing validation tests**

```python
def test_valid_fixture_has_no_errors(valid_project):
    assert validate_project(valid_project) == []


def test_duplicate_object_id_is_rejected(valid_project):
    valid_project["characters"].append(dict(valid_project["characters"][0]))
    assert any("duplicate object id: C001" in error for error in validate_project(valid_project))


def test_asset_filename_must_match_version(valid_project):
    valid_project["assets"][0]["file_name"] = "角色_C001_林岚_综合设定图_V002.png"
    assert any("asset version does not match file name" in error for error in validate_project(valid_project))
```

- [ ] **Step 3: Run tests and verify failure**

Run: `pytest tests/test_validate_project.py -v`

Expected: FAIL because `validate_project` is missing.

- [ ] **Step 4: Implement the validator**

Expose:

```python
REQUIRED_TOP_LEVEL = {
    "schema_version", "project", "source", "analysis", "characters", "scenes",
    "props", "foods", "episodes", "assets", "generation_settings", "generation_runs",
}


def validate_project(data: dict) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_TOP_LEVEL - data.keys()
    extra = data.keys() - REQUIRED_TOP_LEVEL
    errors.extend(f"missing top-level key: {key}" for key in sorted(missing))
    errors.extend(f"unexpected top-level key: {key}" for key in sorted(extra))
    if missing:
        return errors
    _validate_object_ids(data, errors)
    _validate_episode_ids(data, errors)
    _validate_assets(data, errors)
    _validate_owner_references(data, errors)
    _validate_required_media(data, errors)
    return errors
```

Validation rules:

- IDs match `C\d{3}`, `S\d{3}`, `P\d{3}`, `F\d{3}`, `E\d{3}`, and `E\d{3}_SH\d{3}`.
- Object IDs are unique across their own collections; asset `(asset_id, version)` pairs and `reference_token` values are globally unique.
- Asset versions start at 1 and are positive integers.
- `_VNNN` in `file_name` and `reference_token` equals the numeric `version`.
- Asset owner IDs resolve to the collection required by `asset_type`.
- `completed` assets have a non-empty relative path, 64-character lowercase SHA-256, and a file name with an allowed image/audio extension.
- Lead and major characters require a character sheet and voice sample; lead/major characters additionally require expression and action sheets before the core-asset stage can be confirmed.
- Important scenes, props, and foods require their respective images. Ordinary small props are not added to `props`.
- Only episode 1 requires shot sample images; episodes 1–3 require scripts and complete shot prompts.

Add a CLI `main()` that reads one project path, prints each validation error on its own line, and exits 1 when errors exist or prints `VALID` and exits 0 when no errors exist.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_validate_project.py -v`

Expected: all validator tests PASS.

```bash
git add skills/novel-to-ai-drama-pack/scripts/validate_project.py tests/fixtures/valid-project.json tests/test_validate_project.py
git commit -m "feat: validate project and media assets"
```

## Task 5: Enforce sentence-inline `@` references

**Files:**
- Create: `skills/novel-to-ai-drama-pack/scripts/validate_references.py`
- Create: `tests/test_validate_references.py`

- [ ] **Step 1: Write failing reference tests**

```python
def test_inline_character_and_scene_references_pass(valid_project):
    errors = validate_references(valid_project)
    assert errors == []


def test_detached_reference_fails(valid_project):
    shot = valid_project["episodes"][0]["shots"][0]
    shot["prompt_zh"] = "@角色_C001_林岚_综合设定图_V001 林岚推门进入房间。"
    errors = validate_references(valid_project)
    assert any("C001 reference must immediately follow 林岚" in error for error in errors)


def test_missing_food_reference_fails(valid_project):
    shot = valid_project["episodes"][0]["shots"][0]
    shot["prompt_zh"] = shot["prompt_zh"].replace("莲花酥@菜肴_F001_莲花酥_标准图_V001", "莲花酥")
    assert any("F001 missing inline reference" in error for error in validate_references(valid_project))
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/test_validate_references.py -v`

Expected: FAIL because `validate_references` is missing.

- [ ] **Step 3: Implement exact inline matching**

Use registered tokens instead of a permissive free-form regex:

```python
def validate_references(data: dict) -> list[str]:
    errors: list[str] = []
    tokens = {asset["reference_token"]: asset for asset in data["assets"] if asset["status"] == "completed"}
    characters = {item["character_id"]: item for item in data["characters"]}
    scenes = {item["scene_id"]: item for item in data["scenes"]}
    props = {item["prop_id"]: item for item in data["props"]}
    foods = {item["food_id"]: item for item in data["foods"]}

    for episode in data["episodes"]:
        for shot in episode["shots"]:
            for field in ("prompt_zh", "prompt_en"):
                prompt = shot[field]
                _reject_unknown_tokens(prompt, tokens, shot["shot_id"], field, errors)
                for character_id in shot["character_ids"]:
                    item = characters[character_id]
                    token = _active_token(data, character_id, "character_sheet", shot["shot_id"])
                    _require_inline(prompt, item["name"], token, character_id, shot["shot_id"], field, errors)
                scene = scenes[shot["scene_id"]]
                token = _active_token(data, shot["scene_id"], "scene_sheet", shot["shot_id"])
                _require_inline(prompt, scene["name"], token, shot["scene_id"], shot["shot_id"], field, errors)
                for prop_id in shot["prop_ids"]:
                    item = props[prop_id]
                    token = _active_token(data, prop_id, "prop_sheet", shot["shot_id"])
                    _require_inline(prompt, item["name"], token, prop_id, shot["shot_id"], field, errors)
                for food_id in shot["food_ids"]:
                    item = foods[food_id]
                    token = _active_token(data, food_id, "food_image", shot["shot_id"])
                    _require_inline(prompt, item["name"], token, food_id, shot["shot_id"], field, errors)
    return errors
```

Helper behavior:

- `_require_inline` searches for the exact adjacent substring `name + token`; whitespace between name and `@` is invalid.
- `_active_token` resolves the highest completed version whose optional `episode_range` covers the shot episode.
- `_reject_unknown_tokens` extracts every `@` token ending before whitespace or Chinese/English punctuation and rejects tokens not present in the asset registry.
- English prompts retain Chinese names and identical `@` tokens; narrative English may follow them.
- Expression/action references are optional unless `shot.expression_asset_id` or `shot.action_asset_id` is populated; when populated, validate them after the same character name.

Add a CLI `main()` with the same exit contract as `validate_project.py`: one project path, errors to stdout with exit 1, otherwise `VALID` with exit 0.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_validate_references.py -v`

Expected: all inline reference tests PASS.

```bash
git add skills/novel-to-ai-drama-pack/scripts/validate_references.py tests/test_validate_references.py
git commit -m "feat: enforce inline asset references"
```

## Task 6: Build dependency-ordered media jobs

**Files:**
- Create: `skills/novel-to-ai-drama-pack/scripts/build_media_jobs.py`
- Create: `tests/test_build_media_jobs.py`

- [ ] **Step 1: Write failing media-order tests**

Assert the order is style → character sheets → expression/action sheets → scene sheets → prop/food images → voice samples → episode 1 shot samples/dialogue. Assert no shot image job exists for episode 2.

```python
def test_media_jobs_follow_dependency_order(valid_project):
    jobs = build_media_jobs(valid_project)
    kinds = [job["kind"] for job in jobs]
    assert kinds.index("style_reference") < kinds.index("character_sheet")
    assert kinds.index("character_sheet") < kinds.index("expression_sheet")
    assert kinds.index("scene_sheet") < kinds.index("shot_sample")
    assert all(job.get("episode_id") != "E002" for job in jobs if job["kind"] == "shot_sample")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/test_build_media_jobs.py -v`

Expected: FAIL because `build_media_jobs` is missing.

- [ ] **Step 3: Implement the job builder**

Expose `build_media_jobs(data: dict) -> list[dict]` and `write_jobs(project_path: Path, output_path: Path)`. Each job contains:

```json
{
  "job_id": "JOB_IMG_C001_SHEET_V001",
  "kind": "character_sheet",
  "owner_id": "C001",
  "asset_id": "IMG_C001_SHEET",
  "version": 1,
  "prompt": "production-ready prompt",
  "reference_tokens": ["@风格_ST001_写实电影风_V001"],
  "depends_on": ["JOB_IMG_ST001_V001"],
  "status": "pending"
}
```

Implementation rules:

- Build one job per missing required asset; never rebuild completed assets.
- Character expression/action jobs depend on the completed or scheduled character sheet.
- Shot sample jobs collect exact active character, scene, prop, food, expression, and action tokens from the shot.
- Voice jobs use `voice_profile`, exact sample text, one built-in voice, and a 4–8 line delivery instruction block.
- First-episode dialogue audio jobs are one file per dialogue line and depend on the speaker voice profile.
- Jobs are topologically sorted; a cycle raises `MediaJobError` with the participating job IDs.
- Write the manifest atomically beside `project.json` as `media-jobs.json`.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_build_media_jobs.py -v`

Expected: all media job tests PASS.

```bash
git add skills/novel-to-ai-drama-pack/scripts/build_media_jobs.py tests/test_build_media_jobs.py
git commit -m "feat: build ordered media generation jobs"
```

## Task 7: Add resume and scoped-redo guards

**Files:**
- Create: `skills/novel-to-ai-drama-pack/scripts/workflow_guard.py`
- Create: `tests/test_workflow_guard.py`

- [ ] **Step 1: Write failing workflow tests**

Cover legal transitions, refusal to overwrite completed versions, resume returning only unfinished jobs, and redo producing the next version while preserving the old asset.

```python
def test_completed_asset_requires_new_version(valid_project):
    asset = valid_project["assets"][0]
    with pytest.raises(WorkflowGuardError, match="completed assets are immutable"):
        transition_asset(asset, "generating")


def test_resume_returns_only_pending_or_redo_jobs():
    jobs = [{"job_id": "A", "status": "completed"}, {"job_id": "B", "status": "pending"}]
    assert [job["job_id"] for job in resumable_jobs(jobs)] == ["B"]
```

- [ ] **Step 2: Run tests and verify failure**

Run: `pytest tests/test_workflow_guard.py -v`

Expected: FAIL because `workflow_guard` is missing.

- [ ] **Step 3: Implement guarded transitions**

```python
ALLOWED_TRANSITIONS = {
    "draft": {"confirmed", "discarded"},
    "confirmed": {"generating", "discarded"},
    "generating": {"completed", "redo"},
    "redo": {"confirmed", "discarded"},
    "completed": set(),
    "discarded": set(),
}


def transition_asset(asset: dict, target: str) -> dict:
    current = asset["status"]
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        if current == "completed":
            raise WorkflowGuardError("completed assets are immutable; create a new version")
        raise WorkflowGuardError(f"illegal asset transition: {current} -> {target}")
    updated = dict(asset)
    updated["status"] = target
    return updated


def resumable_jobs(jobs: list[dict]) -> list[dict]:
    return [job for job in jobs if job["status"] in {"pending", "redo"}]
```

Add `create_redo_asset(data, asset_id, reason)` that copies immutable metadata, increments the version, clears path/checksum, sets `parent_asset_ids` to the prior version, and appends a run record containing the exact reason and old prompt. It must not edit unrelated assets or completed episodes.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_workflow_guard.py -v`

Expected: all workflow tests PASS.

```bash
git add skills/novel-to-ai-drama-pack/scripts/workflow_guard.py tests/test_workflow_guard.py
git commit -m "feat: guard resume and asset redo"
```

## Task 8: Export the production package

**Files:**
- Create: `skills/novel-to-ai-drama-pack/scripts/xlsx_writer.py`
- Create: `skills/novel-to-ai-drama-pack/scripts/export_package.py`
- Create: `tests/test_export_package.py`

- [ ] **Step 1: Reuse the tested dependency-free XLSX writer**

Run:

```bash
cp /Users/why/.codex/skills/novel-to-seedance-pack/scripts/xlsx_writer.py \
  skills/novel-to-ai-drama-pack/scripts/xlsx_writer.py
```

- [ ] **Step 2: Write failing export tests**

```python
def test_export_contains_prompts_and_assets(tmp_path, valid_project_path):
    outputs = export_package(valid_project_path, tmp_path / "export")
    assert outputs["project_md"].exists()
    assert outputs["shots_xlsx"].read_bytes().startswith(b"PK")
    prompts = outputs["prompts"].read_text(encoding="utf-8")
    assert "林岚@角色_C001_林岚_综合设定图_V001" in prompts
    assert "莲花酥@菜肴_F001_莲花酥_标准图_V001" in prompts
```

- [ ] **Step 3: Run tests and verify failure**

Run: `pytest tests/test_export_package.py -v`

Expected: FAIL because `export_package` is missing.

- [ ] **Step 4: Implement atomic export**

Expose:

```python
def export_package(project_path: Path, output_dir: Path) -> dict[str, Path]:
    data = load_json(project_path)
    errors = validate_project(data) + validate_references(data)
    if errors:
        raise ExportError("project validation failed:\n" + "\n".join(errors))
    stage = output_dir.parent / f".{output_dir.name}.staging"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    outputs = _write_all(data, stage)
    _publish_atomically(stage, output_dir)
    return {name: output_dir / path.name for name, path in outputs.items()}
```

`_write_all` creates:

- `project.md`: story bible, asset tables, three episode scripts, voice profiles, warnings, and completion state.
- `shots.xlsx`: one row per shot with IDs, characters, scene, dialogue, camera, prompt ZH/EN, negative prompt, and exact reference tokens.
- `video-prompts.txt`: copy-ready prompts grouped by episode and shot.
- `media-manifest.json`: all assets, versions, checksums, and relative paths.
- `validation-report.txt`: zero errors plus non-blocking warnings.

Do not delete an existing unknown export directory. Publish into a versioned sibling when the destination is not an export previously managed by this Skill.

Add a CLI `main()` accepting `PROJECT_JSON --output-dir DIRECTORY`; print the absolute path of every published file and exit 0 only after validation and atomic publication succeed.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_export_package.py -v`

Expected: all export tests PASS.

```bash
git add skills/novel-to-ai-drama-pack/scripts/xlsx_writer.py skills/novel-to-ai-drama-pack/scripts/export_package.py tests/test_export_package.py
git commit -m "feat: export drama production package"
```

## Task 9: Write the Skill workflow and reference contracts

**Files:**
- Modify: `skills/novel-to-ai-drama-pack/SKILL.md`
- Modify: `skills/novel-to-ai-drama-pack/agents/openai.yaml`
- Create: `skills/novel-to-ai-drama-pack/references/output-contract.md`
- Create: `skills/novel-to-ai-drama-pack/references/adaptation-rules.md`
- Create: `skills/novel-to-ai-drama-pack/references/image-prompt-rules.md`
- Create: `skills/novel-to-ai-drama-pack/references/shot-prompt-rules.md`
- Create: `skills/novel-to-ai-drama-pack/references/voice-rules.md`

- [ ] **Step 1: Write `SKILL.md` as a concise orchestrator**

Its body must contain these ordered hard gates:

1. Read all five reference files before processing a novel.
2. Copy the source, compute SHA-256, extract all text, and verify first/last chapters.
3. Read the whole novel before creating episode shots.
4. Build story bible, complete episode outline, and canonical IDs first.
5. Generate style → character → expression/action → scene → prop/food → episode-1 sample images using the built-in image generator; save final project assets into the workspace and register checksums.
6. Generate one approximately 20-second AI voice sample for each lead/major character and complete first-episode dialogue audio using the speech CLI; disclose AI generation.
7. Create exactly three scripted episodes for the initial package and only one episode of sample shot images.
8. Write all shot prompts with inline `name@reference_token` syntax; ordinary small objects use text only.
9. Validate after every batch, resume from incomplete jobs, and never overwrite completed versions.
10. Export only after structure, references, required media, and delivery scope return zero errors.

Include explicit fallback behavior: built-in image generation failure is reported rather than silently switching to an API CLI; missing speech API credentials leave voice jobs incomplete and prevent a false completion claim.

For every local reference image, first inspect it with `view_image`, then pass the actual local reference path to the built-in image generator. The human-facing `@reference_token` in a prompt identifies which registered image must be attached; it is not a substitute for supplying the image to the generation tool.

- [ ] **Step 2: Write focused references**

- `output-contract.md`: document every canonical field, enum, ID pattern, asset type, and sample legal object.
- `adaptation-rules.md`: full-book-first analysis, season/episode pacing, compression, visualizing internal monologue, and continuity review.
- `image-prompt-rules.md`: one character composite sheet, expression/action grids, scene sheets, important props, food images, identity-preserving references, inspection, and versioned saving.
- `shot-prompt-rules.md`: exact inline `@` grammar, active-version selection, bilingual token equality, camera/action structure, and examples.
- `voice-rules.md`: built-in voices, 4–8 line delivery directions, 4096-character limit, batch rate limit, file naming, AI disclosure, and no voice cloning.

- [ ] **Step 3: Regenerate UI metadata**

Run:

```bash
python /Users/why/.codex/skills/.system/skill-creator/scripts/generate_openai_yaml.py \
  skills/novel-to-ai-drama-pack \
  --interface display_name="小说转 AI 短剧素材包" \
  --interface short_description="生成短剧剧本、资产图片、声音和带 @引用的分镜提示词" \
  --interface default_prompt="完整读取这部小说，建立统一编号资产库，生成前三集短剧素材包并实际创建核心图片与声音。"
```

- [ ] **Step 4: Validate and commit**

Run:

```bash
python /Users/why/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/novel-to-ai-drama-pack
```

Expected: `Skill is valid!`

```bash
git add skills/novel-to-ai-drama-pack
git commit -m "docs: define novel drama production workflow"
```

## Task 10: Add deterministic end-to-end tests

**Files:**
- Create: `tests/fixtures/sample-novel.md`
- Create: `tests/test_skill_integration.py`

- [ ] **Step 1: Create a representative short fixture**

Write a two-chapter Chinese story containing one protagonist, one major supporting character, one recurring room, one important weapon, one named dish, and enough events to adapt into three short episodes. End chapter two with an explicit cliffhanger so first/last extraction and episode planning can be asserted.

- [ ] **Step 2: Write the integration test**

The test must:

1. Extract the complete fixture.
2. Load a project derived from `valid-project.json`.
3. Build ordered media jobs.
4. Materialize deterministic fixture media bytes only inside the temporary test directory and calculate real checksums; never place fixture media in production assets.
5. Mark jobs complete through `workflow_guard`.
6. Validate project structure and inline references.
7. Export Markdown, XLSX, prompts, manifest, and validation report.

Final assertions:

```python
assert validate_project(project) == []
assert validate_references(project) == []
assert len(project["episodes"]) == 3
assert all(len(ep["shots"]) > 0 for ep in project["episodes"])
assert all(asset["owner_id"] != "E002" for asset in project["assets"] if asset["asset_type"] == "shot_sample")
assert (export_dir / "shots.xlsx").exists()
```

- [ ] **Step 3: Run the complete deterministic suite**

Run: `pytest -v`

Expected: all tests PASS with no warnings from the Skill package.

- [ ] **Step 4: Run formatting and validation checks**

Run:

```bash
python -m compileall -q skills/novel-to-ai-drama-pack/scripts
python /Users/why/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/novel-to-ai-drama-pack
git diff --check
```

Expected: all commands exit 0; quick validation prints `Skill is valid!`.

- [ ] **Step 5: Commit**

```bash
git add tests
git commit -m "test: cover novel drama pack workflow"
```

## Task 11: Run a real media smoke test and install

**Files:**
- Create: `smoke-projects/sample-drama/project.json`
- Create: `smoke-projects/sample-drama/assets/images/`
- Create: `smoke-projects/sample-drama/assets/audio/`
- Create: `smoke-projects/sample-drama/export/`
- Modify: `smoke-projects/sample-drama/project.json`

- [ ] **Step 1: Generate the smallest real image set**

Use the built-in image generator, one call per distinct asset, to create and inspect:

- one style reference;
- one character composite sheet;
- one scene sheet;
- one important dish image;
- one episode-1 shot sample that references the prior images.

Copy final images from the built-in generated-image location into the smoke project with versioned production filenames. Register their paths, checksums, prompts, parent asset IDs, and exact `reference_token` values in `project.json`. Do not leave project-referenced images outside the workspace.

- [ ] **Step 2: Generate one real voice sample when credentials are available**

Run the existing speech CLI with the smoke character's exact sample text, selected built-in voice, and 4–8 line performance directions. Save WAV output under `assets/audio/`, register checksum and AI-generated disclosure, then listen for intelligibility, pacing, and name pronunciation.

If `OPENAI_API_KEY` is absent, run the speech CLI with `--dry-run`, leave the voice asset in `confirmed` rather than `completed`, and verify that formal package completion fails with the precise missing-media error.

- [ ] **Step 3: Validate and export the smoke project**

Run:

```bash
python skills/novel-to-ai-drama-pack/scripts/validate_project.py smoke-projects/sample-drama/project.json
python skills/novel-to-ai-drama-pack/scripts/validate_references.py smoke-projects/sample-drama/project.json
python skills/novel-to-ai-drama-pack/scripts/export_package.py smoke-projects/sample-drama/project.json --output-dir smoke-projects/sample-drama/export
```

Expected with completed voice media: all commands exit 0 and export files exist. Expected without credentials: image and prompt checks pass, formal delivery validation fails only for incomplete required voice media, and no success claim is made.

- [ ] **Step 4: Install without overwriting an unknown Skill**

Run:

```bash
test ! -e /Users/why/.codex/skills/novel-to-ai-drama-pack
cp -R skills/novel-to-ai-drama-pack /Users/why/.codex/skills/novel-to-ai-drama-pack
python /Users/why/.codex/skills/.system/skill-creator/scripts/quick_validate.py /Users/why/.codex/skills/novel-to-ai-drama-pack
```

Expected: destination did not previously exist, copy succeeds, and installed Skill validates. If the destination exists, stop and compare it; do not overwrite it.

- [ ] **Step 5: Final verification and commit**

Run:

```bash
pytest -v
git status --short
```

Expected: tests PASS; only intended smoke artifacts are untracked or modified. Add only the agreed smoke project artifacts, then commit:

```bash
git add smoke-projects/sample-drama
git commit -m "test: validate real drama media workflow"
```
