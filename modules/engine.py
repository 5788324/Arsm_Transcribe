from __future__ import annotations

import json
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.catalog import LibraryCatalog
from modules.io_utils import batch_status_path, cancel_request_path, discover_audio_files, dump_json, translated_transcript_path
from modules.library_grouping import identify_work
from modules.quality import inspect_translated_file
from modules.subtitle_sources import select_subtitle_strategy

API_VERSION = '1.1'
SAFE_ACTIONS = ['convert_existing_subtitle', 'translate_existing_subtitle', 'transcribe_audio']


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
            catalog.mark_roots_offline(roots)
            catalog.upsert_library(items)
            catalog.mark_missing_files_offline()
            catalog_summary = catalog.summary()
            self._ensure_default_profiles(catalog)
        payload = self._response('scan', True, items=items, summary=dict(Counter(item['action'] for item in items)), catalog=catalog_summary)
        dump_json(self.cache_dir / 'latest_scan.json', payload)
        return payload

    def plan(self, roots: list[Path]) -> dict[str, Any]:
        items = self._collect(roots)
        payload = self._response('plan', True, items=items, summary=dict(Counter(item['action'] for item in items)))
        dump_json(self.cache_dir / 'latest_plan.json', payload)
        return payload

    def list_works(self, *, search: str = '', action: str = '', limit: int = 500, offset: int = 0) -> dict[str, Any]:
        with LibraryCatalog(self.database_path) as catalog:
            return self._response('list-works', True, works=catalog.list_works(search=search, action=action, limit=limit, offset=offset), summary=catalog.summary())

    def list_media(self, work_id: int) -> dict[str, Any]:
        with LibraryCatalog(self.database_path) as catalog:
            return self._response('list-media', True, media=catalog.list_media(work_id))

    def enqueue(self, *, work_ids: list[int] | None = None, actions: list[str] | None = None) -> dict[str, Any]:
        selected_actions = actions or SAFE_ACTIONS
        with LibraryCatalog(self.database_path) as catalog:
            media_ids = catalog.select_media_ids(actions=selected_actions, work_ids=work_ids)
            job = catalog.create_job('process', media_ids, self._config_snapshot())
        return self._response('enqueue', True, job=job, media_count=len(media_ids))

    def jobs(self) -> dict[str, Any]:
        with LibraryCatalog(self.database_path) as catalog:
            return self._response('jobs', True, jobs=catalog.list_jobs())

    def set_job_state(self, job_id: int, state: str) -> dict[str, Any]:
        with LibraryCatalog(self.database_path) as catalog:
            job = catalog.set_job_state(job_id, state)
        if state == 'cancelled':
            self.cancel()
        return self._response(state, True, job=job)

    def update_job_progress(self, job_id: int, *, completed: int, failed: int, state: str | None = None) -> dict[str, Any]:
        with LibraryCatalog(self.database_path) as catalog:
            job = catalog.update_job_progress(job_id, completed=completed, failed=failed, state=state)
        return self._response('job-progress', True, job=job)
    def review(self, media_ids: list[int] | None = None) -> dict[str, Any]:
        checked = 0
        flag_count = 0
        max_chars = int(self.config.get('lrc', {}).get('max_chars_per_line', 45))
        with LibraryCatalog(self.database_path) as catalog:
            for media in catalog.list_media_for_review(media_ids):
                audio = Path(media['path'])
                translated_path = translated_transcript_path(self.cache_dir, audio)
                if not translated_path.exists():
                    continue
                flags = inspect_translated_file(translated_path, max_chars=max_chars)
                catalog.replace_quality_flags(int(media['id']), flags)
                checked += 1
                flag_count += len(flags)
            flags = catalog.list_quality_flags()
        return self._response('review', True, checked=checked, flag_count=flag_count, flags=flags)

    def quality_flags(self) -> dict[str, Any]:
        with LibraryCatalog(self.database_path) as catalog:
            return self._response('quality-flags', True, flags=catalog.list_quality_flags())

    def profiles(self) -> dict[str, Any]:
        with LibraryCatalog(self.database_path) as catalog:
            self._ensure_default_profiles(catalog)
            return self._response('profiles', True, profiles=catalog.list_model_profiles())

    def save_profile(self, name: str, kind: str, settings: dict[str, Any], *, active: bool = False) -> dict[str, Any]:
        if kind not in {'asr', 'translate'}:
            return self._response('save-profile', False, error='kind must be asr or translate')
        with LibraryCatalog(self.database_path) as catalog:
            profile = catalog.save_model_profile(name, kind, settings, active=active)
        if active:
            active_path = self.cache_dir / 'active_profiles.json'
            payload: dict[str, Any] = {'profiles': {}}
            if active_path.exists():
                try:
                    payload = json.loads(active_path.read_text(encoding='utf-8-sig'))
                except Exception:
                    payload = {'profiles': {}}
            payload.setdefault('profiles', {})[kind] = settings
            dump_json(active_path, payload)
            self.config[kind] = settings
        return self._response('save-profile', True, profile=profile)

    def glossary(self, work_id: int | None = None) -> dict[str, Any]:
        with LibraryCatalog(self.database_path) as catalog:
            return self._response('glossary', True, terms=catalog.list_glossary(work_id))

    def save_glossary(self, source: str, target: str, work_id: int | None = None) -> dict[str, Any]:
        if not source.strip() or not target.strip():
            return self._response('save-glossary', False, error='source and target are required')
        with LibraryCatalog(self.database_path) as catalog:
            catalog.save_glossary_term(source, target, work_id)
            terms = catalog.list_glossary(work_id)
        dump_json(self.cache_dir / 'glossary.json', {'terms': terms})
        return self._response('save-glossary', True, terms=terms)

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
        dump_json(path, {'requested_at': _now()})
        return self._response('cancel', True, cancel_request_path=str(path))

    def doctor(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        runner = Path(self.config.get('asr', {}).get('faster_whisper', {}).get('runner', {}).get('executable_path', ''))
        checks.append({'name': 'asr_runner', 'ok': runner.is_file(), 'detail': str(runner)})
        checks.append({'name': 'cache_dir', 'ok': self._writable_parent(self.cache_dir), 'detail': str(self.cache_dir)})
        checks.append({'name': 'database_dir', 'ok': self._writable_parent(self.database_path.parent), 'detail': str(self.database_path.parent)})
        if bool(self.config.get('translate', {}).get('enabled', True)):
            base_url = str(self.config.get('translate', {}).get('base_url', '')).rstrip('/')
            try:
                with urllib.request.urlopen(f'{base_url}/models', timeout=3) as response:
                    translation_ok = response.status == 200
                detail = base_url
            except Exception as exc:
                translation_ok = False
                detail = f'{base_url}: {type(exc).__name__}: {exc}'
            checks.append({'name': 'translation_service', 'ok': translation_ok, 'detail': detail})
        try:
            import PySide6
            qt_ok, qt_detail = True, getattr(PySide6, '__version__', 'installed')
        except Exception as exc:
            qt_ok, qt_detail = False, str(exc)
        checks.append({'name': 'pyside6', 'ok': qt_ok, 'detail': qt_detail})
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
                source_language = _language_for_action(strategy['action'])
                items.append({
                    'audio_path': str(audio), 'action': strategy['action'],
                    'source_path': None if strategy['source_path'] is None else str(strategy['source_path']),
                    'source_language': source_language, 'reason': strategy['reason'],
                    'work': identify_work(audio, resolved),
                })
        return items

    def _ensure_default_profiles(self, catalog: LibraryCatalog) -> None:
        if catalog.list_model_profiles():
            return
        asr = self.config.get('asr', {})
        translate = self.config.get('translate', {})
        catalog.save_model_profile(str(asr.get('backend', 'faster_whisper')), 'asr', asr, active=True)
        catalog.save_model_profile(str(translate.get('model', 'local-translation')), 'translate', translate, active=True)

    def _config_snapshot(self) -> dict[str, Any]:
        return {'asr': self.config.get('asr', {}), 'translate': self.config.get('translate', {}), 'lrc': self.config.get('lrc', {})}

    def _response(self, command: str, ok: bool, **data: Any) -> dict[str, Any]:
        return {'api_version': API_VERSION, 'command': command, 'ok': ok, 'created_at': _now(), **data}

    @staticmethod
    def _writable_parent(path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False


def _language_for_action(action: str) -> str:
    return {'convert_existing_subtitle': 'zh', 'translate_existing_subtitle': 'ja'}.get(action, 'unknown')


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')
