from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from build_media_jobs import build_media_jobs
from validate_references import _scan_tokens, validate_references


ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "novel-to-ai-drama-pack"
OUTPUT_CONTRACT = SKILL_DIR / "references" / "output-contract.md"
VOICE_RULES = SKILL_DIR / "references" / "voice-rules.md"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "valid-project.json"

EXPECTED_SHOT_TOKENS = {
    "@角色_C001_林岚_综合设定图_V001",
    "@场景_S001_酒楼包厢_场景设定图_V001",
    "@道具_P001_青铜长剑_道具设定图_V001",
    "@食物_F001_莲花酥_食物设定图_V001",
}


def _documented_shot() -> dict[str, Any]:
    text = OUTPUT_CONTRACT.read_text(encoding="utf-8")
    match = re.search(r"### 镜头\n\n```json\n(\{.*?\})\n```", text, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def test_own_skill_script_paths_are_prefixed_with_python3() -> None:
    markdown_files = [SKILL_DIR / "SKILL.md", *SKILL_DIR.glob("references/*.md")]
    invocation = re.compile(r'"\$SKILL_DIR/scripts/[A-Za-z0-9_-]+\.py"')

    for path in markdown_files:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if invocation.search(line):
                assert 'python3 "$SKILL_DIR/scripts/' in line, (
                    f"{path}:{line_number} has a non-executable own-script reference"
                )


def test_documented_shot_has_exact_valid_inline_token_contract() -> None:
    shot = _documented_shot()
    required_pairs = {
        "林岚": "@角色_C001_林岚_综合设定图_V001",
        "酒楼包厢": "@场景_S001_酒楼包厢_场景设定图_V001",
        "青铜长剑": "@道具_P001_青铜长剑_道具设定图_V001",
        "莲花酥": "@食物_F001_莲花酥_食物设定图_V001",
    }

    for field in ("prompt_zh", "prompt_en"):
        prompt = shot[field]
        occurrences = _scan_tokens(prompt, EXPECTED_SHOT_TOKENS)
        assert all(item.known for item in occurrences)
        assert [item.token for item in occurrences] == list(
            dict.fromkeys(item.token for item in occurrences)
        )
        assert {item.token for item in occurrences} == EXPECTED_SHOT_TOKENS
        for name, token in required_pairs.items():
            assert f"{name}{token}" in prompt

    project = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    project["episodes"][0]["shots"][0] = shot
    assert validate_references(project) == []
    build_media_jobs(project)


def test_voice_rules_do_not_require_extra_minor_or_cameo_samples() -> None:
    text = VOICE_RULES.read_text(encoding="utf-8")

    assert "所有对白都使用发言人的 `voice_profile`" in text
    assert "`minor`/`cameo` 不默认创建 `voice_sample`" in text
    assert "直接使用其 `voice_profile` 与项目内置声线生成" in text
    assert "已显式登记 `voice_sample`" in text
