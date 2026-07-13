# RJ-LRC-Local

面向 Windows 本地环境的 RJ 音声字幕工具。它会把日文音频转录为带时间轴的文本，调用本地大语言模型翻译成简体中文，并在音频同目录生成播放器可直接加载的同名 `.lrc` 文件。

整个流程都在本机执行：音频不会上传到云端。适合已有大量 `mp3`、`wav`、`flac` 等 RJ 音声文件，希望在本地播放器中直接查看中文字幕的场景。

> 本仓库的协作与设计权威文档是 [PROJECT.md](PROJECT.md) 和 [WORKLOG.md](WORKLOG.md)。本 README 是 GitHub 使用入口；遇到两者描述不一致时，以前两者为准。

## 当前状态

核心单文件链路已通过真实音频验证：

```text
音频
  -> 原始转录 JSON
  -> 清洗后 JSON
  -> 中文翻译 JSON
  -> 同名中文 LRC
```

- 已实现 `faster-whisper` / TransWithAI ChickenRice 整合包适配，能调用本机 `infer.exe` 生成日文时间轴。
- 已按真实 LRC 样本处理“空白时间戳行”：它表示上一句结束时间，不会被误写成独立台词。
- 已实现短碎句合并、长句拆分，并标记超过阈值的疑似长静音区间供人工核对。
- 已验证 LM Studio 的 OpenAI-compatible 本地接口可完成批量翻译；当前模型可在 `config.yaml` 中直接替换。
- 默认输出为音频同目录的中文同名 `.lrc`，首行带 `[by: yang 创建]` 标记，方便与外部字幕区分。
- 已实现递归批处理、断点跳过、失败日志和运行状态文件；已在真实大库上启动过扫描验证。
- 已提供 tkinter 桌面启动器与 PyInstaller 打包脚本。EXE 已成功构建，但桌面界面仍属于第一版基础壳，建议先用 CLI 小批量验证自己的模型和字幕效果。

## 为什么这样设计

转录最慢、最依赖 GPU；翻译模型却可能经常替换。因此各阶段**只通过落盘 JSON 文件通信**：

```text
音频
  -> cache/<文件指纹>.transcript.raw.json
  -> cache/<文件指纹>.transcript.clean.json
  -> cache/<文件指纹>.translated.json
  -> 音频同目录/<同名>.lrc
```

这样可以在更换翻译模型或调整提示词时，只重跑翻译和 LRC 生成，不必再次执行昂贵的 ASR。缓存名包含完整路径指纹，可避免不同剧集里同名音频互相覆盖。

## 功能

- 本地日文 ASR：调用 TransWithAI ChickenRice / `infer.exe`，支持 CUDA。
- 分段清洗：合并过碎短句、拆分过长句、记录疑似 VAD 漏识别的长静音。
- 本地翻译：兼容 LM Studio、Ollama 等 OpenAI-compatible API，按批发送并在异常时自动拆小批重试。
- LRC 输出：默认中文同名 `.lrc`；可选输出 `.ja.lrc`、`.zh.lrc`、`.bilingual.lrc`。
- 现成字幕处理：遇到已生成的本项目 LRC 会跳过；日文 `.lrc`、`.vtt`、`.srt` 可直接进入翻译，避免重复 ASR；已有中文 LRC 默认跳过。
- 批处理：递归扫描多个目录，单文件失败不中断整批，状态写入 `logs/batch_status.json`，失败写入 `logs/failed.txt`。
- 桌面启动器：可选择目录并启动后台批处理，查看批次状态与日志。

## 环境准备

### 必需条件

- Windows
- Python 3.10 或更高版本
- 已安装并能手动运行 TransWithAI ChickenRice 的转录整合包
- 本地翻译服务：LM Studio 或 Ollama，且启用 OpenAI-compatible API
- 如果使用 GPU ASR，需要可用的 NVIDIA 驱动与 CUDA 环境

安装 Python 依赖：

```powershell
python -m pip install pyyaml
```

如需构建 EXE，再安装：

```powershell
python -m pip install pyinstaller
```

### 首次配置

打开 [config.yaml](config.yaml)，重点确认以下项目：

```yaml
asr:
  faster_whisper:
    runner:
      executable_path: F:\AI\Model\models\infer.exe
      working_dir: F:\AI\Model\models

translate:
  base_url: http://127.0.0.1:1234/v1
  model: folder02@q4_k_s

lrc:
  primary_variant: zh
  output_mode: same_directory
```

- `executable_path` 和 `working_dir` 必须改为你自己的 ChickenRice 整合包位置。
- LM Studio 默认地址通常是 `http://127.0.0.1:1234/v1`；换模型时通常只需修改 `translate.model`。
- 翻译服务需要在运行命令前启动，并加载一个可用模型。
- 默认 `primary_variant: zh`，所以播放器使用的同名 `.lrc` 是中文。

## 使用方法

以下命令请在仓库根目录执行。

### 处理一个音频

```powershell
python -B app.py run-single "E:\arsm\某个作品\01.mp3"
```

成功后会生成：

```text
E:\arsm\某个作品\01.lrc
cache\...transcript.raw.json
cache\...transcript.clean.json
cache\...translated.json
```

默认不会覆盖已有的有效结果。确认要从头重跑时加 `--overwrite`：

```powershell
python -B app.py run-single "E:\arsm\某个作品\01.mp3" --overwrite
```

### 批量扫描一个或多个目录

```powershell
python -B app.py run-batch "E:\arsm"
```

也可以同时传多个根目录：

```powershell
python -B app.py run-batch "C:\Users\YANG\Music\arsm.one" "E:\arsm" "E:\smar"
```

批处理会递归寻找音频文件。为避免相同文件名的多个音频输出冲突，同一目录同一文件名的不同格式会按优先级去重，优先处理 `wav`，再到 `flac`、`m4a`、`aac`、`mp3` 等格式。

### 仅重试失败文件

当 LM Studio 暂停、网络端口异常等问题修复后，无需重新扫描整个音频库：

```powershell
python -B app.py retry-failed
```

该命令会读取 `logs/failed.txt` 中的音频路径、自动去重，并备份旧清单为 `logs/failed.before_retry.txt`。本次仍失败的文件会写回新的 `logs/failed.txt`；成功或已跳过的项目会从当前失败清单移除。除非要强制重做已有结果，否则不要添加 `--overwrite`。

### 分阶段重跑

适合只想调整某一个步骤的情况：

```powershell
python -B app.py transcribe "E:\arsm\某个作品\01.mp3" --overwrite
python -B app.py clean "E:\arsm\某个作品\01.mp3" --overwrite
python -B app.py translate "E:\arsm\某个作品\01.mp3" --overwrite
python -B app.py write-lrc "E:\arsm\某个作品\01.mp3" --overwrite
```

例如换了翻译模型后，通常只需要执行 `translate` 和 `write-lrc`，无需再次转录。

### 输出额外的日文或双语 LRC

当前默认只生成中文同名 `.lrc`。如需额外文件，在 `config.yaml` 中改为：

```yaml
lrc:
  emit_ja_lrc: true
  emit_zh_lrc: true
  emit_bilingual_lrc: true
```

将分别得到 `.ja.lrc`、`.zh.lrc`、`.bilingual.lrc`。双语采用单行格式：`[时间]日文 / 中文`。

### 桌面程序与 EXE

桌面界面的 `Retry Failed` 按钮会调用 `retry-failed`，只处理 `logs/failed.txt` 中保留的失败项。恢复 LM Studio 后，可先用它继续上次中断的批次。
桌面程序使用中文界面，可直接查看翻译服务是否连通、当前批次模式、成功/跳过/失败数量、百分比进度和正在处理的文件。LM Studio 白屏或未启动时，首页会标记“翻译服务不可用”，并阻止启动会导致翻译失败的批处理；恢复服务后可直接点击“仅重试失败项”继续处理。

直接运行桌面启动器：

```powershell
python -B desktop_app.py
```

构建单文件 EXE：

```powershell
python -B build_exe.py
```

生成的 EXE 位于 `dist/`，文件名会附带构建时间，避免被正在运行的旧版 EXE 占用。`dist/` 不提交到 GitHub；请在自己的机器上按以上命令构建。

## 运行状态与排错

- `logs/batch_status.json`：当前扫描或处理的进度，桌面程序会读取它。
- `logs/batch_runner.json`：后台批处理启动信息。
- `logs/failed.txt`：单文件失败路径和错误；处理其余文件不会被中断。
- `cache/`：可检查每个阶段的 JSON 中间产物，判断问题发生在 ASR、清洗还是翻译。

常见处理方式：

1. 翻译失败：确认 LM Studio/Ollama 服务仍在运行，检查 `base_url` 和 `model`。
2. ASR 失败：确认 `infer.exe`、工作目录、模型文件与 CUDA 环境配置正确。
3. LRC 效果不好：优先检查 `translated.json`。翻译模型可随时更换，只重跑翻译阶段即可。
4. 某作品疑似漏句：查看清洗结果中的长静音标记并人工试听；不要直接假定它一定是 VAD 漏识别。

## 已知限制

- ASMR 耳语、喘息、环境音会影响 VAD 与切分。当前长静音仅作标记，不会虚构缺失台词。
- 当前转录质量和翻译质量取决于本地模型，需要结合实际听感抽查。
- `Qwen3-ASR` 目前只有占位适配；它没有直接时间戳时仍需接入 ForcedAligner，尚未完成实测。
- `txt`、`pdf` 文本目前用于参考检测，并不能替代逐句 ASR 时间轴。
- 公开仓库不包含模型、音频、缓存、日志和 EXE，以避免上传隐私内容与大文件。

## 项目结构

```text
.
├── app.py                 # CLI 入口
├── desktop_app.py         # tkinter 桌面启动器
├── build_exe.py           # PyInstaller 构建入口
├── config.yaml            # 所有可替换配置
├── modules/
│   ├── asr/               # ASR 抽象与 ChickenRice 适配
│   ├── segment_cleaner.py # 分段清洗
│   ├── translate.py       # 本地 OpenAI-compatible 翻译
│   ├── lrc_writer.py      # LRC 生成
│   ├── subtitle_sources.py# 已有字幕导入与策略
│   └── batch.py           # 递归批处理
├── PROJECT.md             # 架构、约束、风险与阶段状态
└── WORKLOG.md             # 实测记录、踩坑与后续事项
```

## 下一步建议

1. 先用一两个熟悉的音频验证 ASR 和翻译质量，再处理大目录。
2. 抽听长静音标记处，决定是否要调整 VAD 阈值。
3. 对比不同本地翻译模型时，只重跑翻译和 LRC 阶段。
4. 大库运行时关注 `logs/batch_status.json` 与 `logs/failed.txt`，不要因为单个文件失败而全量重跑。
