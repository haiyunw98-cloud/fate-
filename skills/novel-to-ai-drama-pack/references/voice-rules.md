# 角色声音与 TTS 规则

## 目录

- [范围与合规](#范围与合规)
- [真实 CLI 与环境](#真实-cli-与环境)
- [内置声线](#内置声线)
- [角色声音档案](#角色声音档案)
- [约-20-秒角色样音](#约-20-秒角色样音)
- [E001 逐句对白](#e001-逐句对白)
- [表演指令](#表演指令)
- [字符与切分限制](#字符与切分限制)
- [批量速率限制与重试](#批量速率限制与重试)
- [文件命名与登记](#文件命名与登记)
- [缺少凭据](#缺少凭据)
- [质量检查](#质量检查)
- [命令示例](#命令示例)

## 范围与合规

为每个 `lead` 和 `major` 角色创建声音档案和约 20 秒声音样本，再为 E001 的所有 `dialogue_lines` 生成逐句对白音频。对 `minor` 和 `cameo` 不默认建样音；如果它们在 E001 有台词，仍必须有合法 `voice_profile` 并生成该句对白。

只使用 OpenAI Speech CLI 支持的内置 AI 声线。不创建自定义声线，不上传真人声纹，不克隆、仿声、模仿或暗示某个真人、名人、演员或用户本人。

在项目交付、试听页和对外发布中给出清晰披露：

```text
本项目角色声音由 AI 生成，使用内置合成声线，未使用真人声音克隆。
```

将披露随资产登记为 `ai_generated: true` 和明确 `ai_disclosure`，但不把这两个可选字段误说成当前所有资产的结构必填项。

## 真实 CLI 与环境

使用已安装 speech Skill 的真实 CLI，不修改它，不写一次性 SDK 脚本：

```bash
TTS_GEN="${CODEX_HOME:-$HOME/.codex}/skills/speech/scripts/text_to_speech.py"
python3 "$TTS_GEN" list-voices
```

实时生成需要：

- 本地环境已安装 `openai` Python 包。
- `OPENAI_API_KEY` 已在用户环境中设置。
- 网络可达且当前运行环境允许请求。

不要请用户在对话中粘贴完整 API 密钥；请用户在本地设置环境变量后确认。正式调用前可以用 `--dry-run` 打印请求负载和预计输出，但 dry-run 不生成媒体。

默认模型是 `gpt-4o-mini-tts-2025-12-15`，默认输出格式是 `mp3`，默认 CLI 声线是 `cedar`。当前 `build_media_jobs.py` 生成的工作单显式使用 `coral`；执行媒体任务时以工作单的 `built_in_voice` 为准，不用 CLI 默认值悄悄替换。

## 内置声线

只从当前 CLI 支持的列表中选择：

```text
alloy
ash
ballad
cedar
coral
echo
fable
marin
nova
onyx
sage
shimmer
verse
```

使用 `list-voices` 的现场输出验证列表；如果 CLI 未来变更，以实际 CLI 为准并更新本契约。

在初始项目中，不为了区分角色随意增加带地域、族群或真人指向的口音。优先用 `voice_profile` 中的音色、语气、语速与 4–8 行表演指令区分角色。

## 角色声音档案

每个 `characters[]` 的 `voice_profile` 必须包含：

- `voice`：用自然语言描述声音年龄感、明暗、厚薄、沙哑或清亮等固定质感；它不是 API 内置声线名的替代。
- `tone`：角色基准态度，如沉稳、克制、轻快、冷淡。
- `pace`：角色基准语速与停顿习惯。
- `sample_text`：约 20 秒样音的精确台词，必须能表现常态、转折与至少一个重音。

可在生成指令中补充音量、情绪弹性、呼吸、句尾、人名发音和禁止特征，但不要向 canonical 角色对象发明当前 validator 并不支持的额外必填字段。

对已确认的角色，锁定同一个内置声线和相容的表演指令。重做时记录改动项，不在逐句对白中随机换声线。

## 约 20 秒角色样音

为每个 `lead` 和 `major` 生成一个 `voice_sample`。使用 `voice_profile.sample_text` 的原文作为 TTS `input`，不在执行时润色、缩写、加前缀或补台词。

把“约 20 秒”当作试听目标，而不是通过加速/减速硬拉到精确毫秒。在常用中速中准备足以展示连贯性的文本，实际生成后检查时长、可懂度和呼吸。明显过短或过长时，修订 canonical `sample_text`、记录原因，再创建新版本；不改写已完成文件。

样音命名例：

```text
AUD_C001_V001.wav
```

项目可读展示名可以是：

```text
声音_AUD_C001_林岚_标准声音_V001.wav
```

但 `file_name`、`relative_path`、`reference_token` 和实体文件必须同时符合项目中已选定的唯一命名，不同时创建两个同版本别名文件。

## E001 逐句对白

在 E001 的每个镜头显式写 `dialogue_lines`。无对白写空数组；有对白时按播放顺序写：

```json
"dialogue_lines": [
  {"speaker_id": "C001", "text": "剑为什么会在这里？"},
  {"speaker_id": "C002", "text": "因为它一直在等你。"}
]
```

为每个对白对象生成一个独立 `dialogue_audio`，不把不同人物、不同句或不同镜头拼成一个文件。实际 TTS `input` 必须与 `text` 字符级一致。

所有对白都使用发言人的 `voice_profile`。只有 `lead`/`major`，或已经为该发言人显式登记 `voice_sample` 时，对白才使用对应样音：样音仍待生成时，`dialogue_audio` 任务依赖该样音任务；样音已完成时，使用其已完成资产和引用令牌，不重复建样音任务。

`minor`/`cameo` 不默认创建 `voice_sample`。它们在 E001 有对白但没有已显式登记 `voice_sample` 时，直接使用其 `voice_profile` 与项目内置声线生成逐句对白，不为了对白额外创建约 20 秒样音。当前工作单会对 `speaker_id + text` 计算内容指纹；台词或发言人改变后不得错用旧对白文件，应产生新版本。

命名格式：

```text
DIALOGUE_E001_SH001_L001_V001.wav
DIALOGUE_E001_SH001_L002_V001.wav
```

每个 `dialogue_audio.owner_id` 是所属 `shot_id`，不是人物 ID。将发言人记录在 `speaker_id`，将精确台词记录在 `text` 和当前任务使用的 `prompt`。

## 表演指令

为每次请求写 4–8 行短指令。只显式化角色档案、当前台词和剧情已经支持的表演，不创造口音、人格、情绪或额外台词。

选用标签：

```text
Voice Affect: <年龄感、明暗、紧绷或松弛>
Tone: <态度与当下对象>
Pacing: <整体语速与局部改变>
Emotion: <起点、转折和结尾情绪>
Pronunciation: <人名、地名、术语或缩写>
Pauses: <有意停顿的位置>
Emphasis: <需要重读的精确词>
Delivery: <句尾、气息与节奏不变项>
```

建议角色样音指令：

```text
Voice Affect: 清亮但不轻浮，保持青年女性自然声线。
Tone: 克制、沉稳，不模仿任何真人。
Pacing: 中速，转折句前短暂停顿。
Emotion: 由冷静逐步露出坚定，不大声煽情。
Pronunciation: 清楚读准“林岚”和项目专名。
Emphasis: 自然重读“这一次”和“不会”。
Delivery: 保持口齿清晰，不加词，不改写原台词。
```

不在 `instructions` 中重复整段输入文本。如果使用 `tts-1` 或 `tts-1-hd`，CLI 会忽略表演指令；初始工作流保持默认 GPT-4o mini TTS 模型以支持指令。

## 字符与切分限制

每个 TTS 请求的输入文本必须不超过 4096 个字符。角色样音和 E001 逐句对白通常应远低于该上限；仍在建任务时检查。

长旁白或长台词超限时：

1. 优先在剧本阶段删除不符合短剧节奏的大段说明。
2. 仍需保留时，在自然句界拆成多个有明确播放顺序的台词对象和文件。
3. 不在 TTS 层面无声截断文本。
4. 保留语意完整、人名发音和句间停顿。

## 批量速率限制与重试

多条台词使用 `speak-batch`，将临时 JSONL 放在项目 `tmp/speech/`，每行一个请求，完成后删除临时 JSONL。不把 API 凭据写入 JSONL、项目文件或 Git。

设置 `--rpm` 不高于 50。CLI 会将超过 50 的值限制为 50，并在请求之间按最小间隔等待。不通过同时启动多个批处理进程绕过上限。

默认 `--attempts 3`。CLI 只对限流、超时和临时网络类错误重试：

- 优先遵循错误中的 `retry-after`。
- 无 `retry-after` 时使用最长 60 秒的递增退避。
- 非临时错误不反复重试。
- 三次仍失败时记录最终错误并保留任务未完成，不创建空音频占位。

## 文件命名与登记

声音样本使用：

```text
asset_id: AUD_C001
file_name: AUD_C001_V001.wav
relative_path: assets/audio/AUD_C001_V001.wav
reference_token: @声音_AUD_C001_林岚_标准声音_V001
```

E001 对白使用：

```text
asset_id: DIALOGUE_E001_SH001_L001
file_name: DIALOGUE_E001_SH001_L001_V001.wav
relative_path: assets/audio/dialogue/DIALOGUE_E001_SH001_L001_V001.wav
owner_type: shot
owner_id: E001_SH001
```

执行时默认选 WAV 便于快速试听与剪辑，文件扩展名必须与 CLI `--response-format` 一致。不使用 `--force` 覆盖已登记完成文件；重做时用新 `_VNNN`。

每个完成音频登记：

- 实际输入文本和 4–8 行表演指令。
- 模型、内置声线、速度、格式和生成时间。
- 版本化文件名、项目内受限相对路径和实际 SHA-256。
- 对白的 `speaker_id`、`text` 和内容指纹。
- AI 生成披露和听检结果。

只在文件真实存在、可读、试听达标且校验值已计算时设为 `completed`。

## 缺少凭据

检查 `OPENAI_API_KEY` 时只检查是否存在，不打印或读出密钥值。如果缺少：

1. 完成角色 `voice_profile`、样本精确文本、E001 `dialogue_lines`、声线选择、表演指令、输出路径和媒体任务清单。
2. 对单条或批任务使用 `--dry-run` 验证负载，不发起 API 请求。
3. 不创建 WAV/MP3 占位文件，不填伪校验值。
4. 将声音资产保持为 `confirmed`、`pending` 或其实际未完成状态，绝不设为 `completed`。
5. 清晰报告“声音媒体未交付，缺少本地 API 凭据”，并阻止项目 `completed` 与正式导出。

dry-run 成功只证明参数形状可接受，不证明账号权限、网络、模型可用性、输出文件或声音质量。

## 质量检查

对每个角色样音和重要对白试听：

- 所有文字都被读出，没有加词、漏词、重复或截断。
- 人名、地名、门派、法宝、外语、数字和缩写发音正确。
- 语速、音量、句间停顿和重音符合角色档案与当前情绪。
- 声音没有明显爆音、底噪、金属感、机械腔、尾部断裂或过度戏剧化。
- 同一角色在样音和 E001 对白中使用同一内置声线与相容表演约束。
- 文件格式可播放，时长合理，音频内容与文件名/台词登记一致。

检查失败时只做一个有针对性的变更，如修正人名发音、降低语速或减少情绪幅度，然后生成新版本并重新试听。不用多项同时变更破坏可归因性。

## 命令示例

生成一个角色样音：

```bash
TTS_GEN="${CODEX_HOME:-$HOME/.codex}/skills/speech/scripts/text_to_speech.py"
python3 "$TTS_GEN" speak \
  --input "这一次，我不会再退。哪怕前面的路没有答案，我也要亲自走到尽头。" \
  --voice coral \
  --instructions "Voice Affect: 清亮且沉稳。
Tone: 克制、坚定，不模仿任何真人。
Pacing: 中速，转折前短暂停顿。
Emotion: 由冷静逐步露出决心。
Emphasis: 重读“这一次”和“亲自”。
Delivery: 口齿清晰，不加词，不改台词。" \
  --response-format wav \
  --out PROJECT/assets/audio/AUD_C001_V001.wav
```

缺凭据时验证同一请求：

```bash
python3 "$TTS_GEN" speak \
  --input "这一次，我不会再退。" \
  --voice coral \
  --instructions "Tone: 沉稳。
Pacing: 中速。
Emotion: 坚定。
Pronunciation: 人名发音清晰。
Emphasis: 重读“这一次”。
Delivery: 不加词，不改台词。" \
  --response-format wav \
  --out PROJECT/assets/audio/AUD_C001_V001.wav \
  --dry-run
```

批量 JSONL 的每行至少写 `input`、`voice`、`instructions`、`response_format` 和 `out`。当命令使用 `--out-dir PROJECT/assets/audio/dialogue` 时，每行 `out` 只写文件基名，不再写 `assets/audio/dialogue/...`，避免在输出目录下重复嵌套路径。

一条完整 JSONL 例子（整个对象在同一行）：

```json
{"input":"剑为什么会在这里？","voice":"coral","instructions":"Voice Affect: 清亮且紧绷。\nTone: 警觉，不模仿任何真人。\nPacing: 中速，问句前短暂停顿。\nEmotion: 由疑惑转为不安。\nPronunciation: 清楚读准人名和专名。\nDelivery: 不加词，不改写原台词。","response_format":"wav","out":"DIALOGUE_E001_SH001_L001_V001.wav"}
```

然后运行：

```bash
python3 "$TTS_GEN" speak-batch \
  --input PROJECT/tmp/speech/e001-dialogue.jsonl \
  --out-dir PROJECT/assets/audio/dialogue \
  --rpm 50 \
  --attempts 3
```

执行成功、试听和 SHA-256 登记后，删除临时 JSONL。
