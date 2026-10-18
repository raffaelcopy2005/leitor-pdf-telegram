"""Cache em disco (LRU por data de acesso) dos PDFs baixados do Telegram."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_SAFE_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

Fetcher = Callable[[Path], Awaitable[None]]


class PdfCache:
    def __init__(self, directory: str | Path, max_bytes: int) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self._locks: dict[str, asyncio.Lock] = {}

    def path_for(self, key: str) -> Path:
        if not _SAFE_KEY.fullmatch(key):
            raise ValueError(f"chave de cache inválida: {key!r}")
        return self.directory / f"{key}.pdf"

    async def get_or_fetch(self, key: str, fetch: Fetcher) -> Path:
        """Devolve o caminho do arquivo em cache, baixando com `fetch(destino)` se necessário."""
        path = self.path_for(key)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if path.exists():
                self._touch(path)
                return path

            tmp = path.with_suffix(".part")
            try:
                await fetch(tmp)
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
            logger.info("Cache: baixado %s (%d bytes)", path.name, path.stat().st_size)
            self.evict(keep=path)
            return path

    @staticmethod
    def _touch(path: Path) -> None:
        try:
            os.utime(path, None)
        except OSError:
            pass

    def total_size(self) -> int:
        return sum(p.stat().st_size for p in self.directory.glob("*.pdf"))

    def evict(self, keep: Path | None = None) -> None:
        """Remove os arquivos acessados há mais tempo até caber no limite."""
        files = []
        for p in self.directory.glob("*.pdf"):
            try:
                st = p.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, p))
        total = sum(size for _, size, _ in files)
        for _, size, p in sorted(files, key=lambda f: f[0]):
            if total <= self.max_bytes:
                break
            if keep is not None and p == keep:
                continue
            try:
                p.unlink()
                total -= size
                logger.info("Cache: removido %s", p.name)
            except OSError as exc:
                logger.warning("Cache: falha ao remover %s: %s", p.name, exc)
