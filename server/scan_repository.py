"""Couche d'accès données pour l'historique des scans.

SQLite par défaut — simple à déployer, suffisant pour un seul automate.
Pour scaler à plusieurs sites, swap pour Postgres en gardant l'interface
`ScanRepository`.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class ScanRecord:
    id: int
    label: str
    confidence: float
    points: int
    tri_status: str
    image_path: str
    created_at: int  # unix seconds


class ScanRepository:
    """Wrapper thread-safe autour de SQLite."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS scans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT NOT NULL,
        confidence REAL NOT NULL,
        points INTEGER NOT NULL,
        tri_status TEXT NOT NULL,
        image_path TEXT NOT NULL,
        created_at INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_scans_created_at ON scans(created_at DESC);
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(self._SCHEMA)

    def insert(
        self,
        *,
        label: str,
        confidence: float,
        points: int,
        tri_status: str,
        image_path: str,
    ) -> int:
        ts = int(time.time())
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO scans (label, confidence, points, tri_status, image_path, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (label, confidence, points, tri_status, image_path, ts),
            )
            return int(cur.lastrowid)

    def list_recent(self, limit: int = 50, offset: int = 0) -> list[ScanRecord]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM scans ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM scans").fetchone()
        return int(row["n"])

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ScanRecord:
        return ScanRecord(
            id=row["id"],
            label=row["label"],
            confidence=float(row["confidence"]),
            points=int(row["points"]),
            tri_status=row["tri_status"],
            image_path=row["image_path"],
            created_at=int(row["created_at"]),
        )
