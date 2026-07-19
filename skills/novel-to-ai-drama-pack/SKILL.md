---
name: novel-to-ai-drama-pack
description: 将 TXT、Markdown、DOCX、EPUB、PDF 或粘贴的完整小说转换为可生产的 AI 短剧或 AI 漫剧素材包，实际生成人物、场景、重要道具、菜肴图片和主角及主要配角声音，并输出全剧规划、前三集剧本、带句内 @参考图 的分镜提示词及第一集示范画面。用户提出小说转短剧、AI 漫剧、人物场景重要道具或菜肴图片、主要角色声音、前三集素材、带 @资产引用 的分镜提示词或小说视频化素材包时使用；只产出可供视频模型使用的提示词，不生成视频文件。
---

# 小说转 AI 短剧素材包

将 `project.json` 维护为唯一事实源，把 Markdown、XLSX、提示词、图片和音频视为派生交付物。实际创建约定的图片与声音；仅编写视频提示词，绝不生成视频。

从当前已加载的 `novel-to-ai-drama-pack/SKILL.md` 所在目录解析绝对路径 `SKILL_DIR`。以下所有本 Skill 脚本都使用 `"$SKILL_DIR/scripts/..."`，不假设当前工作目录，不硬编码用户主目录或开发仓库路径。

## 必读契约

在处理任何小说前，完整读取以下五个文件，不要按需跳读：

- [输出与数据契约](references/output-contract.md)
- [全书分析与短剧改编规则](references/adaptation-rules.md)
- [图片资产与提示词规则](references/image-prompt-rules.md)
- [分镜与句内 @ 引用规则](references/shot-prompt-rules.md)
- [角色声音与 TTS 规则](references/voice-rules.md)

## 项目约定

- 在工作区内创建项目目录，保留原始小说的未修改副本。
- 从 `assets/project-template.json` 复制初始结构，只在 `project.json` 中记录被确认的设定、版本与状态。
- 使用相对路径登记项目媒体，使用 SHA-256 登记原文和已完成媒体。
- 用新版本重做资产，保留旧文件、`redo_parent` 和生成审计记录。
- 不把普通杯子、钥匙、手机、书本等生活小物件放入 `props`，也不为它们生成图或写 `@`。

## 十个硬门禁

### 1. 读全五份参考

先完整读取五份必读契约，再触碰用户小说。以当前脚本契约为准，不自创必填字段或资产类型。

### 2. 复制原文并验证提取

先复制而不移动、覆盖或改写原文，计算 SHA-256，再提取全文：

```bash
python3 "$SKILL_DIR/scripts/extract_source.py" INPUT --output PROJECT/00_原著文件/解析后全文.txt
```

核对提取文本的第一章和最后一章，然后写入 `source.sha256`、`character_count`、`chapter_index` 和 `coverage`。PDF 无可提取文本时停止并报告需要 OCR；不猜测正文。

### 3. 通读全书后再写分镜

按有界批次通读全文，每批原子更新 `project.json`。先确认末章已读且 `source.coverage = 1.0`，再创建分集剧本和镜头。长篇超出单次上下文时，从第一个未完成批次续作，不用局部内容假装全书结论。

### 4. 先建故事圣经、全集纲和编号

先建立世界规则、事件时间线、伏笔与回收、人物弧、完整分季/分集纲和原著章节对应。随后锁定 `PRJ/C/S/P/F/E/SH` 编号、重要性、视觉阶段和声音档案。改名不改号，废弃编号不复用。

### 5. 实际生成有依赖顺序的图片

使用内置图片生成器，按风格参考图 → E001–E003 所有出镜人物（包括 `minor`）的综合设定图 → 主角/主要配角表情与动作多宫格 → E001–E003 所有使用场景（包括 `secondary`）的综合设定图 → 重要道具/菜肴图 → E001 每个镜头一张示范画面的图像链生成实际文件。对每张本地参考图，先用 `view_image` 检查，再把真实本地路径传给图片生成器。`@reference_token` 只标识应附上哪张登记图，不是图片附件。

生成后用 `view_image` 检查人物身份、身体、构图、字样和场景结构；把选定的最终图复制到工作区的版本化相对路径，登记 `checksum`、`prompt`、`parent_asset_ids` 和生成记录。内置生成失败时保留任务未完成并报告真实错误；不得悄悄改用 API/CLI。

### 6. 生成主要角色声音和 E001 逐句对白

为每个 `lead` 或 `major` 角色生成约 20 秒的内置 AI 声线样音，再按 E001 的 `dialogue_lines` 每句一个文件生成完整对白音频。使用已安装的 speech Skill CLI，不写临时 SDK 脚本，不克隆或模仿真人声音，并在交付中明示“AI 生成声音”。

缺少 `OPENAI_API_KEY` 时运行 dry-run 验证参数，保留声音任务为未完成，并阻止“完整素材包已交付”的声明。不伪造 WAV、校验值或 `completed` 状态。

### 7. 初始包只写三集，只为第一集出图

设置 `script_episode_count = 3` 和 `sample_episode_count = 1`，完整写 E001–E003 剧本与镜头提示词。只为 E001 每个镜头实际生成一张 `shot_sample`；不为 E002/E003 生成分镜图，不生成首帧/关键帧/尾帧三套图，不生成任何视频。

### 8. 把完整引用写在句内

对三集的 `prompt_zh` 和 `prompt_en` 都使用相同的注册令牌集。紧跟名称写 `名称@reference_token`，不留空格；不把令牌单独放在开头或引用清单。对每个逻辑阶段选最高 `non-discarded` 版本，覆盖当前集数的 `episode_range` 优先于全局阶段。提示词可先引用尚待生成的活动版本，由媒体依赖排序先完成该资产；正式导出才要求所有引用版本均为 `completed`。普通小物件只用文字。

每批提示词后运行：

```bash
python3 "$SKILL_DIR/scripts/build_media_jobs.py" PROJECT/project.json
```

让媒体工作单对 `script_episode_count` 范围内全部剧集（初始为 E001–E003）及尚待生成的活动版本做依赖感知的句内引用预检；这不会为 E002/E003 创建镜头图或对白任务。当所有被提示词引用的基础图已完成后，再运行 `"$SKILL_DIR/scripts/validate_references.py"` 要求零错误。任何缺少、错版、多余、重复或未注册 `@` 引用都要在生成示范图前修正；不得为了让待生成活动版本“看起来已完成”而改引旧版。

### 9. 分批校验，断点续作，版本不覆盖

在原文提取、全书分析、资产登记、每批媒体和每集剧本后验证项目：

```bash
python3 "$SKILL_DIR/scripts/validate_project.py" PROJECT/project.json
python3 "$SKILL_DIR/scripts/build_media_jobs.py" PROJECT/project.json
```

待提示词引用的活动基础图全部生成并登记后，再运行：

```bash
python3 "$SKILL_DIR/scripts/validate_references.py" PROJECT/project.json
```

活动基础图仍为 `pending` 或 `redo` 时，先依赖感知地校验并续作，不把旧的同阶段 `completed` 版本改成当前引用来规避等待。

从 `media-jobs.json` 中第一个 `pending` 或 `redo` 任务续作，跳过已完成版本。使用 `"$SKILL_DIR/scripts/workflow_guard.py"` 的 `transition_asset`、`resumable_jobs` 和 `create_redo_asset` 维护合法状态；已完成资产永不回写。错误只修正命中范围，不重做无关资产。

### 10. 零错误且实体媒体验真后才导出

只在项目结构、句内引用、三集范围、必需媒体和待生成任务都返回零错误时，把 `project.status` 设为 `completed`。正式导出会对每个已完成媒体进行受限相对路径、普通文件、拒绝符号链接与物理 SHA-256 匹配校验。文件缺失、哈希不符、声音未完成或尚有媒体任务时不导出、不声称完成。

```bash
python3 "$SKILL_DIR/scripts/export_package.py" PROJECT/project.json --output-dir PROJECT/export
```

只把 `project.md`、`shots.xlsx`、`video-prompts.txt`、`media-manifest.json` 和 `validation-report.txt` 作为正式派生导出。如果目标是未知目录，让导出器发布到版本化兄弟目录，不删除用户内容。

## 集中确认

默认只在三个节点集中确认：全书解析与改编方向、核心人物/场景/声音、E001 示范素材。用户明确要求自动继续时，记录推断后跳过暂停，但不跳过硬门禁。
