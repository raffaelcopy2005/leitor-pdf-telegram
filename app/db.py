"""Persistência em SQLite dos PDFs registrados."""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .ids import generate_id

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pdfs (
    id             TEXT PRIMARY KEY,
    file_id        TEXT NOT NULL,
    file_unique_id TEXT NOT NULL UNIQUE,
    file_name      TEXT NOT NULL,
    file_size      INTEGER,
    chat_id        INTEGER,
    message_id     INTEGER,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pdfs_created_at ON pdfs (created_at);
"""


@dataclass(frozen=True)
class PdfRecord:
    id: str
    file_id: str
    file_unique_id: str
    file_name: str
    file_size: int | None
    chat_id: int | None
    message_id: int | None
    created_at: str


class Database:
    """Wrapper simples e thread-safe sobre sqlite3 (o volume de escrita é baixo)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if str(self.path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> PdfRecord | None:
        return PdfRecord(**dict(row)) if row is not None else None

    def _get_by_unique_unlocked(self, file_unique_id: str) -> PdfRecord | None:
        cur = self._conn.execute("SELECT * FROM pdfs WHERE file_unique_id = ?", (file_unique_id,))
        return self._row(cur.fetchone())

    def get(self, pdf_id: str) -> PdfRecord | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM pdfs WHERE id = ?", (pdf_id,))
            return self._row(cur.fetchone())

    def get_by_unique_id(self, file_unique_id: str) -> PdfRecord | None:
        with self._lock:
            return self._get_by_unique_unlocked(file_unique_id)

    def register(
        self,
        *,
        file_id: str,
        file_unique_id: str,
        file_name: str,
        file_size: int | None,
        chat_id: int | None,
        message_id: int | None,
    ) -> tuple[PdfRecord, bool]:
        """Registra um PDF. Retorna (registro, criado). Duplicatas (file_unique_id) devolvem o existente."""
        with self._lock:
            existing = self._get_by_unique_unlocked(file_unique_id)
            if existing is not None:
                return existing, False

            created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for _ in range(10):
                record = PdfRecord(
                    id=generate_id(),
                    file_id=file_id,
                    file_unique_id=file_unique_id,
                    file_name=file_name,
                    file_size=file_size,
                    chat_id=chat_id,
                    message_id=message_id,
                    created_at=created_at,
                )
                try:
                    self._conn.execute(
                        "INSERT INTO pdfs (id, file_id, file_unique_id, file_name, file_size,"
                        " chat_id, message_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            record.id,
                            record.file_id,
                            record.file_unique_id,
                            record.file_name,
                            record.file_size,
                            record.chat_id,
                            record.message_id,
                            record.created_at,
                        ),
                    )
                    self._conn.commit()
                    logger.info("PDF registrado: id=%s nome=%r", record.id, record.file_name)
                    return record, True
                except sqlite3.IntegrityError:
                    self._conn.rollback()
                    existing = self._get_by_unique_unlocked(file_unique_id)
                    if existing is not None:
                        return existing, False
                    # Colisão de ID (extremamente improvável): tenta outro.
            raise RuntimeError("Não foi possível gerar um ID único")

    def update_location(self, pdf_id: str, chat_id: int, message_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE pdfs SET chat_id = ?, message_id = ? WHERE id = ?", (chat_id, message_id, pdf_id)
            )
            self._conn.commit()

    def delete(self, pdf_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM pdfs WHERE id = ?", (pdf_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM pdfs").fetchone()[0])

    def recent(self, limit: int = 10) -> list[PdfRecord]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM pdfs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,))
            return [PdfRecord(**dict(row)) for row in cur.fetchall()]
