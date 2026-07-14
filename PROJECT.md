# Arsm Transcribe 项目文档

> `PROJECT.md` 与 `WORKLOG.md` 是 AI 协作权威入口；`README.md` 是公开使用说明。接手项目必须先读本文档和 WORKLOG 最新记录。

## 0. 工作规则

1. 改模块前先在 WORKLOG 顶部写计划，会话结束前补实际改动、踩坑和下一步。
2. 修改功能后同步更新本文档状态和 README。
3. 不确定的模型、VAD、字幕语言和配对不得猜测，标记待人工确认。
4. ASR、清洗、翻译和字幕生成只通过磁盘 JSON 通信；SQLite 只管理资料库和任务状态。
5. 简单功能允许按功能族合并交付，但全库写入、来源覆盖和未知配对必须保留安全检查。

## 1. 产品目标

```text
本地音频库
  → 增量扫描与作品聚合
  → 已有字幕分类
  → ASR（需要时）
  → 分段清洗
  → 本地模型翻译（需要时）
  → 中文 LRC + 中文 VTT
  → 质量检查与人工复核
```

默认资料库为 `E:\arsm`。每个 WAV、MP3、有音效、无音效版本均为独立媒体，不按 stem 去重。项目独立于 Yang-Kura；未来只通过版本化 JSON CLI 集成。

音频和字幕内容不上传云端。TXT/PDF 当前只作校对参考，不替代逐句时间轴。

## 2. 架构与数据

### 阶段 JSON

```text
cache/*.transcript.raw.json
cache/*.transcript.clean.json
cache/*.translated.json
音频目录/audio.lrc
音频目录/audio.vtt
```

所有 JSON、LRC 和 VTT 使用临时文件完成后原子替换。更换翻译模型只重跑翻译和字幕写出，不重跑 ASR。

### SQLite schema v2

`data/library.db` 保存作品、媒体、字幕来源、任务、执行尝试、质量标记、模型档案和术语表。媒体通过路径、大小和 mtime 增量更新，移动或删除的文件标记离线而非立即删除历史。

### API 1.1

- 扫描诊断：`scan`、`plan`、`doctor`、`status`。
- 资料库：`list-works`、`list-media`。
- 任务：`enqueue`、`jobs`、`pause`、`resume`、`cancel-job`、`cancel`、`retry-failed`。
- 质量和配置：`review`、`profiles`、`glossary`、`add-term`。
- 处理链路：`run-single`、`run-batch`、`transcribe`、`clean`、`translate`、`write-lrc`。

模型档案激活结果落盘到 `cache/active_profiles.json`；术语表导出到 `cache/glossary.json`，翻译模块只读取磁盘 JSON。

## 3. ASR 实测约束

ChickenRice/cu128-transcribe 真实输出：

```text
[00:03.04]い、いらっしゃい。待ってたよ……
[00:06.86]
[00:08.38]どう?この日のために用意した、特別仕様の巫女服。
```

空文本时间戳代表上一句结束时间，不能作为独立台词。长空档只标记疑似漏识别，不自动编造内容；当前 VAD 阈值 0.5 是否优于早期建议 0.2 仍待人工试听。

清洗阶段必须独立存在：合并短间隔碎片、按标点拆分超长句、记录疑似长静音。

## 4. 字幕处理策略

| 来源 | 默认动作 |
|---|---|
| 本项目或已有中文 LRC | 跳过 |
| 中文/非日文 VTT、SRT | 保留时间轴，直接转换 |
| 日文 VTT、SRT、LRC | 保留时间轴，只翻译 |
| 语言未知定时字幕 | 人工确认并跳过 |
| 无定时字幕 | ASR → 清洗 → 翻译 |
| TXT/PDF | 参考文本 |

匹配包含 `audio.wav.vtt`、`audio.mp3.vtt`、完整文件名 SRT、日文 LRC 和普通 stem 字幕。同名来源不静默覆盖，冲突使用 `.zh.lrc/.zh.vtt`。

## 5. 正式桌面端

`desktop_app.py` 启动 PySide6 媒体资料库界面，包含：

- 总览：作品/媒体/处理分类/质量统计和一键安全处理。
- 作品资料库：RJ 或目录聚合、封面缩略图、搜索筛选、音轨详情。
- 任务中心：进度、暂停、恢复、当前文件后停止、失败重试。
- 质量审查：空翻译、日文残留、超长行、时间轴异常、重叠和长静音。
- 模型中心：环境诊断、模型档案保存和激活。
- 术语表：全局或作品术语，翻译 prompt 自动使用。

旧 tkinter 界面保留为 `desktop_legacy.py`。正式 EXE 由 `build_exe.py` 打包。

## 6. 当前实测状态

| 模块 | 状态 | 说明 |
|---|---|---|
| ASR/清洗/翻译/LRC/VTT | 已实现 | ASR 与翻译质量仍需人工抽听 |
| 字幕来源分类与冲突保护 | 已实现并测试 | 人工确认项明确跳过 |
| SQLite 资料库 schema v2 | 已实现并迁移验证 | 267 个真实作品查询约 127 ms |
| API 1.1 | 已实现 | CLI 返回版本化 JSON |
| PySide6 正式 UI | 已实现 | 6 页面，PySide6-Essentials 6.11.1 |
| 任务控制 | 已实现基础版 | 暂停采用安全停止，恢复靠阶段 JSON |
| 质量检查 | 已实现基础规则 | 仍需真实字幕人工校准阈值 |
| 模型档案/术语表 | 已实现 | 通过磁盘 JSON 接入处理模块 |
| Windows EXE | 已构建并启动验证 | `Arsm-Transcribe-20260713-132137.exe` |
| Qwen3-ASR + ForcedAligner | 未启动 | 第一版稳定后再做 A/B |

2026-07-13 只读扫描 `E:\arsm`：267 个作品、8611 个媒体；直接转换 3350、只翻译 219、ASR 4934、已有 LRC 108。扫描约 15 秒，未启动模型、未改音频目录。

## 7. 风险与下一步

- LM Studio 当前 API 可连接，但此前发生过白屏和连接中断；接口可用不代表模型质量合格。
- 当前作品聚合优先 RJ 编号，否则按资料库第一层目录；特殊散装目录需要通过 UI 抽查。
- 任务 attempts 表已建立，当前批次主要记录 job 汇总，逐阶段执行历史可继续增强。
- 质量规则是启发式，需要用真实中文字幕校准误报率。
- 下一步先用 1-3 个熟悉作品验证 UI、播放器加载、翻译质量和暂停恢复，再决定是否分批处理全库。
