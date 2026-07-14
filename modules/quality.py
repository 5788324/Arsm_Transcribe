from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from modules.io_utils import load_json

KANA_RE = re.compile(r'[\u3040-\u30ff]')


def inspect_translated_file(path: Path, *, max_chars: int = 45) -> list[dict[str, str]]:
    if not path.exists():
        return [{'code': 'missing_translation', 'severity': 'warning', 'message': '缺少 translated.json，无法检查字幕质量'}]
    try:
        payload = load_json(path)
    except Exception as exc:
        return [{'code': 'invalid_translation_json', 'severity': 'error', 'message': f'translated.json 无法读取：{exc}'}]
    segments = payload.get('segments', [])
    flags: list[dict[str, str]] = []
    previous_start = -1.0
    previous_end = -1.0
    empty_count = 0
    japanese_count = 0
    long_count = 0
    overlap_count = 0
    for index, segment in enumerate(segments, start=1):
        start = float(segment.get('start', 0))
        end = float(segment.get('end', start))
        translation = str(segment.get('translation', '')).strip()
        if not translation:
            empty_count += 1
        if KANA_RE.search(translation):
            japanese_count += 1
        if len(translation) > max_chars:
            long_count += 1
        if end < start or start < previous_start:
            flags.append({'code': 'invalid_timeline', 'severity': 'error', 'message': f'第 {index} 行时间轴倒序或结束早于开始'})
        elif previous_end > start + 0.2:
            overlap_count += 1
        previous_start, previous_end = start, end
    if not segments:
        flags.append({'code': 'empty_segments', 'severity': 'error', 'message': '字幕没有任何有效分段'})
    if empty_count:
        flags.append({'code': 'empty_translation', 'severity': 'error', 'message': f'{empty_count} 行中文翻译为空'})
    if japanese_count:
        flags.append({'code': 'japanese_residue', 'severity': 'warning', 'message': f'{japanese_count} 行中文结果仍包含日文假名'})
    if long_count:
        flags.append({'code': 'long_line', 'severity': 'warning', 'message': f'{long_count} 行超过 {max_chars} 字'})
    if overlap_count:
        flags.append({'code': 'timeline_overlap', 'severity': 'warning', 'message': f'{overlap_count} 处时间轴重叠超过 0.2 秒'})
    for gap in payload.get('suspected_gaps', []):
        duration = float(gap.get('duration', 0))
        flags.append({'code': 'long_silence', 'severity': 'info', 'message': f'疑似长静音 {duration:.1f} 秒，待人工试听'})
    return flags
