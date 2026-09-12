"""Persistência mínima: SQLite relacional (usuários/jobs) + JSON atômico (artefatos)."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Any, Iterable, Optional


class SqliteStore:
    """Wrapper serializado de SQLite para armazenamento relacional local."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True) if os.path.dirname(db_path) else None
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def init_schema(self, statements: Iterable[str]) -> None:
        with self._lock:
            cur = self._conn.cursor()
            for stmt in statements:
                cur.execute(stmt)
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.lastrowid

    def execute_rowcount(self, sql: str, params: tuple = ()) -> int:
        """Executa e retorna o número de linhas afetadas (para atualizações atômicas)."""
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class JsonStore:
    """Store simples de chave->valor com escrita atômica (tmp + rename)."""

    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True) if os.path.dirname(path) else None
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    self._data = json.load(fh)
            except (ValueError, OSError):
                self._data = {}

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._persist()

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)
            self._persist()

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._data.keys())

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def _persist(self) -> None:
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, sort_keys=True, indent=2)
        os.replace(tmp, self.path)