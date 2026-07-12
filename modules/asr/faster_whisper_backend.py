from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from modules.asr.base import ASRBackend, TranscriptSegment, normalize_segment
from modules.io_utils import ensure_parent_dir

TIMESTAMP_LINE_RE = re.compile(r"^\[(?P<mm>\d{2}):(?P<ss>\d{2})\.(?P<cs>\d{2})\](?P<text>.*)$")
TRUNCATED_EMPTY_TIMESTAMP_RE = re.compile(r"^:(?P<ss>\d{2})\.(?P<cs>\d{2})\]$")


class FasterWhisperBackend(ASRBackend):
    name = "faster_whisper"

    def transcribe(self, audio_path: Path, config: dict[str, Any]) -> list[TranscriptSegment]:
        lrc_path = self._obtain_lrc(audio_path, config)
        return self.parse_lrc(lrc_path)

    def _obtain_lrc(self, audio_path: Path, config: dict[str, Any]) -> Path:
        asr_config = config.get("asr", {})
        backend_config = asr_config.get("faster_whisper", {})
        runner_config = backend_config.get("runner", {})

        existing_lrc = runner_config.get("existing_lrc_path")
        if existing_lrc:
            lrc_path = Path(existing_lrc).expanduser().resolve()
            if not lrc_path.exists():
                raise FileNotFoundError(f"configured existing_lrc_path not found: {lrc_path}")
            return lrc_path

        executable_path = runner_config.get("executable_path")
        if executable_path:
            return self._run_infer_exe(audio_path, config, runner_config)

        command_template = runner_config.get("command_template")
        if command_template:
            return self._run_command_template(audio_path, config, runner_config, command_template)

        raise ValueError(
            "ASR runner is not configured. Set asr.faster_whisper.runner.executable_path, "
            "command_template, or existing_lrc_path in config.yaml."
        )

    def _run_infer_exe(
        self,
        audio_path: Path,
        config: dict[str, Any],
        runner_config: dict[str, Any],
    ) -> Path:
        asr_config = config.get("asr", {})
        executable_path = Path(str(runner_config["executable_path"])).expanduser().resolve()
        if not executable_path.exists():
            raise FileNotFoundError(f"infer executable not found: {executable_path}")

        working_dir_value = runner_config.get("working_dir") or executable_path.parent
        working_dir = Path(working_dir_value).expanduser().resolve()
        output_dir_value = runner_config.get("output_dir")
        output_dir = (Path(output_dir_value).expanduser().resolve() if output_dir_value else audio_path.parent)
        ensure_parent_dir(output_dir / "placeholder.txt")
        output_lrc_path = output_dir / f"{audio_path.stem}.lrc"

        args = [
            str(executable_path),
            f'--audio_suffixes={runner_config.get("audio_suffixes", "mp3,wav,flac,m4a,aac,ogg,wma,mp4,mkv,avi,mov,webm,flv,wmv")}',
            f'--sub_formats={runner_config.get("sub_formats", "lrc")}',
            f'--device={asr_config.get("device", "cuda")}',
            f'--task={runner_config.get("task", "transcribe")}',
            "--overwrite",
        ]

        generation_config = runner_config.get("generation_config")
        if generation_config:
            args.append(f"--generation_config={generation_config}")

        model_name_or_path = runner_config.get("model_name_or_path")
        if model_name_or_path:
            args.append(f"--model_name_or_path={model_name_or_path}")

        if output_dir_value:
            args.append(f"--output_dir={output_dir}")

        vad_threshold = backend_config_value(config, "vad", "threshold")
        if vad_threshold is not None:
            args.append(f"--vad_threshold={vad_threshold}")

        args.append(str(audio_path))

        completed = subprocess.run(
            args,
            cwd=str(working_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "infer.exe failed with exit code "
                f"{completed.returncode}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )

        if not output_lrc_path.exists():
            raise FileNotFoundError(
                f"infer.exe completed but expected LRC output was not found: {output_lrc_path}"
            )
        return output_lrc_path

    def _run_command_template(
        self,
        audio_path: Path,
        config: dict[str, Any],
        runner_config: dict[str, Any],
        command_template: str,
    ) -> Path:
        asr_config = config.get("asr", {})
        output_dir = Path(runner_config.get("output_dir") or audio_path.parent).expanduser().resolve()
        ensure_parent_dir(output_dir / "placeholder.txt")
        output_lrc_path = output_dir / f"{audio_path.stem}.lrc"

        working_dir_value = runner_config.get("working_dir")
        working_dir = None if not working_dir_value else Path(working_dir_value).expanduser().resolve()

        command = command_template.format(
            audio_path=str(audio_path),
            audio_dir=str(audio_path.parent),
            audio_stem=audio_path.stem,
            output_lrc_path=str(output_lrc_path),
            output_dir=str(output_dir),
            model=asr_config.get("model", ""),
            language=asr_config.get("language", ""),
            device=asr_config.get("device", ""),
            compute_type=asr_config.get("compute_type", ""),
            vad_threshold=backend_config_value(config, "vad", "threshold") or "",
        )

        completed = subprocess.run(
            command,
            cwd=str(working_dir) if working_dir else None,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "ASR runner failed with exit code "
                f"{completed.returncode}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
            )

        if not output_lrc_path.exists():
            raise FileNotFoundError(
                f"ASR runner completed but expected LRC output was not found: {output_lrc_path}"
            )
        return output_lrc_path

    @classmethod
    def parse_lrc(cls, lrc_path: Path) -> list[TranscriptSegment]:
        raw_lines = lrc_path.read_text(encoding="utf-8").splitlines()
        segments: list[dict[str, Any]] = []

        for line_number, raw_line in enumerate(raw_lines, start=1):
            line = raw_line.strip().lstrip("\ufeff")
            if not line:
                continue

            match = TIMESTAMP_LINE_RE.match(line)
            if not match:
                truncated_match = TRUNCATED_EMPTY_TIMESTAMP_RE.match(line)
                if truncated_match and segments and segments[-1].get('end') is not None:
                    continue
                raise ValueError(f"unsupported LRC line at {lrc_path}:{line_number}: {raw_line!r}")

            timestamp_seconds = _timestamp_to_seconds(match.group("mm"), match.group("ss"), match.group("cs"))
            text = match.group("text").strip()

            if text:
                segments.append(
                    {
                        "start": timestamp_seconds,
                        "end": None,
                        "text": text,
                        "confidence": None,
                    }
                )
                continue

            if not segments:
                raise ValueError(
                    f"found empty timestamp line before any text at {lrc_path}:{line_number}"
                )

            previous = segments[-1]
            if previous["end"] is not None:
                raise ValueError(
                    f"segment already had end timestamp before empty line at {lrc_path}:{line_number}"
                )
            previous["end"] = timestamp_seconds

        finalized: list[TranscriptSegment] = []
        for index, segment in enumerate(segments):
            end = segment["end"]
            if end is None:
                if index + 1 < len(segments):
                    end = float(segments[index + 1]["start"])
                else:
                    end = float(segment["start"])
            finalized.append(normalize_segment({**segment, "end": end}))
        return finalized


def backend_config_value(config: dict[str, Any], *keys: str) -> Any:
    current: Any = config.get("asr", {}).get("faster_whisper", {})
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _timestamp_to_seconds(mm: str, ss: str, cs: str) -> float:
    minutes = int(mm)
    seconds = int(ss)
    centiseconds = int(cs)
    return minutes * 60 + seconds + centiseconds / 100.0
