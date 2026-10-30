from __future__ import annotations
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

class SessionStore:

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ':memory:':
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute('CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at REAL NOT NULL)')
        self._db.commit()

    def get(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute('SELECT payload FROM sessions WHERE session_id = ?', (session_id,)).fetchone()
        if not row:
            return None
        try:
            value = json.loads(row[0])
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def put(self, session_id: str, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        with self._lock:
            self._db.execute('INSERT INTO sessions (session_id, payload, updated_at) VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at', (session_id, payload, time.time()))
            self._db.commit()

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._db.execute('DELETE FROM sessions WHERE session_id = ?', (session_id,))
            self._db.commit()

    def prune(self, max_age_days: int=30) -> int:
        cutoff = time.time() - max_age_days * 86400
        with self._lock:
            cur = self._db.execute('DELETE FROM sessions WHERE updated_at < ?', (cutoff,))
            self._db.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._db.close()
