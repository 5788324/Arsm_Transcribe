from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from modules.asr.faster_whisper_backend import FasterWhisperBackend
from modules.asr.qwen3_asr_backend import Qwen3ASRBackend
from modules.batch import BatchProcessor
from modules.io_utils import (
    bilingual_lrc_path, clean_transcript_path, dump_json, is_valid_json_file,
    ja_lrc_path, load_json, primary_lrc_path, primary_vtt_path,
    raw_transcript_path, translated_transcript_path, zh_lrc_path, zh_vtt_path,
)
from modules.lrc_writer import LRCWriter
from modules.segment_cleaner import SegmentCleaner
from modules.subtitle_sources import SubtitleImporter, select_subtitle_strategy
from modules.translate import Translator
from modules.vtt_writer import VTTWriter

BACKENDS = {'faster_whisper': FasterWhisperBackend, 'qwen3_asr': Qwen3ASRBackend}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = load_config(Path(args.config))

    stage_commands = {
        'run-single': lambda: run_single(Path(args.audio), config, overwrite=args.overwrite),
        'transcribe': lambda: run_transcribe(Path(args.audio), config, overwrite=args.overwrite),
        'clean': lambda: run_clean(Path(args.audio), config, overwrite=args.overwrite),
        'translate': lambda: run_translate(Path(args.audio), config, overwrite=args.overwrite),
        'write-lrc': lambda: run_write_subtitles(Path(args.audio), config, overwrite=args.overwrite),
    }
    if args.command in stage_commands:
        stage_commands[args.command]()
        return 0
    if args.command == 'run-batch':
        summary = run_batch([Path(value) for value in args.roots], config, overwrite=args.overwrite)
        print(f'batch summary: total={summary.total}, succeeded={summary.succeeded}, skipped={summary.skipped}, failed={summary.failed}')
        return 0 if summary.failed == 0 else 1
    if args.command == 'retry-failed':
        summary = run_retry_failed(config, overwrite=args.overwrite)
        print(f'retry summary: total={summary.total}, succeeded={summary.succeeded}, skipped={summary.skipped}, failed={summary.failed}')
        return 0 if summary.failed == 0 else 1

    from modules.engine import EngineService
    service = EngineService(config)
    if args.command == 'scan':
        result = service.scan([Path(value) for value in args.roots])
    elif args.command == 'plan':
        result = service.plan([Path(value) for value in args.roots])
    elif args.command == 'list-works':
        result = service.list_works(search=args.search, action=args.action)
    elif args.command == 'list-media':
        result = service.list_media(args.work_id)
    elif args.command == 'enqueue':
        result = service.enqueue(work_ids=args.work_id or None, actions=args.action or None)
    elif args.command == 'jobs':
        result = service.jobs()
    elif args.command in {'pause', 'resume', 'cancel-job'}:
        state = {'pause': 'paused', 'resume': 'running', 'cancel-job': 'cancelled'}[args.command]
        result = service.set_job_state(args.job_id, state)
    elif args.command == 'review':
        result = service.review(args.media_id or None)
    elif args.command == 'profiles':
        result = service.profiles()
    elif args.command == 'glossary':
        result = service.glossary(args.work_id)
    elif args.command == 'add-term':
        result = service.save_glossary(args.source, args.target, args.work_id)
    elif args.command == 'status':
        result = service.status()
    elif args.command == 'cancel':
        result = service.cancel()
    elif args.command == 'doctor':
        result = service.doctor()
    else:
        parser.error(f'unsupported command: {args.command}')
        return 2
    _emit_result(result, Path(args.output) if getattr(args, 'output', None) else None)
    return 0 if result.get('ok', False) else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Local RJ audio to Chinese LRC/VTT pipeline')
    parser.add_argument('--config', default='config.yaml', help='Path to config.yaml')
    subparsers = parser.add_subparsers(dest='command', required=True)
    for command_name in ('run-single', 'transcribe', 'clean', 'translate', 'write-lrc'):
        subparser = subparsers.add_parser(command_name)
        subparser.add_argument('audio')
        subparser.add_argument('--overwrite', action='store_true')
    batch_parser = subparsers.add_parser('run-batch')
    batch_parser.add_argument('roots', nargs='+')
    batch_parser.add_argument('--overwrite', action='store_true')
    retry_parser = subparsers.add_parser('retry-failed')
    retry_parser.add_argument('--overwrite', action='store_true')
    for command_name in ('scan', 'plan'):
        subparser = subparsers.add_parser(command_name)
        subparser.add_argument('roots', nargs='+')
        subparser.add_argument('--output')
    works = subparsers.add_parser('list-works')
    works.add_argument('--search', default='')
    works.add_argument('--action', default='')
    works.add_argument('--output')
    media = subparsers.add_parser('list-media')
    media.add_argument('work_id', type=int)
    media.add_argument('--output')
    enqueue = subparsers.add_parser('enqueue')
    enqueue.add_argument('--work-id', type=int, action='append')
    enqueue.add_argument('--action', action='append')
    enqueue.add_argument('--output')
    subparsers.add_parser('jobs').add_argument('--output')
    for command_name in ('pause', 'resume', 'cancel-job'):
        subparser = subparsers.add_parser(command_name)
        subparser.add_argument('job_id', type=int)
        subparser.add_argument('--output')
    review = subparsers.add_parser('review')
    review.add_argument('--media-id', type=int, action='append')
    review.add_argument('--output')
    subparsers.add_parser('profiles').add_argument('--output')
    glossary = subparsers.add_parser('glossary')
    glossary.add_argument('--work-id', type=int)
    glossary.add_argument('--output')
    term = subparsers.add_parser('add-term')
    term.add_argument('source')
    term.add_argument('target')
    term.add_argument('--work-id', type=int)
    term.add_argument('--output')
    for command_name in ('status', 'cancel', 'doctor'):
        subparsers.add_parser(command_name).add_argument('--output')
    return parser

def load_config(path: Path) -> dict[str, Any]:
    with path.open('r', encoding='utf-8-sig') as handle:
        config = yaml.safe_load(handle) or {}
    paths_config = config.setdefault('paths', {})
    for key in ('cache_dir', 'output_dir', 'log_dir', 'database_path'):
        if key in paths_config:
            candidate = Path(paths_config[key]).expanduser()
            if not candidate.is_absolute():
                paths_config[key] = str((path.resolve().parent / candidate).resolve())
    cache_dir = Path(paths_config.get('cache_dir', path.resolve().parent / 'cache')).expanduser()
    if not cache_dir.is_absolute():
        cache_dir = (path.resolve().parent / cache_dir).resolve()
    active_path = cache_dir / 'active_profiles.json'
    if active_path.exists():
        try:
            active = json.loads(active_path.read_text(encoding='utf-8-sig')).get('profiles', {})
        except Exception:
            active = {}
        for kind in ('asr', 'translate'):
            if isinstance(active.get(kind), dict):
                config[kind] = active[kind]
    return config

def run_single(audio_path: Path, config: dict[str, Any], *, overwrite: bool) -> str:
    audio = audio_path.expanduser().resolve()
    raw_json = raw_transcript_path(_cache_dir(config), audio)
    clean_json = clean_transcript_path(_cache_dir(config), audio)
    translated_json = translated_transcript_path(_cache_dir(config), audio)
    lrc_targets = _lrc_targets(audio, config)
    vtt_targets = _vtt_targets(audio, config)
    strategy = select_subtitle_strategy(audio, overwrite=overwrite)

    if strategy['action'] == 'skip_existing_lrc' and not overwrite:
        return 'skipped'
    if strategy['action'] == 'manual_review':
        return 'skipped'

    writer = LRCWriter()
    planned_lrc = writer.planned_outputs(
        *lrc_targets,
        emit_ja=bool(config.get('lrc', {}).get('emit_ja_lrc', False)),
        emit_zh=bool(config.get('lrc', {}).get('emit_zh_lrc', False)),
        emit_bilingual=bool(config.get('lrc', {}).get('emit_bilingual_lrc', False)),
    )
    if not overwrite and all(is_valid_json_file(path) for path in (raw_json, clean_json, translated_json)) and all(path.exists() for path in planned_lrc) and any(path.exists() for path in vtt_targets):
        return 'skipped'

    source_path: Path | None = None
    if strategy['action'] in {'translate_existing_subtitle', 'convert_existing_subtitle'}:
        source_path = Path(strategy['source_path'])
        raw_path = SubtitleImporter().import_to_raw_json(audio, source_path, raw_json, overwrite=overwrite)
    else:
        raw_path = run_transcribe(audio, config, overwrite=overwrite)
    clean_path = run_clean(audio, config, overwrite=overwrite, raw_path=raw_path)
    if strategy['action'] == 'convert_existing_subtitle':
        translated_path = _copy_as_chinese_translation(clean_path, translated_json, overwrite=overwrite)
    else:
        translated_path = run_translate(audio, config, overwrite=overwrite, clean_path=clean_path)
    run_write_subtitles(audio, config, overwrite=overwrite, translated_path=translated_path, source_path=source_path)
    return 'processed'


def run_batch(roots: list[Path], config: dict[str, Any], *, overwrite: bool):
    return BatchProcessor(config, lambda path, overwrite=False: run_single(path, config, overwrite=overwrite)).run(roots, overwrite=overwrite)


def run_retry_failed(config: dict[str, Any], *, overwrite: bool):
    return BatchProcessor(config, lambda path, overwrite=False: run_single(path, config, overwrite=overwrite)).retry_failed(overwrite=overwrite)


def run_transcribe(audio_path: Path, config: dict[str, Any], *, overwrite: bool) -> Path:
    audio = audio_path.expanduser().resolve()
    backend_name = config.get('asr', {}).get('backend', 'faster_whisper')
    backend_cls = BACKENDS.get(backend_name)
    if backend_cls is None:
        raise ValueError(f'unsupported ASR backend: {backend_name}')
    return backend_cls().transcribe_to_json(audio, raw_transcript_path(_cache_dir(config), audio), config, overwrite=overwrite)


def run_clean(audio_path: Path, config: dict[str, Any], *, overwrite: bool, raw_path: Path | None = None) -> Path:
    audio = audio_path.expanduser().resolve()
    raw_json = raw_path or raw_transcript_path(_cache_dir(config), audio)
    return SegmentCleaner(config).clean_file(raw_json, clean_transcript_path(_cache_dir(config), audio), overwrite=overwrite)


def run_translate(audio_path: Path, config: dict[str, Any], *, overwrite: bool, clean_path: Path | None = None) -> Path:
    audio = audio_path.expanduser().resolve()
    clean_json = clean_path or clean_transcript_path(_cache_dir(config), audio)
    return Translator(config).translate_file(clean_json, translated_transcript_path(_cache_dir(config), audio), overwrite=overwrite)


def run_write_subtitles(audio_path: Path, config: dict[str, Any], *, overwrite: bool, translated_path: Path | None = None, source_path: Path | None = None) -> tuple[Path, Path]:
    audio = audio_path.expanduser().resolve()
    translated = translated_path or translated_transcript_path(_cache_dir(config), audio)
    lrc_targets = _lrc_targets(audio, config)
    primary_lrc_target = lrc_targets[0]
    if source_path is not None and source_path.suffix.lower() == '.lrc' and _same_path(source_path, primary_lrc_target):
        primary_lrc_target = lrc_targets[2]
    LRCWriter().write_file(
        translated, primary_lrc_target, lrc_targets[1], lrc_targets[2], lrc_targets[3],
        primary_variant=str(config.get('lrc', {}).get('primary_variant', 'zh')),
        emit_ja=bool(config.get('lrc', {}).get('emit_ja_lrc', False)),
        emit_zh=bool(config.get('lrc', {}).get('emit_zh_lrc', False)),
        emit_bilingual=bool(config.get('lrc', {}).get('emit_bilingual_lrc', False)),
        overwrite=overwrite,
    )
    vtt_target = VTTWriter().write_file(translated, *_vtt_targets(audio, config), source_path=source_path, overwrite=overwrite)
    return primary_lrc_target, vtt_target


def run_write_lrc(audio_path: Path, config: dict[str, Any], *, overwrite: bool, translated_path: Path | None = None):
    return run_write_subtitles(audio_path, config, overwrite=overwrite, translated_path=translated_path)


def _cache_dir(config: dict[str, Any]) -> Path:
    return Path(config.get('paths', {}).get('cache_dir', 'cache')).expanduser().resolve()


def _output_dir(audio: Path, config: dict[str, Any]) -> Path:
    if str(config.get('lrc', {}).get('output_mode', 'same_directory')) == 'same_directory':
        return audio.parent
    return Path(config.get('paths', {}).get('output_dir', 'output')).expanduser().resolve()


def _lrc_targets(audio: Path, config: dict[str, Any]) -> tuple[Path, Path, Path, Path]:
    output_dir = _output_dir(audio, config)
    return primary_lrc_path(output_dir, audio), ja_lrc_path(output_dir, audio), zh_lrc_path(output_dir, audio), bilingual_lrc_path(output_dir, audio)


def _vtt_targets(audio: Path, config: dict[str, Any]) -> tuple[Path, Path]:
    output_dir = _output_dir(audio, config)
    return primary_vtt_path(output_dir, audio), zh_vtt_path(output_dir, audio)


def _copy_as_chinese_translation(clean_path: Path, translated_path: Path, *, overwrite: bool) -> Path:
    if is_valid_json_file(translated_path) and not overwrite:
        return translated_path
    payload = load_json(clean_path)
    for segment in payload.get('segments', []):
        segment['translation'] = str(segment.get('text', '')).strip()
    payload['translation_provider'] = 'existing_chinese_subtitle'
    dump_json(translated_path, payload)
    return translated_path


def _same_path(left: Path, right: Path) -> bool:
    return str(left.resolve()).casefold() == str(right.resolve()).casefold()

def _emit_result(payload: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output is not None:
        from modules.io_utils import atomic_write_text
        atomic_write_text(output.expanduser().resolve(), text + '\n')
    print(text)


if __name__ == '__main__':
    raise SystemExit(main())
