from __future__ import annotations

from pathlib import Path

from modules.io_utils import ensure_parent_dir, load_json

BY_TAG = '[by: yang \u521b\u5efa]'


class LRCWriter:
    def planned_outputs(
        self,
        primary_output_path: Path,
        ja_output_path: Path,
        zh_output_path: Path,
        bilingual_output_path: Path,
        *,
        emit_ja: bool = False,
        emit_zh: bool = False,
        emit_bilingual: bool = False,
    ) -> list[Path]:
        outputs = [primary_output_path]
        if emit_ja:
            outputs.append(ja_output_path)
        if emit_zh:
            outputs.append(zh_output_path)
        if emit_bilingual:
            outputs.append(bilingual_output_path)
        return outputs

    def write_file(
        self,
        translated_json_path: Path,
        primary_output_path: Path,
        ja_output_path: Path,
        zh_output_path: Path,
        bilingual_output_path: Path,
        *,
        primary_variant: str = 'zh',
        emit_ja: bool = False,
        emit_zh: bool = False,
        emit_bilingual: bool = False,
        overwrite: bool = False,
    ) -> tuple[Path, Path, Path, Path]:
        planned = self.planned_outputs(
            primary_output_path,
            ja_output_path,
            zh_output_path,
            bilingual_output_path,
            emit_ja=emit_ja,
            emit_zh=emit_zh,
            emit_bilingual=emit_bilingual,
        )
        if planned and all(path.exists() for path in planned) and not overwrite:
            return primary_output_path, ja_output_path, zh_output_path, bilingual_output_path

        payload = load_json(translated_json_path)
        segments = payload.get('segments', [])

        ja_lines = [BY_TAG]
        zh_lines = [BY_TAG]
        bilingual_lines = [BY_TAG]
        for segment in segments:
            timestamp = format_lrc_timestamp(float(segment['start']))
            ja_text = str(segment['text']).strip()
            zh_text = str(segment.get('translation', '')).strip()
            ja_lines.append(f'{timestamp}{ja_text}')
            zh_lines.append(f'{timestamp}{zh_text}')
            bilingual_lines.append(f'{timestamp}{ja_text} / {zh_text}')

        primary_lines = {
            'ja': ja_lines,
            'zh': zh_lines,
            'bilingual': bilingual_lines,
        }.get(primary_variant, zh_lines)
        _write_lines(primary_output_path, primary_lines)
        if emit_ja:
            _write_lines(ja_output_path, ja_lines)
        elif overwrite:
            _remove_if_possible(ja_output_path)
        if emit_zh:
            _write_lines(zh_output_path, zh_lines)
        elif overwrite:
            _remove_if_possible(zh_output_path)
        if emit_bilingual:
            _write_lines(bilingual_output_path, bilingual_lines)
        elif overwrite:
            _remove_if_possible(bilingual_output_path)
        return primary_output_path, ja_output_path, zh_output_path, bilingual_output_path


def format_lrc_timestamp(seconds: float) -> str:
    total_centiseconds = int(round(seconds * 100))
    minutes, centiseconds = divmod(total_centiseconds, 6000)
    seconds_part, centiseconds_part = divmod(centiseconds, 100)
    return f'[{minutes:02d}:{seconds_part:02d}.{centiseconds_part:02d}]'


def _write_lines(path: Path, lines: list[str]) -> None:
    ensure_parent_dir(path)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _remove_if_possible(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        # Stale sidecars should not block rewriting the primary subtitle.
        return
