from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import run_single, run_write_subtitles
from modules.batch import BatchProcessor
from modules.catalog import LibraryCatalog
from modules.io_utils import cancel_request_path, discover_audio_files, dump_json
from modules.subtitle_sources import select_subtitle_strategy
from modules.vtt_writer import VTTWriter


VTT_ZH = '''WEBVTT

00:00:01.000 --> 00:00:03.000
你好。
'''


class ProductizationTests(unittest.TestCase):
    def test_discovery_keeps_each_media_variant(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            (root / 'track.wav').write_bytes(b'')
            (root / 'track.mp3').write_bytes(b'')
            self.assertEqual(2, len(discover_audio_files(root)))

    def test_full_filename_vtt_is_selected(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            audio = root / 'track.wav'
            audio.write_bytes(b'')
            source = root / 'track.wav.vtt'
            source.write_text(VTT_ZH, encoding='utf-8')
            strategy = select_subtitle_strategy(audio, overwrite=False)
            self.assertEqual('convert_existing_subtitle', strategy['action'])
            self.assertEqual(source, strategy['source_path'])

    def test_chinese_vtt_converts_without_translation_service(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            audio = root / 'track.wav'
            audio.write_bytes(b'')
            (root / 'track.wav.vtt').write_text(VTT_ZH, encoding='utf-8')
            config = {
                'paths': {'cache_dir': str(root / 'cache'), 'output_dir': str(root / 'output'), 'log_dir': str(root / 'logs')},
                'segment_cleaner': {'merge_gap_seconds': 0.5, 'long_silence_seconds': 10.0},
                'translate': {'enabled': True, 'base_url': 'http://127.0.0.1:1/v1'},
                'lrc': {'output_mode': 'same_directory', 'primary_variant': 'zh'},
            }
            self.assertEqual('processed', run_single(audio, config, overwrite=False))
            self.assertIn('你好。', (root / 'track.lrc').read_text(encoding='utf-8-sig'))
            self.assertIn('NOTE by yang 创建', (root / 'track.vtt').read_text(encoding='utf-8-sig'))

    def test_japanese_primary_lrc_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            audio = root / 'track.wav'
            audio.write_bytes(b'')
            source = root / 'track.lrc'
            source.write_text('[00:01.00]こんにちは\n', encoding='utf-8')
            translated = root / 'translated.json'
            dump_json(translated, {'segments': [{'start': 1, 'end': 2, 'text': 'こんにちは', 'translation': '你好'}]})
            config = {'paths': {'cache_dir': str(root / 'cache')}, 'lrc': {'output_mode': 'same_directory', 'primary_variant': 'zh'}}
            lrc_target, _ = run_write_subtitles(audio, config, overwrite=False, translated_path=translated, source_path=source)
            self.assertEqual((root / 'track.zh.lrc').resolve(), lrc_target)
            self.assertIn('こんにちは', source.read_text(encoding='utf-8'))
            self.assertIn('你好', lrc_target.read_text(encoding='utf-8-sig'))
    def test_vtt_writer_preserves_same_name_source(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            source = root / 'track.vtt'
            source.write_text('original', encoding='utf-8')
            translated = root / 'translated.json'
            dump_json(translated, {'segments': [{'start': 1, 'end': 2, 'translation': '中文'}]})
            target = VTTWriter().write_file(translated, source, root / 'track.zh.vtt', source_path=source)
            self.assertEqual(root / 'track.zh.vtt', target)
            self.assertEqual('original', source.read_text(encoding='utf-8'))

    def test_batch_honors_cancel_request_between_files(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            for name in ('one.wav', 'two.wav'):
                (root / name).write_bytes(b'')
            log_dir = root / 'logs'
            calls = []

            def pipeline(path: Path, overwrite: bool = False) -> str:
                calls.append(path)
                dump_json(cancel_request_path(log_dir), {'requested': True})
                return 'processed'

            config = {'paths': {'cache_dir': str(root / 'cache'), 'log_dir': str(log_dir)}}
            summary = BatchProcessor(config, pipeline).run([root])
            self.assertEqual(1, len(calls))
            self.assertEqual(1, summary.succeeded)
    def test_catalog_upsert_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(dir='tests/.tmp') as temp:
            root = Path(temp)
            audio = root / 'track.wav'
            audio.write_bytes(b'123')
            item = {'audio_path': str(audio.resolve()), 'action': 'transcribe_audio', 'source_path': None, 'reason': 'test'}
            with LibraryCatalog(root / 'library.db') as catalog:
                catalog.upsert_media([item])
                catalog.upsert_media([item])
                self.assertEqual(1, catalog.summary()['total'])


if __name__ == '__main__':
    unittest.main()
