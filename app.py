from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from modules.asr.faster_whisper_backend import FasterWhisperBackend
from modules.asr.qwen3_asr_backend import Qwen3ASRBackend
from modules.batch import BatchProcessor
from modules.io_utils import (
    bilingual_lrc_path,
    clean_transcript_path,
    is_valid_json_file,
    ja_lrc_path,
    primary_lrc_path,
    raw_transcript_path,
    translated_transcript_path,
    zh_lrc_path,
)
from modules.lrc_writer import LRCWriter
from modules.segment_cleaner import SegmentCleaner
from modules.subtitle_sources import SubtitleImporter, select_subtitle_strategy
from modules.translate import Translator


BACKENDS = {
    'faster_whisper': FasterWhisperBackend,
    'qwen3_asr': Qwen3ASRBackend,
}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(Path(args.config))

    if args.command == 'run-single':
        run_single(Path(args.audio), config, overwrite=args.overwrite)
        return 0
    if args.command == 'transcribe':
        run_transcribe(Path(args.audio), config, overwrite=args.overwrite)
        return 0
    if args.command == 'clean':
        run_clean(Path(args.audio), config, overwrite=args.overwrite)
        return 0
    if args.command == 'translate':
        run_translate(Path(args.audio), config, overwrite=args.overwrite)
        return 0
    if args.command == 'write-lrc':
        run_write_lrc(Path(args.audio), config, overwrite=args.overwrite)
        return 0
    if args.command == 'run-batch':
        summary = run_batch([Path(value) for value in args.roots], config, overwrite=args.overwrite)
        print(f'?????: total={summary.total}, succeeded={summary.succeeded}, skipped={summary.skipped}, failed={summary.failed}')
        return 0 if summary.failed == 0 else 1

    parser.error(f'unsupported command: {args.command}')
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='RJ transcript to bilingual LRC pipeline')
    parser.add_argument('--config', default='config.yaml', help='Path to config.yaml')
    subparsers = parser.add_subparsers(dest='command', required=True)

    for command_name in ('run-single', 'transcribe', 'clean', 'translate', 'write-lrc'):
        subparser = subparsers.add_parser(command_name)
        subparser.add_argument('audio', help='Path to one audio file')
        subparser.add_argument('--overwrite', action='store_true')

    batch_parser = subparsers.add_parser('run-batch')
    batch_parser.add_argument('roots', nargs='+', help='One or more root folders to scan recursively')
    batch_parser.add_argument('--overwrite', action='store_true')
    return parser


def load_config(path: Path) -> dict[str, Any]:
    with path.open('r', encoding='utf-8') as handle:
        return yaml.safe_load(handle) or {}


def run_single(audio_path: Path, config: dict[str, Any], *, overwrite: bool) -> str:
    resolved_audio_path = audio_path.expanduser().resolve()
    raw_json_path = raw_transcript_path(_cache_dir(config), resolved_audio_path)
    clean_json_path = clean_transcript_path(_cache_dir(config), resolved_audio_path)
    translated_json_path = translated_transcript_path(_cache_dir(config), resolved_audio_path)
    primary_path, ja_path, zh_path, bilingual_path = _lrc_targets(resolved_audio_path, config)

    strategy = select_subtitle_strategy(resolved_audio_path, overwrite=overwrite)
    if strategy['action'] == 'skip_existing_lrc' and not overwrite:
        return 'skipped'

    writer = LRCWriter()
    planned_outputs = writer.planned_outputs(
        primary_path,
        ja_path,
        zh_path,
        bilingual_path,
        emit_ja=bool(config.get('lrc', {}).get('emit_ja_lrc', False)),
        emit_zh=bool(config.get('lrc', {}).get('emit_zh_lrc', False)),
        emit_bilingual=bool(config.get('lrc', {}).get('emit_bilingual_lrc', False)),
    )
    outputs_exist = (
        is_valid_json_file(raw_json_path)
        and is_valid_json_file(clean_json_path)
        and is_valid_json_file(translated_json_path)
        and all(path.exists() for path in planned_outputs)
    )
    if outputs_exist and not overwrite:
        return 'skipped'

    if strategy['action'] == 'translate_existing_subtitle':
        importer = SubtitleImporter()
        source_path = Path(strategy['source_path'])
        raw_path = importer.import_to_raw_json(resolved_audio_path, source_path, raw_json_path, overwrite=overwrite)
    else:
        raw_path = run_transcribe(resolved_audio_path, config, overwrite=overwrite)

    clean_path = run_clean(resolved_audio_path, config, overwrite=overwrite, raw_path=raw_path)
    translated_path = run_translate(resolved_audio_path, config, overwrite=overwrite, clean_path=clean_path)
    run_write_lrc(resolved_audio_path, config, overwrite=overwrite, translated_path=translated_path)
    return 'processed'


def run_batch(roots: list[Path], config: dict[str, Any], *, overwrite: bool):
    processor = BatchProcessor(config, lambda audio_path, overwrite=False: run_single(audio_path, config, overwrite=overwrite))
    return processor.run(roots, overwrite=overwrite)


def run_transcribe(audio_path: Path, config: dict[str, Any], *, overwrite: bool) -> Path:
    audio_path = audio_path.expanduser().resolve()
    backend_name = config.get('asr', {}).get('backend', 'faster_whisper')
    backend_cls = BACKENDS.get(backend_name)
    if backend_cls is None:
        raise ValueError(f'unsupported ASR backend: {backend_name}')

    output_path = raw_transcript_path(_cache_dir(config), audio_path)
    backend = backend_cls()
    return backend.transcribe_to_json(audio_path, output_path, config, overwrite=overwrite)


def run_clean(
    audio_path: Path,
    config: dict[str, Any],
    *,
    overwrite: bool,
    raw_path: Path | None = None,
) -> Path:
    audio_path = audio_path.expanduser().resolve()
    raw_json_path = raw_path or raw_transcript_path(_cache_dir(config), audio_path)
    clean_json_path = clean_transcript_path(_cache_dir(config), audio_path)
    cleaner = SegmentCleaner(config)
    return cleaner.clean_file(raw_json_path, clean_json_path, overwrite=overwrite)


def run_translate(
    audio_path: Path,
    config: dict[str, Any],
    *,
    overwrite: bool,
    clean_path: Path | None = None,
) -> Path:
    audio_path = audio_path.expanduser().resolve()
    clean_json_path = clean_path or clean_transcript_path(_cache_dir(config), audio_path)
    translated_json_path = translated_transcript_path(_cache_dir(config), audio_path)
    translator = Translator(config)
    return translator.translate_file(clean_json_path, translated_json_path, overwrite=overwrite)


def run_write_lrc(
    audio_path: Path,
    config: dict[str, Any],
    *,
    overwrite: bool,
    translated_path: Path | None = None,
) -> tuple[Path, Path, Path, Path]:
    audio_path = audio_path.expanduser().resolve()
    translated_json_path = translated_path or translated_transcript_path(_cache_dir(config), audio_path)
    primary_output_path, ja_output_path, zh_output_path, bilingual_output_path = _lrc_targets(audio_path, config)

    writer = LRCWriter()
    return writer.write_file(
        translated_json_path,
        primary_output_path,
        ja_output_path,
        zh_output_path,
        bilingual_output_path,
        primary_variant=str(config.get('lrc', {}).get('primary_variant', 'zh')),
        emit_ja=bool(config.get('lrc', {}).get('emit_ja_lrc', False)),
        emit_zh=bool(config.get('lrc', {}).get('emit_zh_lrc', False)),
        emit_bilingual=bool(config.get('lrc', {}).get('emit_bilingual_lrc', False)),
        overwrite=overwrite,
    )


def _cache_dir(config: dict[str, Any]) -> Path:
    return Path(config.get('paths', {}).get('cache_dir', 'cache')).expanduser().resolve()


def _lrc_targets(audio_path: Path, config: dict[str, Any]) -> tuple[Path, Path, Path, Path]:
    output_mode = str(config.get('lrc', {}).get('output_mode', 'same_directory'))
    if output_mode == 'same_directory':
        output_dir = audio_path.parent
    else:
        output_dir = Path(config.get('paths', {}).get('output_dir', 'output')).expanduser().resolve()
    return (
        primary_lrc_path(output_dir, audio_path),
        ja_lrc_path(output_dir, audio_path),
        zh_lrc_path(output_dir, audio_path),
        bilingual_lrc_path(output_dir, audio_path),
    )


if __name__ == '__main__':
    raise SystemExit(main())
