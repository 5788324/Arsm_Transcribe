from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import shutil
import sys
from pathlib import Path
from typing import Any

from modules.io_utils import (
    AUDIO_EXTENSIONS,
    append_log_line,
    batch_status_path,
    cancel_request_path,
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
        failed_path.unlink(missing_ok=True)
        cancel_request_path(self.log_dir).unlink(missing_ok=True)

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
            mode='full_scan',
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
                        mode='full_scan',
                    )

        dump_json(reference_scan_path(self.cache_dir), {'items': reference_items})
        return self._run_audio_paths(
            audio_files,
            overwrite=overwrite,
            reference_items=reference_items,
            started_at=started_at,
            mode='full_scan',
        )

    def retry_failed(self, *, overwrite: bool = False) -> BatchSummary:
        """Re-run only audio paths listed in the current failure log."""
        failed_path = failed_log_path(self.log_dir)
        audio_files = _read_failed_audio_paths(failed_path)
        self._backup_and_reset_failed_log(failed_path)
        return self._run_audio_paths(
            audio_files,
            overwrite=overwrite,
            reference_items=[],
            started_at=_now_iso(),
            mode='retry_failed',
        )

    def _run_audio_paths(
        self,
        audio_files: list[Path],
        *,
        overwrite: bool,
        reference_items: list[dict[str, Any]],
        started_at: str,
        mode: str,
    ) -> BatchSummary:
        failed_path = failed_log_path(self.log_dir)
        status_path = batch_status_path(self.log_dir)
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
            mode=mode,
        )

        for index, audio_path in enumerate(audio_files, start=1):
            if cancel_request_path(self.log_dir).exists():
                self._write_status(
                    status_path, state='cancelled', started_at=started_at, updated_at=_now_iso(),
                    total=total, succeeded=succeeded, failed=failed, skipped=skipped,
                    current_index=index - 1, current_audio_path=None,
                    last_error=None, mode=mode,
                )
                return BatchSummary(total=total, succeeded=succeeded, failed=failed, skipped=skipped, reference_items=reference_items)
            _safe_print(f'[batch] {index}/{total}: {audio_path}')
            current_error = None
            try:
                if not audio_path.is_file():
                    raise FileNotFoundError(f'audio file no longer exists: {audio_path}')
                status = self.pipeline(audio_path, overwrite=overwrite)
                if status == 'skipped':
                    skipped += 1
                else:
                    succeeded += 1
            except Exception as exc:
                failed += 1
                current_error = f'{type(exc).__name__}: {exc}'
                append_log_line(failed_path, f'{audio_path}\t{current_error}')
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
                mode=mode,
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
            mode=mode,
        )
        return BatchSummary(
            total=total,
            succeeded=succeeded,
            failed=failed,
            skipped=skipped,
            reference_items=reference_items,
        )

    def _backup_and_reset_failed_log(self, failed_path: Path) -> None:
        failed_path.parent.mkdir(parents=True, exist_ok=True)
        if not failed_path.exists():
            return
        backup_path = failed_path.with_name('failed.before_retry.txt')
        shutil.copyfile(failed_path, backup_path)
        failed_path.unlink()

    def _write_status(self, status_path: Path, **payload: Any) -> None:
        dump_json(status_path, payload)


def _read_failed_audio_paths(failed_path: Path) -> list[Path]:
    if not failed_path.exists():
        return []

    paths: list[Path] = []
    seen: set[str] = set()
    for line in failed_path.read_text(encoding='utf-8').splitlines():
        raw_path, separator, _error = line.partition('\t')
        if not separator or not raw_path.strip():
            continue
        audio_path = Path(raw_path.strip()).expanduser().resolve()
        if audio_path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        key = str(audio_path).casefold()
        if key not in seen:
            seen.add(key)
            paths.append(audio_path)
    return paths


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
