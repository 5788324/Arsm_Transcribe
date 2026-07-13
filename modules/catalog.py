from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1


class LibraryCatalog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> 'LibraryCatalog':
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def upsert_media(self, items: Iterable[dict[str, Any]]) -> int:
        now = datetime.now().isoformat(timespec='seconds')
        rows = []
        for item in items:
            path = Path(str(item['audio_path']))
            stat = path.stat()
            rows.append((str(path), stat.st_size, stat.st_mtime_ns, item['action'], item.get('source_path'), item.get('reason'), now))
        self.connection.executemany(
            '''
            INSERT INTO media(path, size_bytes, mtime_ns, action, source_path, reason, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
              size_bytes=excluded.size_bytes, mtime_ns=excluded.mtime_ns,
              action=excluded.action, source_path=excluded.source_path,
              reason=excluded.reason, last_seen_at=excluded.last_seen_at
            ''',
            rows,
        )
        self.connection.commit()
        return len(rows)

    def summary(self) -> dict[str, int]:
        result = {'total': 0}
        for row in self.connection.execute('SELECT action, COUNT(*) AS count FROM media GROUP BY action'):
            result[str(row['action'])] = int(row['count'])
            result['total'] += int(row['count'])
        return result

    def _initialize(self) -> None:
        self.connection.executescript(
            '''
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS media(
              id INTEGER PRIMARY KEY,
              path TEXT NOT NULL UNIQUE COLLATE NOCASE,
              size_bytes INTEGER NOT NULL,
              mtime_ns INTEGER NOT NULL,
              action TEXT NOT NULL,
              source_path TEXT,
              reason TEXT NOT NULL,
              last_seen_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_media_action ON media(action);
            '''
        )
        self.connection.execute(
            'INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)',
            ('schema_version', str(SCHEMA_VERSION)),
        )
        self.connection.commit()
