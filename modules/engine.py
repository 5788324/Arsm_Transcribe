from __future__ import annotations

import json
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.catalog import LibraryCatalog
from modules.io_utils import batch_status_path, cancel_request_path, discover_audio_files, dump_json
from modules.subtitle_sources import select_subtitle_strategy

API_VERSION = '1.0'


class EngineService:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        paths = config.get('paths', {})
        self.cache_dir = Path(paths.get('cache_dir', 'cache')).expanduser().resolve()
        self.log_dir = Path(paths.get('log_dir', 'logs')).expanduser().resolve()
        self.database_path = Path(paths.get('database_path', 'data/library.db')).expanduser().resolve()

    def scan(self, roots: list[Path]) -> dict[str, Any]:
        items = self._collect(roots)
        with LibraryCatalog(self.database_path) as catalog:
            catalog.upsert_media(items)
            catalog_summary = catalog.summary()
        payload = self._response('scan', True, items=items, summary=dict(Counter(item['action'] for item in items)), catalog=catalog_summary)
        dump_json(self.cache_dir / 'latest_scan.json', payload)
        return payload

    def plan(self, roots: list[Path]) -> dict[str, Any]:
        items = self._collect(roots)
        payload = self._response('plan', True, items=items, summary=dict(Counter(item['action'] for item in items)))
        dump_json(self.cache_dir / 'latest_plan.json', payload)
        return payload

    def status(self) -> dict[str, Any]:
        path = batch_status_path(self.log_dir)
        status = None
        if path.exists():
            try:
                status = json.loads(path.read_text(encoding='utf-8-sig'))
            except Exception as exc:
                return self._response('status', False, error=f'invalid status JSON: {exc}')
        return self._response('status', True, status=status)

    def cancel(self) -> dict[str, Any]:
        path = cancel_request_path(self.log_dir)
        dump_json(path, {'requested_at': datetime.now().isoformat(timespec='seconds')})
        return self._response('cancel', True, cancel_request_path=str(path))
    def doctor(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        runner = Path(self.config.get('asr', {}).get('faster_whisper', {}).get('runner', {}).get('executable_path', ''))
        checks.append({'name': 'asr_runner', 'ok': runner.is_file(), 'detail': str(runner)})
        checks.append({'name': 'cache_dir', 'ok': self._writable_parent(self.cache_dir), 'detail': str(self.cache_dir)})
        checks.append({'name': 'database_dir', 'ok': self._writable_parent(self.database_path.parent), 'detail': str(self.database_path.parent)})
        enabled = bool(self.config.get('translate', {}).get('enabled', True))
        if enabled:
            base_url = str(self.config.get('translate', {}).get('base_url', '')).rstrip('/')
            try:
                with urllib.request.urlopen(f'{base_url}/models', timeout=3) as response:
                    translation_ok = response.status == 200
                detail = base_url
            except Exception as exc:
                translation_ok = False
                detail = f'{base_url}: {type(exc).__name__}: {exc}'
            checks.append({'name': 'translation_service', 'ok': translation_ok, 'detail': detail})
        return self._response('doctor', all(check['ok'] for check in checks), checks=checks)

    def _collect(self, roots: list[Path]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for root in roots:
            resolved = root.expanduser().resolve()
            if not resolved.is_dir():
                raise FileNotFoundError(f'library root does not exist: {resolved}')
            for audio in discover_audio_files(resolved):
                key = str(audio).casefold()
                if key in seen:
                    continue
                seen.add(key)
                strategy = select_subtitle_strategy(audio, overwrite=False)
                items.append({
                    'audio_path': str(audio),
                    'action': strategy['action'],
                    'source_path': None if strategy['source_path'] is None else str(strategy['source_path']),
                    'reason': strategy['reason'],
                })
        return items

    def _response(self, command: str, ok: bool, **data: Any) -> dict[str, Any]:
        return {'api_version': API_VERSION, 'command': command, 'ok': ok, 'created_at': datetime.now().isoformat(timespec='seconds'), **data}

    @staticmethod
    def _writable_parent(path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False
