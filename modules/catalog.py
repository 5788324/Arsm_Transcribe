from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 2


class LibraryCatalog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA foreign_keys = ON')
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> 'LibraryCatalog':
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def mark_roots_offline(self, roots: Iterable[Path]) -> None:
        for root in roots:
            prefix = str(root.expanduser().resolve()).rstrip('\\/') + '\\%'
            self.connection.execute("UPDATE media SET offline=1 WHERE path LIKE ? ESCAPE '^'", (prefix,))
        self.connection.commit()
    def mark_missing_files_offline(self) -> int:
        missing = [int(row['id']) for row in self.connection.execute('SELECT id, path FROM media WHERE offline=0') if not Path(row['path']).is_file()]
        if missing:
            self.connection.executemany('UPDATE media SET offline=1 WHERE id=?', [(media_id,) for media_id in missing])
            self.connection.commit()
        return len(missing)
    def upsert_library(self, items: Iterable[dict[str, Any]]) -> int:
        now = _now()
        count = 0
        for item in items:
            work = item['work']
            self.connection.execute(
                '''INSERT INTO works(work_key, title, root_path, rj_code, cover_path, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(work_key) DO UPDATE SET title=excluded.title, root_path=excluded.root_path,
                     rj_code=excluded.rj_code, cover_path=excluded.cover_path, updated_at=excluded.updated_at''',
                (work['key'], work['title'], work['root_path'], work.get('rj_code'), work.get('cover_path'), now),
            )
            work_id = int(self.connection.execute('SELECT id FROM works WHERE work_key=?', (work['key'],)).fetchone()['id'])
            path = Path(str(item['audio_path']))
            stat = path.stat()
            self.connection.execute(
                '''INSERT INTO media(work_id, path, size_bytes, mtime_ns, extension, action, source_path, reason, status, offline, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                   ON CONFLICT(path) DO UPDATE SET work_id=excluded.work_id, size_bytes=excluded.size_bytes,
                     mtime_ns=excluded.mtime_ns, extension=excluded.extension, action=excluded.action,
                     source_path=excluded.source_path, reason=excluded.reason, offline=0, last_seen_at=excluded.last_seen_at''',
                (work_id, str(path), stat.st_size, stat.st_mtime_ns, path.suffix.lower(), item['action'],
                 item.get('source_path'), item.get('reason', ''), _status_for_action(item['action']), now),
            )
            if item.get('source_path'):
                self.connection.execute(
                    '''INSERT INTO subtitle_sources(media_id, path, kind, language, is_primary, detected_at)
                       SELECT id, ?, ?, ?, 1, ? FROM media WHERE path=?
                       ON CONFLICT(media_id, path) DO UPDATE SET kind=excluded.kind, language=excluded.language, detected_at=excluded.detected_at''',
                    (item['source_path'], Path(item['source_path']).suffix.lower(), item.get('source_language', 'unknown'), now, str(path)),
                )
            count += 1
        self.connection.commit()
        return count

    def upsert_media(self, items: Iterable[dict[str, Any]]) -> int:
        compatible = []
        for item in items:
            path = Path(str(item['audio_path']))
            compatible.append({**item, 'work': {'key': str(path.parent).casefold(), 'title': path.parent.name, 'root_path': str(path.parent)}})
        return self.upsert_library(compatible)

    def summary(self) -> dict[str, int]:
        result = {'total': 0, 'works': int(self.connection.execute('SELECT COUNT(*) FROM works').fetchone()[0])}
        for row in self.connection.execute('SELECT action, COUNT(*) AS count FROM media WHERE offline=0 GROUP BY action'):
            result[str(row['action'])] = int(row['count'])
            result['total'] += int(row['count'])
        result['quality_flags'] = int(self.connection.execute('SELECT COUNT(*) FROM quality_flags WHERE resolved=0').fetchone()[0])
        result['failed'] = int(self.connection.execute("SELECT COUNT(*) FROM media WHERE status='failed' AND offline=0").fetchone()[0])
        return result

    def list_works(self, *, search: str = '', action: str = '', limit: int = 500, offset: int = 0) -> list[dict[str, Any]]:
        conditions = ['m.offline=0']
        params: list[Any] = []
        if search:
            conditions.append('(w.title LIKE ? OR w.rj_code LIKE ? OR m.path LIKE ?)')
            token = f'%{search}%'
            params.extend([token, token, token])
        if action:
            conditions.append('m.action=?')
            params.append(action)
        params.extend([limit, offset])
        rows = self.connection.execute(
            f'''SELECT w.id, w.title, w.root_path, w.rj_code, w.cover_path,
                       COUNT(m.id) AS media_count,
                       SUM(CASE WHEN m.action='transcribe_audio' THEN 1 ELSE 0 END) AS asr_count,
                       SUM(CASE WHEN m.action='translate_existing_subtitle' THEN 1 ELSE 0 END) AS translate_count,
                       SUM(CASE WHEN m.action='convert_existing_subtitle' THEN 1 ELSE 0 END) AS convert_count,
                       SUM(CASE WHEN m.action='manual_review' THEN 1 ELSE 0 END) AS review_count,
                       SUM(CASE WHEN m.action='skip_existing_lrc' THEN 1 ELSE 0 END) AS completed_count
                FROM works w JOIN media m ON m.work_id=w.id
                WHERE {' AND '.join(conditions)} GROUP BY w.id ORDER BY w.title COLLATE NOCASE LIMIT ? OFFSET ?''',
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def list_media(self, work_id: int) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            '''SELECT m.*, (SELECT COUNT(*) FROM quality_flags q WHERE q.media_id=m.id AND q.resolved=0) AS quality_count
               FROM media m WHERE m.work_id=? AND m.offline=0 ORDER BY m.path COLLATE NOCASE''',
            (work_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def select_media_ids(self, *, actions: list[str], work_ids: list[int] | None = None) -> list[int]:
        if not actions:
            return []
        action_marks = ','.join('?' for _ in actions)
        params: list[Any] = list(actions)
        sql = f'SELECT id FROM media WHERE offline=0 AND action IN ({action_marks})'
        if work_ids:
            work_marks = ','.join('?' for _ in work_ids)
            sql += f' AND work_id IN ({work_marks})'
            params.extend(work_ids)
        return [int(row['id']) for row in self.connection.execute(sql, params)]
    def create_job(self, action: str, media_ids: list[int], config_snapshot: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        cursor = self.connection.execute(
            '''INSERT INTO jobs(state, action, media_ids_json, config_json, total, completed, failed, created_at, updated_at)
               VALUES ('queued', ?, ?, ?, ?, 0, 0, ?, ?)''',
            (action, json.dumps(media_ids), json.dumps(config_snapshot, ensure_ascii=False), len(media_ids), now, now),
        )
        self.connection.commit()
        return self.get_job(int(cursor.lastrowid))

    def set_job_state(self, job_id: int, state: str) -> dict[str, Any]:
        allowed = {'queued', 'running', 'paused', 'cancelled', 'completed', 'failed'}
        if state not in allowed:
            raise ValueError(f'unsupported job state: {state}')
        self.connection.execute('UPDATE jobs SET state=?, updated_at=? WHERE id=?', (state, _now(), job_id))
        self.connection.commit()
        return self.get_job(job_id)

    def update_job_progress(self, job_id: int, *, completed: int, failed: int, state: str | None = None) -> dict[str, Any]:
        if state is None:
            self.connection.execute('UPDATE jobs SET completed=?, failed=?, updated_at=? WHERE id=?', (completed, failed, _now(), job_id))
        else:
            self.connection.execute('UPDATE jobs SET completed=?, failed=?, state=?, updated_at=? WHERE id=?', (completed, failed, state, _now(), job_id))
        self.connection.commit()
        return self.get_job(job_id)
    def get_job(self, job_id: int) -> dict[str, Any]:
        row = self.connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise KeyError(f'job not found: {job_id}')
        return dict(row)

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute('SELECT * FROM jobs ORDER BY id DESC LIMIT ?', (limit,))]

    def replace_quality_flags(self, media_id: int, flags: list[dict[str, Any]]) -> None:
        self.connection.execute('DELETE FROM quality_flags WHERE media_id=? AND resolved=0', (media_id,))
        now = _now()
        self.connection.executemany(
            'INSERT INTO quality_flags(media_id, code, severity, message, resolved, created_at) VALUES (?, ?, ?, ?, 0, ?)',
            [(media_id, flag['code'], flag['severity'], flag['message'], now) for flag in flags],
        )
        self.connection.commit()

    def list_quality_flags(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            '''SELECT q.*, m.path AS media_path, w.title AS work_title FROM quality_flags q
               JOIN media m ON m.id=q.media_id JOIN works w ON w.id=m.work_id
               WHERE q.resolved=0 ORDER BY CASE q.severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, q.id DESC LIMIT ?''',
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_media_for_review(self, media_ids: list[int] | None = None) -> list[dict[str, Any]]:
        if media_ids:
            placeholders = ','.join('?' for _ in media_ids)
            rows = self.connection.execute(f'SELECT * FROM media WHERE id IN ({placeholders})', media_ids).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM media WHERE offline=0 AND status<>'pending' ORDER BY id DESC LIMIT 2000").fetchall()
        return [dict(row) for row in rows]

    def save_model_profile(self, name: str, kind: str, settings: dict[str, Any], active: bool = False) -> dict[str, Any]:
        if active:
            self.connection.execute('UPDATE model_profiles SET active=0 WHERE kind=?', (kind,))
        self.connection.execute(
            '''INSERT INTO model_profiles(name, kind, settings_json, active, updated_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(name, kind) DO UPDATE SET settings_json=excluded.settings_json, active=excluded.active, updated_at=excluded.updated_at''',
            (name, kind, json.dumps(settings, ensure_ascii=False), int(active), _now()),
        )
        self.connection.commit()
        row = self.connection.execute('SELECT * FROM model_profiles WHERE name=? AND kind=?', (name, kind)).fetchone()
        return dict(row)

    def list_model_profiles(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute('SELECT * FROM model_profiles ORDER BY kind, active DESC, name')]

    def save_glossary_term(self, source: str, target: str, work_id: int | None = None) -> None:
        self.connection.execute(
            '''INSERT INTO glossary(work_id, source, target, updated_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(work_id, source) DO UPDATE SET target=excluded.target, updated_at=excluded.updated_at''',
            (work_id, source.strip(), target.strip(), _now()),
        )
        self.connection.commit()

    def list_glossary(self, work_id: int | None = None) -> list[dict[str, Any]]:
        if work_id is None:
            rows = self.connection.execute('SELECT * FROM glossary ORDER BY source').fetchall()
        else:
            rows = self.connection.execute('SELECT * FROM glossary WHERE work_id IS NULL OR work_id=? ORDER BY work_id, source', (work_id,)).fetchall()
        return [dict(row) for row in rows]

    def _initialize(self) -> None:
        self.connection.executescript(
            '''
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS works(id INTEGER PRIMARY KEY, work_key TEXT NOT NULL UNIQUE COLLATE NOCASE, title TEXT NOT NULL,
              root_path TEXT NOT NULL, rj_code TEXT, cover_path TEXT, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS media(id INTEGER PRIMARY KEY, work_id INTEGER REFERENCES works(id), path TEXT NOT NULL UNIQUE COLLATE NOCASE,
              size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, extension TEXT, action TEXT NOT NULL, source_path TEXT, reason TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending', offline INTEGER NOT NULL DEFAULT 0, last_seen_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_media_action ON media(action);
            CREATE TABLE IF NOT EXISTS subtitle_sources(id INTEGER PRIMARY KEY, media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
              path TEXT NOT NULL, kind TEXT NOT NULL, language TEXT NOT NULL, is_primary INTEGER NOT NULL DEFAULT 0, detected_at TEXT NOT NULL,
              UNIQUE(media_id, path));
            CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, state TEXT NOT NULL, action TEXT NOT NULL, media_ids_json TEXT NOT NULL,
              config_json TEXT NOT NULL, total INTEGER NOT NULL, completed INTEGER NOT NULL, failed INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
              media_id INTEGER REFERENCES media(id), stage TEXT NOT NULL, state TEXT NOT NULL, error TEXT, started_at TEXT, finished_at TEXT);
            CREATE TABLE IF NOT EXISTS quality_flags(id INTEGER PRIMARY KEY, media_id INTEGER NOT NULL REFERENCES media(id) ON DELETE CASCADE,
              code TEXT NOT NULL, severity TEXT NOT NULL, message TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS model_profiles(id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, settings_json TEXT NOT NULL,
              active INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, UNIQUE(name, kind));
            CREATE TABLE IF NOT EXISTS glossary(id INTEGER PRIMARY KEY, work_id INTEGER REFERENCES works(id) ON DELETE CASCADE,
              source TEXT NOT NULL, target TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(work_id, source));
            '''
        )
        self._migrate_legacy_media()
        self.connection.execute('CREATE INDEX IF NOT EXISTS idx_media_work ON media(work_id)')
        self.connection.execute('INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)', ('schema_version', str(SCHEMA_VERSION)))
        self.connection.commit()

    def _migrate_legacy_media(self) -> None:
        columns = {row['name'] for row in self.connection.execute('PRAGMA table_info(media)')}
        additions = {
            'work_id': 'INTEGER REFERENCES works(id)', 'extension': 'TEXT',
            'status': "TEXT NOT NULL DEFAULT 'pending'", 'offline': 'INTEGER NOT NULL DEFAULT 0',
        }
        for name, definition in additions.items():
            if name not in columns:
                self.connection.execute(f'ALTER TABLE media ADD COLUMN {name} {definition}')


def _status_for_action(action: str) -> str:
    return 'completed' if action == 'skip_existing_lrc' else ('review' if action == 'manual_review' else 'pending')


def _now() -> str:
    return datetime.now().isoformat(timespec='seconds')
