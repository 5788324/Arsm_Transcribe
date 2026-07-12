from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, TypedDict

from modules.io_utils import dump_json, is_valid_json_file


class TranscriptSegment(TypedDict):
    start: float
    end: float
    text: str
    confidence: float | None


class ASRBackend(ABC):
    name: str

    @abstractmethod
    def transcribe(self, audio_path: Path, config: dict[str, Any]) -> list[TranscriptSegment]:
        """Return normalized transcript segments."""


    def transcribe_to_json(
        self,
        audio_path: Path,
        output_path: Path,
        config: dict[str, Any],
        *,
        overwrite: bool = False,
    ) -> Path:
        if is_valid_json_file(output_path) and not overwrite:
            return output_path

        segments = self.transcribe(audio_path, config)
        payload: dict[str, Any] = {
            "audio_path": str(audio_path),
            "backend": self.name,
            "segment_count": len(segments),
            "segments": segments,
        }
        dump_json(output_path, payload)
        return output_path



def normalize_segment(segment: dict[str, Any]) -> TranscriptSegment:
    start = float(segment["start"])
    end = float(segment["end"])
    if end < start:
        raise ValueError(f"segment end before start: {segment}")

    text = str(segment["text"]).strip()
    if not text:
        raise ValueError("segment text cannot be empty")

    confidence = segment.get("confidence")
    normalized_confidence = None if confidence is None else float(confidence)

    return TranscriptSegment(
        start=round(start, 3),
        end=round(end, 3),
        text=text,
        confidence=normalized_confidence,
    )
