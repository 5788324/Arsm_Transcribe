from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from modules.io_utils import append_log_line, dump_json, is_valid_json_file, load_json

PRIMARY_SPLIT_RE = re.compile(r"(?<=[。！？!?…])")
SECONDARY_SPLIT_RE = re.compile(r"(?<=[、，,])")


class SegmentCleaner:
    def __init__(self, config: dict[str, Any]) -> None:
        cleaner_config = config.get("segment_cleaner", {})
        lrc_config = config.get("lrc", {})
        self.merge_gap_seconds = float(cleaner_config.get("merge_gap_seconds", 0.5))
        self.long_silence_seconds = float(cleaner_config.get("long_silence_seconds", 10.0))
        self.max_chars_per_line = int(lrc_config.get("max_chars_per_line", 45))

    def clean_file(self, raw_json_path: Path, clean_json_path: Path, *, overwrite: bool = False) -> Path:
        if is_valid_json_file(clean_json_path) and not overwrite:
            return clean_json_path

        payload = load_json(raw_json_path)
        segments = payload.get("segments", [])
        merged = self._merge_short_segments(segments)
        split_segments = self._split_long_segments(merged)
        warnings = self._detect_long_silences(split_segments)

        cleaned_payload: dict[str, Any] = {
            "audio_path": payload.get("audio_path"),
            "source_raw_json": str(raw_json_path),
            "segment_count": len(split_segments),
            "warnings": warnings,
            "segments": split_segments,
        }
        dump_json(clean_json_path, cleaned_payload)
        return clean_json_path

    def _merge_short_segments(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not segments:
            return []

        merged: list[dict[str, Any]] = []
        current = dict(segments[0])
        for next_segment in segments[1:]:
            gap = float(next_segment["start"]) - float(current["end"])
            merged_text = f"{current['text']}{next_segment['text']}"
            if gap <= self.merge_gap_seconds and len(merged_text) <= self.max_chars_per_line:
                current["end"] = float(next_segment["end"])
                current["text"] = merged_text
                current["confidence"] = _merge_confidence(current.get("confidence"), next_segment.get("confidence"))
            else:
                merged.append(_round_segment(current))
                current = dict(next_segment)
        merged.append(_round_segment(current))
        return merged

    def _split_long_segments(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for segment in segments:
            text = str(segment["text"]).strip()
            if len(text) <= self.max_chars_per_line:
                result.append(_round_segment(segment))
                continue

            parts = _split_text(text, self.max_chars_per_line)
            durations = _allocate_ranges(float(segment["start"]), float(segment["end"]), parts)
            for part, (part_start, part_end) in zip(parts, durations):
                result.append(
                    _round_segment(
                        {
                            "start": part_start,
                            "end": part_end,
                            "text": part,
                            "confidence": segment.get("confidence"),
                        }
                    )
                )
        return result

    def _detect_long_silences(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        for current, following in zip(segments, segments[1:]):
            gap = round(float(following["start"]) - float(current["end"]), 3)
            if gap >= self.long_silence_seconds:
                warnings.append(
                    {
                        "type": "suspected_missing_voice_activity",
                        "gap_seconds": gap,
                        "after_text": current["text"],
                        "before_text": following["text"],
                        "start": float(current["end"]),
                        "end": float(following["start"]),
                    }
                )
        return warnings



def _merge_confidence(left: Any, right: Any) -> float | None:
    values = [float(value) for value in (left, right) if value is not None]
    if not values:
        return None
    return sum(values) / len(values)



def _split_text(text: str, max_chars: int) -> list[str]:
    for splitter in (PRIMARY_SPLIT_RE, SECONDARY_SPLIT_RE):
        parts = _split_by_regex(text, splitter, max_chars)
        if len(parts) > 1:
            return parts
    return [text[index:index + max_chars] for index in range(0, len(text), max_chars)]



def _split_by_regex(text: str, pattern: re.Pattern[str], max_chars: int) -> list[str]:
    tokens = [token.strip() for token in pattern.split(text) if token.strip()]
    if not tokens:
        return [text]

    merged_tokens: list[str] = []
    current = ""
    for token in tokens:
        candidate = f"{current}{token}"
        if current and len(candidate) > max_chars:
            merged_tokens.append(current)
            current = token
        else:
            current = candidate
    if current:
        merged_tokens.append(current)

    if len(merged_tokens) == 1 and len(merged_tokens[0]) > max_chars:
        return [text]
    return merged_tokens



def _allocate_ranges(start: float, end: float, parts: list[str]) -> list[tuple[float, float]]:
    if not parts:
        return []
    if end < start:
        raise ValueError(f"invalid segment duration: start={start}, end={end}")

    duration = max(end - start, 0.0)
    if duration == 0:
        return [(start, start) for _ in parts]

    total_weight = sum(max(len(part), 1) for part in parts)
    cursor = start
    ranges: list[tuple[float, float]] = []
    for index, part in enumerate(parts):
        weight = max(len(part), 1)
        if index == len(parts) - 1:
            next_cursor = end
        else:
            next_cursor = cursor + duration * weight / total_weight
        ranges.append((cursor, next_cursor))
        cursor = next_cursor
    return ranges



def _round_segment(segment: dict[str, Any]) -> dict[str, Any]:
    return {
        "start": round(float(segment["start"]), 3),
        "end": round(float(segment["end"]), 3),
        "text": str(segment["text"]).strip(),
        "confidence": None if segment.get("confidence") is None else round(float(segment["confidence"]), 4),
    }
