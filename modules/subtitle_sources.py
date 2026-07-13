from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from modules.asr.base import normalize_segment
from modules.asr.faster_whisper_backend import FasterWhisperBackend
from modules.io_utils import dump_json, is_valid_json_file

TIMECODE_RE = re.compile(
    r'^(?:(?P<hh>\d{2}):)?(?P<mm>\d{2}):(?P<ss>\d{2})[\.,](?P<ms>\d{3})\s+-->\s+'
    r'(?:(?P<hh2>\d{2}):)?(?P<mm2>\d{2}):(?P<ss2>\d{2})[\.,](?P<ms2>\d{3})'
)
LRC_TEXT_RE = re.compile(r'^\[(?:\d{2}:\d{2}\.\d{2}|\d{2}:\d{2}\.\d{3})\](.*)$')
LRC_TAG_RE = re.compile(r'^\[(ar|ti|al|by|offset|re|ve):.*\]$', re.IGNORECASE)
HIRAGANA_KATAKANA_RE = re.compile(r'[\u3040-\u30ff]')
CJK_RE = re.compile(r'[\u4e00-\u9fff]')
OUR_BY_TAG = '[by: yang \u521b\u5efa]'


def select_subtitle_strategy(audio_path: Path, *, overwrite: bool) -> dict[str, Any]:
    parent = audio_path.parent
    primary_lrc = parent / f'{audio_path.stem}.lrc'
    candidates = [
        parent / f'{audio_path.name}.vtt',
        parent / f'{audio_path.name}.srt',
        parent / f'{audio_path.stem}.ja.lrc',
        parent / f'{audio_path.stem}.vtt',
        parent / f'{audio_path.stem}.srt',
    ]

    if primary_lrc.exists() and not overwrite:
        analysis = analyze_subtitle_file(primary_lrc)
        if analysis['kind'] == 'japanese_source':
            return _strategy('translate_existing_subtitle', primary_lrc, 'existing primary LRC is Japanese')
        return _strategy('skip_existing_lrc', primary_lrc, f'existing primary LRC kept ({analysis["kind"]})')

    for candidate in candidates:
        if not candidate.exists() or candidate == primary_lrc:
            continue
        kind = analyze_subtitle_file(candidate)['kind']
        if kind == 'japanese_source':
            return _strategy('translate_existing_subtitle', candidate, 'reuse Japanese timed subtitle')
        if kind in {'chinese_or_non_japanese', 'bilingual', 'ours_bilingual'}:
            return _strategy('convert_existing_subtitle', candidate, 'reuse Chinese/non-Japanese timed subtitle')
        return _strategy('manual_review', candidate, 'timed subtitle language is unknown')

    return _strategy('transcribe_audio', None, 'no reusable timed subtitle found')


def _strategy(action: str, source_path: Path | None, reason: str) -> dict[str, Any]:
    return {'action': action, 'source_path': source_path, 'reason': reason}

class SubtitleImporter:
    def import_to_raw_json(
        self,
        audio_path: Path,
        source_path: Path,
        raw_json_path: Path,
        *,
        overwrite: bool = False,
    ) -> Path:
        if is_valid_json_file(raw_json_path) and not overwrite:
            return raw_json_path

        suffix = source_path.suffix.lower()
        if suffix == '.lrc':
            segments = FasterWhisperBackend.parse_lrc(source_path)
        elif suffix == '.vtt':
            segments = self._parse_vtt_or_srt(source_path, is_vtt=True)
        elif suffix == '.srt':
            segments = self._parse_vtt_or_srt(source_path, is_vtt=False)
        else:
            raise ValueError(f'unsupported subtitle source: {source_path}')

        payload: dict[str, Any] = {
            'audio_path': str(audio_path),
            'backend': 'existing_subtitle_import',
            'source_subtitle_path': str(source_path),
            'segment_count': len(segments),
            'segments': segments,
        }
        dump_json(raw_json_path, payload)
        return raw_json_path

    def _parse_vtt_or_srt(self, source_path: Path, *, is_vtt: bool) -> list[dict[str, Any]]:
        lines = source_path.read_text(encoding='utf-8-sig').splitlines()
        index = 0
        segments: list[dict[str, Any]] = []

        while index < len(lines):
            line = lines[index].strip()
            index += 1
            if not line:
                continue
            if is_vtt and line == 'WebVTT':
                continue
            if line.isdigit():
                if index >= len(lines):
                    break
                line = lines[index].strip()
                index += 1

            match = TIMECODE_RE.match(line)
            if not match:
                continue

            text_lines: list[str] = []
            while index < len(lines) and lines[index].strip():
                text_lines.append(lines[index].strip())
                index += 1

            text = ' '.join(text_lines).strip()
            if not text:
                continue

            segments.append(
                normalize_segment(
                    {
                        'start': _timecode_to_seconds(match.group('hh'), match.group('mm'), match.group('ss'), match.group('ms')),
                        'end': _timecode_to_seconds(match.group('hh2'), match.group('mm2'), match.group('ss2'), match.group('ms2')),
                        'text': text,
                        'confidence': None,
                    }
                )
            )
        return segments


def analyze_subtitle_file(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    if OUR_BY_TAG in text:
        return {'kind': 'ours_bilingual'}

    extracted_lines = _extract_text_lines(path, text, suffix)
    if not extracted_lines:
        return {'kind': 'unknown'}

    slash_lines = sum(1 for line in extracted_lines if ' / ' in line)
    kana_lines = sum(1 for line in extracted_lines if HIRAGANA_KATAKANA_RE.search(line))
    cjk_lines = sum(1 for line in extracted_lines if CJK_RE.search(line))

    if slash_lines >= max(1, len(extracted_lines) // 3):
        return {'kind': 'bilingual'}
    if kana_lines > 0:
        return {'kind': 'japanese_source'}
    if cjk_lines > 0:
        return {'kind': 'chinese_or_non_japanese'}
    return {'kind': 'unknown'}


def _extract_text_lines(path: Path, text: str, suffix: str) -> list[str]:
    lines = text.splitlines()
    extracted: list[str] = []
    if suffix == '.lrc':
        for raw in lines:
            line = raw.strip()
            if not line or LRC_TAG_RE.match(line):
                continue
            match = LRC_TEXT_RE.match(line)
            if match:
                payload = match.group(1).strip()
                if payload:
                    extracted.append(payload)
        return extracted

    if suffix in {'.vtt', '.srt'}:
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            index += 1
            if not line or line == 'WebVTT':
                continue
            if line.isdigit() and index < len(lines):
                line = lines[index].strip()
                index += 1
            match = TIMECODE_RE.match(line)
            if not match:
                continue
            block: list[str] = []
            while index < len(lines) and lines[index].strip():
                block.append(lines[index].strip())
                index += 1
            payload = ' '.join(block).strip()
            if payload:
                extracted.append(payload)
        return extracted

    return []


def _timecode_to_seconds(hours: str | None, minutes: str, seconds: str, milliseconds: str) -> float:
    hh = int(hours or 0)
    mm = int(minutes)
    ss = int(seconds)
    ms = int(milliseconds)
    return hh * 3600 + mm * 60 + ss + ms / 1000.0
