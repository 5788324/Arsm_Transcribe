from __future__ import annotations

from pathlib import Path
from typing import Any

from modules.asr.base import ASRBackend, TranscriptSegment


class Qwen3ASRBackend(ASRBackend):
    name = "qwen3_asr"

    def transcribe(self, audio_path: Path, config: dict[str, Any]) -> list[TranscriptSegment]:
        raise NotImplementedError(
            "Qwen3-ASR backend is intentionally not implemented in phase 1-4. "
            "It needs an external forced aligner because Qwen3-ASR does not emit timestamps by itself."
        )
