from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import load_config
from modules.catalog import LibraryCatalog
from modules.engine import EngineService
from modules.io_utils import dump_json, translated_transcript_path
from modules.library_grouping import identify_work
from modules.quality import inspect_translated_file


class LibraryProductTests(unittest.TestCase):
    def test_rj_directory_groups_nested_tracks(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            audio = root / 'downloads' / 'RJ01234567 作品名' / 'bonus' / 'track.wav'
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b'')
            work = identify_work(audio, root)
            self.assertEqual('RJ01234567', work['rj_code'])
            self.assertEqual('RJ01234567 作品名', work['title'])

    def test_fallback_groups_by_first_library_directory(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            audio = root / '作品甲' / 'disc1' / 'track.mp3'
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b'')
            work = identify_work(audio, root)
            self.assertEqual('作品甲', work['title'])

    def test_engine_scan_builds_work_and_media_views(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            audio = root / 'RJ01234567 Test' / 'track.wav'
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b'')
            config = {'paths': {'cache_dir': str(root / 'cache'), 'log_dir': str(root / 'logs'), 'database_path': str(root / 'library.db')}, 'translate': {'enabled': False}, 'asr': {}}
            engine = EngineService(config)
            scan = engine.scan([root])
            self.assertTrue(scan['ok'])
            works = engine.list_works()['works']
            self.assertEqual(1, len(works))
            media = engine.list_media(int(works[0]['id']))['media']
            self.assertEqual(str(audio.resolve()), media[0]['path'])

    def test_job_state_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            audio = root / 'work' / 'track.wav'
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b'')
            item = {'audio_path': str(audio.resolve()), 'action': 'transcribe_audio', 'reason': 'test', 'source_path': None}
            with LibraryCatalog(root / 'library.db') as catalog:
                catalog.upsert_media([item])
                media_ids = catalog.select_media_ids(actions=['transcribe_audio'])
                job = catalog.create_job('process', media_ids, {'model': 'test'})
                paused = catalog.set_job_state(int(job['id']), 'paused')
                self.assertEqual('paused', paused['state'])

    def test_missing_media_is_marked_offline(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            audio = root / 'work' / 'track.wav'
            audio.parent.mkdir(parents=True)
            audio.write_bytes(b'')
            item = {'audio_path': str(audio.resolve()), 'action': 'transcribe_audio', 'reason': 'test', 'source_path': None}
            with LibraryCatalog(root / 'library.db') as catalog:
                catalog.upsert_media([item])
                audio.unlink()
                self.assertEqual(1, catalog.mark_missing_files_offline())
                self.assertEqual(0, catalog.summary()['total'])

    def test_job_progress_updates(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            with LibraryCatalog(Path(temp) / 'library.db') as catalog:
                job = catalog.create_job('process', [], {})
                updated = catalog.update_job_progress(int(job['id']), completed=3, failed=1, state='running')
                self.assertEqual(3, updated['completed'])
                self.assertEqual(1, updated['failed'])
    def test_quality_checker_finds_common_problems(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            path = Path(temp) / 'translated.json'
            dump_json(path, {'segments': [
                {'start': 2, 'end': 1, 'translation': ''},
                {'start': 1, 'end': 3, 'translation': 'まだ日文が残っています'},
                {'start': 3, 'end': 4, 'translation': '很长' * 30},
            ]})
            codes = {flag['code'] for flag in inspect_translated_file(path, max_chars=20)}
            self.assertTrue({'empty_translation', 'japanese_residue', 'long_line', 'invalid_timeline'} <= codes)

    def test_legacy_database_migrates_before_indexes(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            database = Path(temp) / 'legacy.db'
            connection = sqlite3.connect(database)
            connection.executescript('''
                CREATE TABLE media(id INTEGER PRIMARY KEY, path TEXT UNIQUE, size_bytes INTEGER, mtime_ns INTEGER,
                  action TEXT, source_path TEXT, reason TEXT, last_seen_at TEXT);
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
            ''')
            connection.close()
            with LibraryCatalog(database) as catalog:
                columns = {row['name'] for row in catalog.connection.execute('PRAGMA table_info(media)')}
                self.assertIn('work_id', columns)

    def test_active_model_profile_overrides_yaml_via_disk_json(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            config_path = root / 'config.yaml'
            config_path.write_text('paths:\n  cache_dir: cache\n  database_path: library.db\ntranslate:\n  model: old\nasr: {}\n', encoding='utf-8')
            config = load_config(config_path)
            engine = EngineService(config)
            engine.save_profile('new-model', 'translate', {'model': 'new', 'enabled': False}, active=True)
            reloaded = load_config(config_path)
            self.assertEqual('new', reloaded['translate']['model'])
    def test_glossary_is_exported_for_translation_module(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            config = {'paths': {'cache_dir': str(root / 'cache'), 'database_path': str(root / 'library.db'), 'log_dir': str(root / 'logs')}, 'translate': {'enabled': False}, 'asr': {}}
            engine = EngineService(config)
            result = engine.save_glossary('先輩', '前辈')
            self.assertTrue(result['ok'])
            self.assertTrue((root / 'cache' / 'glossary.json').exists())


if __name__ == '__main__':
    unittest.main()
