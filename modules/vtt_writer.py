from __future__ import annotations

from pathlib import Path

from modules.io_utils import atomic_write_text, load_json

NOTE = 'NOTE by yang 创建'


class VTTWriter:
    def write_file(
        self,
        translated_json_path: Path,
        primary_output_path: Path,
        zh_output_path: Path,
        *,
        source_path: Path | None = None,
        overwrite: bool = False,
    ) -> Path:
        target = self.resolve_target(primary_output_path, zh_output_path, source_path=source_path, overwrite=overwrite)
        if target.exists() and not overwrite:
            return target

        payload = load_json(translated_json_path)
        lines = ['WEBVTT', '', NOTE, '']
        cue_index = 1
        for segment in payload.get('segments', []):
            text = str(segment.get('translation', '')).strip()
            if not text:
                continue
            lines.extend([
                str(cue_index),
                f'{format_vtt_timestamp(float(segment["start"]))} --> {format_vtt_timestamp(float(segment["end"]))}',
                text,
                '',
            ])
            cue_index += 1
        atomic_write_text(target, '\n'.join(lines))
        return target

    @staticmethod
    def resolve_target(
        primary_output_path: Path,
        zh_output_path: Path,
        *,
        source_path: Path | None,
        overwrite: bool,
    ) -> Path:
        if source_path is not None and _same_path(source_path, primary_output_path):
            return zh_output_path
        if primary_output_path.exists() and not overwrite:
            return zh_output_path
        return primary_output_path


def format_vtt_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}'


def _same_path(left: Path, right: Path) -> bool:
    return str(left.resolve()).casefold() == str(right.resolve()).casefold()
