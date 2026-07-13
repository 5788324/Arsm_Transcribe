# Arsm Transcribe

Windows 本地 RJ/ASMR 音声字幕处理工具。它会扫描音声资料库，优先复用已有 VTT/SRT/LRC；没有可用时间轴时才运行 ASR，最终为每个媒体版本生成中文 LRC 和中文 VTT。

音频和字幕内容不会上传云端。转录与翻译模型都可以替换，阶段结果保存在磁盘 JSON 中，更换翻译模型不需要重跑昂贵的 ASR。

## 当前能力

- ChickenRice/cu128-transcribe `infer.exe` ASR 适配，正确解析“空时间戳行代表上一句结束时间”的真实格式。
- 独立分段清洗，合并碎片、拆分长句并标记疑似长静音漏识别。
- 本地 OpenAI-compatible 批量翻译，兼容 LM Studio/Ollama。
- 生成带 `by yang 创建`声明的同名中文 `.lrc` 和 `.vtt`。
- 支持 `audio.wav.vtt`、`audio.mp3.vtt`、普通 VTT/SRT/LRC 等来源命名。
- 中文定时字幕直接转换，日文定时字幕只翻译，无字幕才运行 ASR。
- SQLite 增量资料库、处理计划、诊断、断点续跑和失败重试。
- tkinter 过渡桌面端；正式 PySide6 任务管理界面仍在开发。

## 环境

- Windows 10/11
- Python 3.11+
- `PyYAML`
- 本地 ASR 整合包，当前配置指向 `F:\AI\Model\models\infer.exe`
- 可选本地翻译服务：LM Studio 或 Ollama 的 OpenAI-compatible API

仓库不包含模型、音频、缓存、日志、SQLite 数据库或打包 EXE。

## 配置

编辑 `config.yaml`：

```yaml
paths:
  cache_dir: cache
  output_dir: output
  log_dir: logs
  database_path: data/library.db

translate:
  enabled: true
  base_url: http://127.0.0.1:1234/v1
  model: folder02@q4_k_s
```

ASR 执行器、模型路径、VAD、翻译模型、批大小和超时也全部在该文件中配置。更换模型后通常只需重跑 `translate` 和 `write-lrc`。

## 推荐流程

先检查环境：

```powershell
python -B app.py doctor
```

只生成处理预览，不改写音频目录：

```powershell
python -B app.py plan "E:\arsm" --output logs\arsm-plan.json
```

确认计划后再执行：

```powershell
python -B app.py run-batch "E:\arsm"
```

如果中途服务停止：

```powershell
python -B app.py retry-failed
```

需要安全停止当前批次时：

```powershell
python -B app.py cancel
```
扫描并更新 SQLite 增量索引：

```powershell
python -B app.py scan "E:\arsm" --output logs\arsm-scan.json
```

## 单文件与分阶段运行

```powershell
python -B app.py run-single "E:\arsm\RJxxxx\Track01.wav"
python -B app.py transcribe "E:\arsm\RJxxxx\Track01.wav"
python -B app.py clean "E:\arsm\RJxxxx\Track01.wav"
python -B app.py translate "E:\arsm\RJxxxx\Track01.wav"
python -B app.py write-lrc "E:\arsm\RJxxxx\Track01.wav"
```

`write-lrc` 为兼容旧命令保留，现在会同时写出中文 LRC 和中文 VTT。默认不会覆盖有效结果；显式使用 `--overwrite` 才会重跑，但来源字幕冲突仍会采用安全侧文件名。

## 桌面端

```powershell
python -B desktop_app.py
```

当前界面支持目录管理、扫描预览、批处理、安全停止、失败重试、服务检查和进度查看。扫描预览写入 `cache/latest_plan.json`，不会处理音频。正式 PySide6 多页任务管理界面是下一阶段工作。

## 输出与冲突规则

正常输出：

```text
Track01.wav
Track01.lrc
Track01.vtt
```

如果已有 `Track01.vtt` 是来源字幕，项目不会静默覆盖它，而会生成 `Track01.zh.vtt`；同名日文 LRC 会保留来源并生成 `Track01.zh.lrc`。未知语言或无法可靠配对的字幕进入人工确认，不会猜测或覆盖。

TXT/PDF 没有逐句时间戳时只作为校对参考，不能直接转换成 LRC。

## 开发与验证

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py desktop_app.py modules tests
```

详细架构、实测格式、硬规则和阶段状态见 `PROJECT.md`；每次开发记录和踩坑见 `WORKLOG.md`。

## 后续规划

1. SQLite 任务/尝试记录、暂停与协作取消。
2. 术语表、角色名一致性、日文残留与时间轴质量检查。
3. PySide6 正式桌面端和 Windows 便携版。
4. 小样本人工验收后，对 `E:\arsm` 分批执行。
5. 项目稳定后再决定是否通过 JSON CLI 接入 Yang-Kura。
