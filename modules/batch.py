from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import sys
from pathlib import Path
from typing import Any

from modules.io_utils import (
    append_log_line,
    batch_status_path,
    discover_audio_files,
    discover_reference_files,
    dump_json,
    failed_log_path,
    reference_scan_path,
)
from modules.subtitle_sources import select_subtitle_strategy


@dataclass
class BatchSummary:
    total: int
    succeeded: int
    failed: int
    skipped: int
    reference_items: list[dict[str, Any]]


class BatchProcessor:
    def __init__(self, config: dict[str, Any], pipeline: Any) -> None:
        self.config = config
        self.pipeline = pipeline
        paths_config = config.get('paths', {})
        self.cache_dir = Path(paths_config.get('cache_dir', 'cache')).expanduser().resolve()
        self.log_dir = Path(paths_config.get('log_dir', 'logs')).expanduser().resolve()

    def run(self, roots: list[Path], *, overwrite: bool = False) -> BatchSummary:
        resolved_roots = [root.expanduser().resolve() for root in roots]
        audio_files: list[Path] = []
        reference_items: list[dict[str, Any]] = []
        failed_path = failed_log_path(self.log_dir)
        status_path = batch_status_path(self.log_dir)
        failed_path.parent.mkdir(parents=True, exist_ok=True)
        if failed_path.exists():
            failed_path.unlink()

        started_at = _now_iso()
        self._write_status(
            status_path,
            state='scanning',
            started_at=started_at,
            updated_at=started_at,
            total=0,
            succeeded=0,
            failed=0,
            skipped=0,
            current_index=0,
            current_audio_path=None,
            last_error=None,
        )

        discovered = 0
        for root in resolved_roots:
            for audio_path in discover_audio_files(root):
                audio_files.append(audio_path)
                references = discover_reference_files(audio_path)
                strategy = select_subtitle_strategy(audio_path, overwrite=overwrite)
                reference_items.append(
                    {
                        'audio_path': str(audio_path),
                        'has_reference_files': bool(references),
                        'reference_files': [str(path) for path in references],
                        'strategy': strategy['action'],
                        'strategy_reason': strategy['reason'],
                        'strategy_source_path': None if strategy['source_path'] is None else str(strategy['source_path']),
                    }
                )
                discovered += 1
                if discovered == 1 or discovered % 25 == 0:
                    self._write_status(
                        status_path,
                        state='scanning',
                        started_at=started_at,
                        updated_at=_now_iso(),
                        total=0,
                        succeeded=0,
                        failed=0,
                        skipped=0,
                        current_index=discovered,
                        current_audio_path=str(audio_path),
                        last_error=None,
                    )

        dump_json(reference_scan_path(self.cache_dir), {'items': reference_items})

        succeeded = 0
        failed = 0
        skipped = 0
        total = len(audio_files)
        self._write_status(
            status_path,
            state='running',
            started_at=started_at,
            updated_at=_now_iso(),
            total=total,
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            current_index=0,
            current_audio_path=None,
            last_error=None,
        )

        for index, audio_path in enumerate(audio_files, start=1):
            _safe_print(f'[batch] {index}/{total}: {audio_path}')
            current_error = None
            try:
                status = self.pipeline(audio_path, overwrite=overwrite)
                if status == 'skipped':
                    skipped += 1
                else:
                    succeeded += 1
            except Exception as exc:
                failed += 1
                current_error = f'{type(exc).__name__}: {exc}'
                append_log_line(failed_path, f'{audio_path}	{current_error}')
                _safe_print(f'[batch][failed] {audio_path} -> {current_error}')

            self._write_status(
                status_path,
                state='running',
                started_at=started_at,
                updated_at=_now_iso(),
                total=total,
                succeeded=succeeded,
                failed=failed,
                skipped=skipped,
                current_index=index,
                current_audio_path=str(audio_path),
                last_error=current_error,
            )

        self._write_status(
            status_path,
            state='completed',
            started_at=started_at,
            updated_at=_now_iso(),
            total=total,
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            current_index=total,
            current_audio_path=None,
            last_error=None,
        )

        return BatchSummary(
            total=total,
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            reference_items=reference_items,
        )

    def _write_status(self, status_path: Path, **payload: Any) -> None:
        dump_json(status_path, payload)


def _safe_print(message: str) -> None:
    stream = sys.stdout
    encoding = getattr(stream, 'encoding', None) or 'utf-8'
    try:
        stream.write(message + '\n')
    except UnicodeEncodeError:
        safe = message.encode(encoding, errors='backslashreplace').decode(encoding, errors='ignore')
        stream.write(safe + '\n')
    stream.flush()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')
