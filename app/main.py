"""Ponto de entrada: FastAPI (API + frontend estático) + bot em long polling no mesmo event loop.

Execute com:  uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
ou:           python -m app
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from telegram import Bot
from telegram.error import BadRequest

from . import __version__
from .api import FileTooBig, router
from .bot import MAX_DOWNLOAD_BYTES, build_application
from .cache import PdfCache
from .config import Settings
from .db import Database

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    # O httpx registra as URLs da Bot API em INFO — e elas contêm o BOT_TOKEN.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


class BotFileSource:
    """Baixa arquivos via getFile. Nunca expõe a URL (que contém o token) ao cliente."""

    def __init__(self, bot_token: str) -> None:
        self._token = bot_token
        self._bot: Bot | None = None
        self._owns_bot = False

    def attach(self, bot: Bot) -> None:
        self._bot = bot

    async def _get_bot(self) -> Bot:
        if self._bot is None:
            bot = Bot(self._token)
            await bot.initialize()
            self._bot, self._owns_bot = bot, True
        return self._bot

    async def fetch(self, file_id: str, dest: Path) -> None:
        bot = await self._get_bot()
        try:
            tg_file = await bot.get_file(file_id)
        except BadRequest as exc:
            if "too big" in str(exc).lower():
                raise FileTooBig() from exc
            raise
        if tg_file.file_size and tg_file.file_size > MAX_DOWNLOAD_BYTES:
            raise FileTooBig()
        await tg_file.download_to_drive(dest)

    async def close(self) -> None:
        if self._owns_bot and self._bot is not None:
            await self._bot.shutdown()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    tg_app = None
    app.state.bot_running = False
    if settings.run_bot:
        tg_app = build_application(settings, app.state.db, {"cache": app.state.cache})
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(allowed_updates=["message", "channel_post"])
        if isinstance(app.state.file_source, BotFileSource):
            app.state.file_source.attach(tg_app.bot)
        app.state.bot_running = True
        logger.info("Bot @%s em execução (long polling)", tg_app.bot.username)
        if settings.effective_owner_id is None:
            logger.warning("OWNER_ID/ALLOWED_USER_IDS vazios: comandos de dono e avisos estão desativados.")
    else:
        logger.warning("RUN_BOT=false: somente a API e o frontend estão ativos.")
    try:
        yield
    finally:
        if tg_app is not None:
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()
        source = app.state.file_source
        if isinstance(source, BotFileSource):
            await source.close()
        app.state.db.close()


def create_app(settings: Settings | None = None, *, file_source: object | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)

    app = FastAPI(title="Leitor de PDF", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.db = Database(settings.db_path)
    app.state.cache = PdfCache(settings.cache_dir, settings.cache_max_bytes)
    app.state.file_source = file_source or BotFileSource(settings.bot_token)

    @app.middleware("http")
    async def no_cache_frontend(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            # O WebView do Telegram faz cache agressivo; garante que atualizações cheguem.
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    app.include_router(router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
