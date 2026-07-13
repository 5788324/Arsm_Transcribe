from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma', '.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv'}
REFERENCE_EXTENSIONS = {'.txt', '.pdf', '.vtt', '.srt', '.lrc'}
AUDIO_EXTENSION_PRIORITY = {
    '.wav': 0,
    '.flac': 1,
    '.m4a': 2,
    '.aac': 3,
    '.mp3': 4,
    '.ogg': 5,
    '.wma': 6,
    '.mp4': 7,
    '.mkv': 8,
    '.avi': 9,
    '.mov': 10,
    '.webm': 11,
    '.flv': 12,
    '.wmv': 13,
}


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    with path.open('r', encoding='utf-8') as handle:
        return json.load(handle)


def is_valid_json_file(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        load_json(path)
    except Exception:
        return False
    return True


def atomic_write_text(path: Path, content: str) -> None:
    ensure_parent_dir(path)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False, suffix='.tmp') as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def dump_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')


def append_log_line(path: Path, message: str) -> None:
    ensure_parent_dir(path)
    with path.open('a', encoding='utf-8') as handle:
        handle.write(message.rstrip() + '\n')


def stem_for_artifact(audio_path: Path) -> str:
    return audio_path.stem


def artifact_stem(audio_path: Path) -> str:
    safe_stem = _safe_name(audio_path.stem)
    parent_hint = _safe_name(audio_path.parent.name) or 'root'
    digest = hashlib.sha1(str(audio_path).encode('utf-8')).hexdigest()[:12]
    return f'{parent_hint}__{safe_stem}__{digest}'


def raw_transcript_path(cache_dir: Path, audio_path: Path) -> Path:
    return cache_dir / f'{artifact_stem(audio_path)}.transcript.raw.json'


def clean_transcript_path(cache_dir: Path, audio_path: Path) -> Path:
    return cache_dir / f'{artifact_stem(audio_path)}.transcript.clean.json'


def translated_transcript_path(cache_dir: Path, audio_path: Path) -> Path:
    return cache_dir / f'{artifact_stem(audio_path)}.translated.json'


def reference_scan_path(cache_dir: Path) -> Path:
    return cache_dir / 'has_reference_text.json'


def failed_log_path(log_dir: Path) -> Path:
    return log_dir / 'failed.txt'


def batch_status_path(log_dir: Path) -> Path:
    return log_dir / 'batch_status.json'


def cancel_request_path(log_dir: Path) -> Path:
    return log_dir / 'cancel.request.json'


def primary_lrc_path(output_dir: Path, audio_path: Path) -> Path:
    return output_dir / f'{stem_for_artifact(audio_path)}.lrc'


def ja_lrc_path(output_dir: Path, audio_path: Path) -> Path:
    return output_dir / f'{stem_for_artifact(audio_path)}.ja.lrc'


def zh_lrc_path(output_dir: Path, audio_path: Path) -> Path:
    return output_dir / f'{stem_for_artifact(audio_path)}.zh.lrc'


def bilingual_lrc_path(output_dir: Path, audio_path: Path) -> Path:
    return output_dir / f'{stem_for_artifact(audio_path)}.bilingual.lrc'


def primary_vtt_path(output_dir: Path, audio_path: Path) -> Path:
    return output_dir / f'{stem_for_artifact(audio_path)}.vtt'


def zh_vtt_path(output_dir: Path, audio_path: Path) -> Path:
    return output_dir / f'{stem_for_artifact(audio_path)}.zh.vtt'


def discover_audio_files(root: Path) -> list[Path]:
    """Return every supported media variant; WAV/MP3 siblings are distinct jobs."""
    return sorted(
        (path for path in root.rglob('*') if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS),
        key=lambda path: str(path).casefold(),
    )


def discover_reference_files(audio_path: Path) -> list[Path]:
    return sorted(
        path for path in audio_path.parent.iterdir()
        if path.is_file() and path.suffix.lower() in REFERENCE_EXTENSIONS
    )


def _safe_name(value: str) -> str:
    collapsed = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff._-]+', '_', value)
    return collapsed.strip('._-')[:80] or 'item'


def _audio_sort_key(path: Path) -> tuple[int, str]:
    return (AUDIO_EXTENSION_PRIORITY.get(path.suffix.lower(), 999), str(path).casefold())
