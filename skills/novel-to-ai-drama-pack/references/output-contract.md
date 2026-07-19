# 输出与数据契约

## 目录

- [单一事实源](#单一事实源)
- [顶层结构](#顶层结构)
- [项目与原文](#项目与原文)
- [分析与生成设置](#分析与生成设置)
- [人物场景道具和菜肴](#人物场景道具和菜肴)
- [剧集与镜头](#剧集与镜头)
- [资产登记](#资产登记)
- [枚举与编号](#枚举与编号)
- [阶段版本与重做](#阶段版本与重做)
- [正式导出门禁](#正式导出门禁)
- [项目目录与导出](#项目目录与导出)
- [合法对象示例](#合法对象示例)

## 单一事实源

把每个项目的 `project.json` 作为 canonical 单一事实源。只从它派生 Markdown、XLSX、提示词文本、媒体清单和校验报告，不从派生文件反向覆盖项目数据。

从 `assets/project-template.json` 创建新项目。使用 `project_io.atomic_write_json` 原子保存；不中途写入半个 JSON。顶层不得添加脚本不识别的键。

## 顶层结构

`schema_version` 必须是 `1.0.0`。根对象必须且只能包含以下键：

| 键 | 类型 | 用途 |
|---|---|---|
| `schema_version` | string | 数据契约版本 |
| `project` | object | 项目身份、状态和目标集数 |
| `source` | object | 原文来源、哈希、章节和阅读覆盖 |
| `analysis` | object | 故事圣经、时间线、故事弧和改编决策 |
| `characters` | array | 人物档案和声音档案 |
| `scenes` | array | 可持续引用的场景 |
| `props` | array | 需要长期一致的重要道具 |
| `foods` | array | 重要菜肴、特色食物和宴席 |
| `episodes` | array | 分集剧本与镜头 |
| `assets` | array | 已规划或已生成的图片/音频版本 |
| `generation_settings` | object | 画幅、生成器和首批范围 |
| `generation_runs` | array | 生成、失败、续作与重做审计记录 |

## 项目与原文

### `project`

必填字段：

| 字段 | 类型 | 规则 |
|---|---|---|
| `project_id` | string | `PRJ\d{3}` |
| `title` | non-empty string | 项目名 |
| `status` | enum | 见状态枚举 |
| `target_episode_count` | positive integer | 必须等于 `episodes` 长度 |

### `source`

必填字段：

- `input_type`：非空字符串，例如 `txt`、`docx`、`epub`、`pdf`、`markdown` 或 `pasted_text`。
- `file_name`：`null` 或非空字符串。
- `sha256`：非空字符串；正式项目写入原文的 64 位小写 SHA-256。
- `character_count`：大于等于 0 的整数。
- `chapter_index`：数组。记录章节名、顺序和文本范围；当前校验器不强制其内部形状。
- `coverage`：`0.0`–`1.0` 的数值；项目 `completed` 时必须是 `1.0`。

## 分析与生成设置

### `analysis`

- `world_bible`：非空字符串。包含世界规则、阵营、核心冲突、人物锁定和视觉时代。
- `timeline`：数组。记录事件先后、角色年龄/伤势/服装、天气和道具归属变化。
- `story_arc`：数组。记录全剧分季/分集纲、原著章节对应和伏笔回收。
- `adaptation_decisions`：数组。记录合并、前置、可视化和补充设定及理由。

### `generation_settings`

| 字段 | 类型 | 初始包合法值 |
|---|---|---|
| `aspect_ratio` | non-empty string | `9:16` |
| `image_provider` | non-empty string | `built_in_image_gen` |
| `voice_provider` | non-empty string | `openai_speech` |
| `sample_episode_count` | integer | 必须是 `1` |
| `script_episode_count` | positive integer | 正式导出必须是 `3` |

`generation_runs` 必须是对象数组。对每次实际生成、失败、dry-run 或重做，记录稳定的 `run_id`、操作、资产身份/版本、输入引用、提示词、结果或错误、状态和时间。当前校验器只强制“每项为对象”，不要把这些审计建议误作额外必填契约。

## 人物场景道具和菜肴

### `characters[]`

必填：

- `character_id`：`C\d{3}`。
- `name`：非空字符串。
- `importance`：`lead | major | minor | cameo`。
- `role`：非空字符串。
- `appearance`：非空的固定外形描述。
- `voice_profile`：对象，必填非空 `voice`、`tone`、`pace`、`sample_text`。

`lead` 和 `major` 需要综合角色图、表情图、动作图和声音样本。E001–E003 任一镜头出现的 `minor`/`cameo` 也至少需要基础角色图，包括只在 E002/E003 首次出现的角色。当前媒体工作流不为 `minor`/`cameo` 自动生成表情、动作或声音样本；不要在镜头中为它们选择 extra，除非先升级为 `major` 或显式注册并完成匹配当前阶段的附加资产。

### `scenes[]`、`props[]`、`foods[]`

每项必填自己的 ID（`scene_id`、`prop_id` 或 `food_id`）、`name` 和 `importance`。

- 场景重要性：`important | secondary`。
- 道具重要性：`important | secondary`。
- 菜肴重要性：`important | secondary`。
- `important` 场景必须有 `scene_sheet`。
- E001–E003 镜头使用的每个场景都必须有 `scene_sheet`，因此被三集引用的 `secondary` 场景也要建基础图。
- `secondary` 普通物件和普通食物不进入 `prop_ids`/`food_ids`，只在镜头叙事中描述；确实重要时先升级为 `important` 并登记资产。
- `important` 道具必须有 `prop_sheet`。
- `important` 菜肴必须有 `food_image`。
- 普通小物件和普通食物不得为了提示词而人为升级成图片资产；不把它们写入镜头 `prop_ids`/`food_ids`，只写自然语言。

## 剧集与镜头

### `episodes[]`

按数组顺序连续编号。每项必填：

- `episode_id`：`E\d{3}`，第一项必须 `E001`。
- `episode_number`：从 1 开始的连续整数，与数组位置相符。
- `title`：非空字符串。
- `script`：字符串；`production` 和 `completed` 状态下首批指定集必须非空。
- `shots`：非空镜头数组。

初始包正式导出时，前三项必须依次是 E001、E002、E003，且三集都有非空剧本和镜头。

### `shots[]`

每个镜头必填：

- `shot_id`：`E\d{3}_SH\d{3}`，前缀与所属集一致，在全项目唯一。
- `character_ids`：人物 ID 数组；每项必须存在。
- `scene_id`：已存在的场景 ID。
- `prop_ids`：被当作重要资产使用的道具 ID 数组。
- `food_ids`：被当作重要资产使用的菜肴 ID 数组。
- `prompt_zh`、`prompt_en`、`negative_prompt`：字符串；生产状态必须非空。
- `expression_asset_id`、`action_asset_id`：必须显式存在，取 `null` 或已完成且属于本镜头角色的对应资产 ID。

E001 每个镜头在正式导出时还必须显式包含 `dialogue_lines`；无对白时写 `[]`，不得省略。每个对白对象必须有已存在的 `speaker_id` 和非空 `text`。为确保三集字段一致，建议 E002/E003 也显式写 `dialogue_lines`，但当前正式门禁只强制 E001。

`shot_size`、`camera`、`camera_height`、`camera_angle`、`camera_movement`、`lens`、`composition` 是可选摄影字段；导出器会把存在的值汇总到 XLSX，不得把它们宣布为当前校验器必填项。

## 资产登记

### 核心字段

每个 `assets[]` 对象必须按当前脚本契约提供：

- `asset_id`：大写字母开头，仅大写字母、数字和下划线。同一逻辑资产的所有版本共用它。
- `version`：从 1 开始的正整数，同一 `asset_id` 必须连续。
- `asset_type`：见资产类型映射。
- `owner_type`、`owner_id`：必须与资产类型和已存在对象匹配。
- `reference_token`：非空、全局唯一，带对象 ID 和结尾 `_VNNN`，不带文件扩展名。
- `file_name`：非空、带对象 ID 与 `_VNNN.ext`。
- `parent_asset_ids`：资产 ID 数组；每个引用都必须存在。
- `status`：资产状态枚举。

已完成资产还必须有：

- `relative_path`：受限在项目根目录内的非空相对路径，基名与 `file_name` 一致。
- `checksum`：该实体文件的 64 位小写 SHA-256。
- `prompt`：实际生成所用的非空文本。当前字段名是 `prompt`，不是 `generation_prompt`。

### 可选 canonical 资产字段

以下字段只在适用资产上写入，不是所有 `assets[]` 的全局必填项。表中“当前校验”说明 `validate_project.py` 或相关现有脚本是否强制其类型/格式。

| 字段 | 建议类型 | 适用资产 | 语义与当前校验 |
|---|---|---|---|
| `negative_prompt` | string | 图片资产 | 生成时使用的可验证禁止项；当前项目 validator 不强制存在或类型，workflow redo 会在存在时保留。 |
| `episode_range` | two-item integer array | 有换装/年龄/状态阶段的资产 | `[start, end]`，两项为正整数且 `start <= end`；阶段选择、引用校验和媒体工作单会强制该格式。 |
| `redo_parent` | object | 版本 2 及以后的重做资产 | 恰好 `{"asset_id": string, "version": positive integer}`，指向同 `asset_id` 的前一版；当前 validator 强制形状、相邻版本和父版存在。 |
| `stage` | string（建议非空） | 需要人类可读阶段标签的资产 | 例如 `youth`、`battle_damaged`；当前 validator 不强制存在、枚举或类型，活动范围仍以 `episode_range` 为准。 |
| `stage_id` | string（建议非空） | 需要稳定阶段标识的资产 | 用于审计和 UI 关联；当前 validator 不强制。 |
| `stage_name` | string（建议非空） | 需要中文展示名的阶段资产 | 例如“重伤阶段”；当前 validator 不强制。 |
| `stage_metadata` | object | 需要额外阶段说明的资产 | 保存不参与当前活动选择的说明性元数据；当前 validator 不强制存在或内部形状。 |
| `ai_generated` | boolean | AI 生成图片和音频 | 标识媒体来源；当前 validator 不强制，声音工作单会在输入中写 `true`。 |
| `ai_disclosure` | string（建议非空） | 需要对外披露的 AI 媒体，尤其声音 | 保存可对用户展示的 AI 生成说明；当前 validator 不强制。 |
| `content_fingerprint` | 64-char lowercase hex string | `dialogue_audio` | SHA-256，由 canonical `speaker_id + text` 计算，防止发言人或台词改变后错用旧音频；媒体工作单在字段存在时强制 64 位小写十六进制且与元数据一致。 |
| `speaker_id` | string（建议 `C\d{3}`） | `dialogue_audio` | 该句对白的发言人；工作单使用它建内容指纹并选择声音档案，当前项目 validator 不单独强制该资产字段。 |
| `text` | string（建议非空） | `dialogue_audio` | 实际逐句 TTS 文本，应与所属 `dialogue_lines[].text` 及生成 `prompt` 一致；当前项目 validator 不单独强制。 |
| `line_number` | positive integer | `dialogue_audio` | 当需要显式保留镜头内逐句顺序时使用；当前工作单主要从 `asset_id` 的 `Lnnn` 表达顺序，validator 不强制该字段。 |
| `language` | string（建议 BCP-47 或稳定项目代码） | 声音或多语图片资产 | 记录内容语言；当前 validator 不强制存在、枚举或类型。 |

### 资产类型和所属类型

| `asset_type` | `owner_type` | 所属 ID | 媒体 |
|---|---|---|---|
| `style_reference` | `project` | `PRJnnn` | 图片 |
| `character_sheet` | `character` | `Cnnn` | 图片 |
| `expression_sheet` | `character` | `Cnnn` | 图片 |
| `action_sheet` | `character` | `Cnnn` | 图片 |
| `voice_sample` | `character` | `Cnnn` | 音频 |
| `scene_sheet` | `scene` | `Snnn` | 图片 |
| `prop_sheet` | `prop` | `Pnnn` | 图片 |
| `food_image` | `food` | `Fnnn` | 图片 |
| `shot_sample` | `shot` | `Ennn_SHnnn` | 图片 |
| `dialogue_audio` | `shot` | `Ennn_SHnnn` | 音频 |

图片扩展名只能是 `.png`、`.jpg`、`.jpeg` 或 `.webp`。音频扩展名只能是 `.wav`、`.mp3`、`.aac`、`.flac` 或 `.opus`。

## 枚举与编号

### ID 正则

| 对象 | 格式 | 例子 |
|---|---|---|
| 项目 | `PRJ\d{3}` | `PRJ001` |
| 人物 | `C\d{3}` | `C001` |
| 场景 | `S\d{3}` | `S001` |
| 道具 | `P\d{3}` | `P001` |
| 菜肴 | `F\d{3}` | `F001` |
| 剧集 | `E\d{3}` | `E001` |
| 镜头 | `E\d{3}_SH\d{3}` | `E001_SH001` |
| 资产 | `[A-Z][A-Z0-9_]*` | `CHAR_C001` |

对象 ID 在各自集合唯一，且人物/场景/道具/菜肴 ID 跨集合不重复。资产以 `(asset_id, version)` 唯一，`reference_token` 全局唯一。

### 状态

资产状态：

```text
draft | confirmed | generating | completed | redo | discarded
```

合法迁移：

```text
draft -> confirmed | discarded
confirmed -> generating | discarded
generating -> completed | redo
redo -> confirmed | discarded
completed -> 不可变
discarded -> 不可变
```

项目状态除上述值外，还允许 `core_assets_confirmed` 和 `production`。`core_assets_confirmed`、`production`、`completed` 启用必需媒体门禁；`production`、`completed` 启用剧本和提示词非空门禁。

## 阶段版本与重做

### `episode_range`

对角色换装、年龄或其他阶段性外形，可在资产上写 `[start, end]`。两个值必须是正整数且 `start <= end`。

选择某集的活动资产时：

1. 忽略 `discarded`。
2. 对每个逻辑资产的同一范围选最高版本。
3. 有覆盖当前集数的 `episode_range` 时选该阶段；否则选无范围的全局版本。
4. 同一所属与资产类型的范围不得重叠；多个候选视为歧义并阻断。
5. 表情或动作图的父角色图必须与该集的活动角色图一致。

活动资产是每个逻辑阶段的最高 `non-discarded` 版本，不是“最高已完成版本”。当它处于 `redo`、`confirmed` 或 `generating` 时，允许提示词预先引用它的注册令牌，媒体工作单会依赖该版本并先生成它。实际作为参考图附件和正式导出时，活动版本必须已有真实文件且为 `completed`；不得为了通过该门禁而回退引用同阶段的旧已完成版本。

### `redo_parent`

不改写已完成版本。用 `create_redo_asset` 创建连续的下一版，将 `status` 设为 `redo`，清理路径和哈希，并写入：

```json
"redo_parent": {"asset_id": "CHAR_C001", "version": 1}
```

`redo_parent.asset_id` 必须等于当前 `asset_id`，`redo_parent.version` 必须是当前版本减 1 且该历史版本存在。版本 1 不得带 `redo_parent`。

## 正式导出门禁

仅当以下条件同时成立时导出：

1. `validate_project` 零错误。
2. `validate_references` 零错误。
3. `project.status = completed` 且 `source.coverage = 1.0`。
4. `script_episode_count = 3`，前三集为 E001–E003 且剧本/镜头完整。
5. E001 每个镜头显式有 `dialogue_lines`。
6. 不存在待生成的必需媒体任务。
7. E001–E003 所有镜头人物与场景都有基础图，包括三集才首次出现的 `minor` 人物和 `secondary` 场景；仍只为 E001 生成 `shot_sample`。
8. 每个 `completed` 媒体的相对路径受限在项目内，路径组件和文件都不是符号链接，目标是普通文件。
9. 现场读取实体文件计算的 SHA-256 与 `checksum` 相同；平台无法安全拒绝符号链接时闭合失败。

因缺少 API 凭据而只做了声音 dry-run 时，声音资产不是 `completed`，因此不满足正式导出门禁。

## 项目目录与导出

建议项目目录：

```text
projects/<项目名>/
├── 00_原著文件/
├── 01_小说解析/
├── 02_短剧规划/
├── 03_人物数据库/
├── 04_人物综合设定图/
├── 05_场景数据库与图片/
├── 06_重要物品与菜肴/
├── 07_短剧剧本/
├── 08_AI视频分镜与提示词/
├── 09_第一集示范画面/
├── 10_角色声音与第一集配音/
├── 11_剪辑与发布素材/
├── 12_一致性数据库/
├── assets/
├── project.json
├── media-jobs.json
└── export/
```

正式导出五件：`project.md`、`shots.xlsx`、`video-prompts.txt`、`media-manifest.json`、`validation-report.txt`。导出只更新由本 Skill 标记且物理哈希未被外部修改的完整目录；未知或已变更目录使用 `-vNNN` 兄弟版本，不删除用户文件。

## 合法对象示例

### 人物

```json
{
  "character_id": "C001",
  "name": "林岚",
  "importance": "lead",
  "role": "protagonist",
  "appearance": "青衣束发，目光坚毅",
  "voice_profile": {
    "voice": "清亮女声",
    "tone": "沉稳",
    "pace": "中速",
    "sample_text": "这一次，我不会再退。"
  }
}
```

### 镜头

```json
{
  "shot_id": "E001_SH001",
  "character_ids": ["C001"],
  "scene_id": "S001",
  "prop_ids": ["P001"],
  "food_ids": ["F001"],
  "dialogue_lines": [
    {"speaker_id": "C001", "text": "剑为什么会在这里？"}
  ],
  "prompt_zh": "林岚@角色_C001_林岚_综合设定图_V001走进酒楼包厢@场景_S001_酒楼包厢_场景设定图_V001。",
  "prompt_en": "林岚@角色_C001_林岚_综合设定图_V001 enters 酒楼包厢@场景_S001_酒楼包厢_场景设定图_V001.",
  "negative_prompt": "换脸，多余肢体，文字水印",
  "expression_asset_id": null,
  "action_asset_id": null
}
```

### 已完成角色图

```json
{
  "asset_id": "CHAR_C001",
  "version": 1,
  "asset_type": "character_sheet",
  "owner_type": "character",
  "owner_id": "C001",
  "reference_token": "@角色_C001_林岚_综合设定图_V001",
  "file_name": "CHAR_C001_V001.png",
  "relative_path": "assets/characters/CHAR_C001_V001.png",
  "checksum": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "prompt": "林岚综合角色设定图",
  "parent_asset_ids": ["STYLE_PRJ001"],
  "status": "completed"
}
```

### 重做资产

```json
{
  "asset_id": "CHAR_C001",
  "version": 2,
  "asset_type": "character_sheet",
  "owner_type": "character",
  "owner_id": "C001",
  "reference_token": "@角色_C001_林岚_综合设定图_V002",
  "file_name": "CHAR_C001_V002.png",
  "prompt": "保持五官，修正侧面发饰",
  "parent_asset_ids": ["STYLE_PRJ001"],
  "redo_parent": {"asset_id": "CHAR_C001", "version": 1},
  "status": "redo"
}
```
