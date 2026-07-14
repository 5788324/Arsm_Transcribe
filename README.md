# Arsm Transcribe

Windows 本地 RJ/ASMR 音声字幕资料库。它会按作品管理大量音频，优先复用已有 VTT/SRT/LRC；没有可用时间轴时才运行 ASR，最终生成播放器可加载的中文 LRC 和中文 VTT。

音频和字幕内容全程留在本机。ASR、翻译模型、提示词和术语表都可以更换，昂贵的转录结果通过磁盘 JSON 保留，不会因为换翻译模型而作废。

## 主要功能

- 现代 PySide6 媒体资料库界面：总览、作品库、任务中心、质量审查、模型中心、术语表六个页面。
- 按 RJ 编号优先聚合作品；没有 RJ 编号时按资料库第一层作品目录归组，不移动或重命名文件。
- 中文定时字幕直接转换，日文 VTT/SRT/LRC 只翻译，无字幕才运行 ASR，未知语言留给人工确认。
- 支持真实资料库中的 `audio.wav.vtt`、`audio.mp3.vtt`、普通 VTT/SRT/LRC 命名。
- SQLite 增量索引、搜索筛选、任务入队、暂停/恢复、安全停止、失败重试和消失文件离线标记。
- 空翻译、日文残留、超长行、时间轴异常、重叠和长静音质量检查。
- ASR/翻译模型配置档案；激活档案写入 `cache/active_profiles.json`，后续 CLI/桌面任务自动使用。
- 全局和作品术语表；术语导出为 `cache/glossary.json` 后由翻译模块注入 prompt。
- 输出包含 `by yang 创建`声明的中文 `.lrc` 和 `.vtt`，并保护同名来源字幕。

2026-07-13 对 `E:\arsm` 的只读真实扫描结果：267 个作品、8611 个媒体版本；其中 3350 个可直接转换、219 个只需翻译、4934 个需要 ASR、108 个已有 LRC 可跳过。扫描约 15 秒，作品列表查询约 127 ms。

## 安装

```powershell
python -m pip install -r requirements.txt
```

当前环境要求：Windows 10/11、Python 3.11+、本地 ChickenRice/cu128-transcribe ASR，以及 LM Studio/Ollama OpenAI-compatible 翻译服务。

仓库不包含模型、音频、缓存、SQLite 数据库、日志和 EXE。

## 桌面端

```powershell
python -B desktop_app.py
```

推荐操作顺序：

1. 在总览点击“扫描新文件”。
2. 在作品库搜索或检查自动分类结果。
3. 点击“一键处理安全项目”并确认摘要。
4. 在任务中心查看进度、暂停、恢复、停止或重试失败项。
5. 处理后运行质量审查，检查异常字幕。

暂停和停止都会等待当前文件结束，不强杀模型进程。恢复时依靠 raw/clean/translated JSON 自动跳过已完成步骤。

旧 tkinter 界面保留在 `desktop_legacy.py`，仅用于应急，不再作为默认入口。

## 配置与模型

默认资料库和模型配置位于 `config.yaml`：

```yaml
library:
  roots:
    - E:\arsm

translate:
  enabled: true
  base_url: http://127.0.0.1:1234/v1
  model: folder02@q4_k_s
```

桌面“模型中心”可保存和激活多个 ASR/翻译档案。更换翻译模型后通常只需要重新执行 `translate` 和 `write-lrc`，不需要重跑 ASR。

## CLI

环境诊断与资料库扫描：

```powershell
python -B app.py doctor
python -B app.py plan "E:\arsm" --output logs\arsm-plan.json
python -B app.py scan "E:\arsm" --output logs\arsm-scan.json
python -B app.py list-works --search RJ01234567
```

处理与恢复：

```powershell
python -B app.py run-single "E:\arsm\RJxxxx\Track01.wav"
python -B app.py run-batch "E:\arsm"
python -B app.py retry-failed
python -B app.py cancel
```

资料库 API 1.1 还提供：`list-media`、`enqueue`、`jobs`、`pause`、`resume`、`cancel-job`、`review`、`profiles`、`glossary`、`add-term` 和 `status`。所有命令返回可供未来 Yang-Kura 调用的版本化 JSON。

## 输出规则

正常输出：

```text
Track01.wav
Track01.lrc
Track01.vtt
```

如果已有 `Track01.vtt` 是来源字幕，生成 `Track01.zh.vtt`；同名日文 LRC 会保留并生成 `Track01.zh.lrc`。人工确认项会跳过，不进入失败清单。

TXT/PDF 没有逐句时间戳时只作为校对参考，不直接转换成 LRC。

## 构建与验证

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app.py desktop_app.py desktop_qt.py modules tests
python -B build_exe.py
```

已验证 18 项自动化测试、PySide6 离屏启动和单文件 EXE 构建。最新本机产物位于 `dist/Arsm-Transcribe-*.exe`，`dist/` 不提交 GitHub。

## 设计参考

项目借鉴但不直接照搬 [Subtitle Edit](https://github.com/SubtitleEdit/subtitleedit) 的字幕检查、[Buzz](https://github.com/chidiwilliams/buzz) 的本地任务体验、[faster-whisper](https://github.com/SYSTRAN/faster-whisper) 的 ASR 结构、[stable-ts](https://github.com/jianfch/stable-ts) 的时间戳优化和 [WhisperX](https://github.com/m-bain/whisperX) 的强制对齐思路。引入第三方代码前必须单独核对许可证。

详细架构、实测格式与当前风险见 `PROJECT.md`；逐次开发记录和踩坑见 `WORKLOG.md`。
