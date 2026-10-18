"""Rotas HTTP consumidas pelo Mini App."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from telegram.error import TelegramError

from .cache import PdfCache
from .config import Settings
from .db import Database, PdfRecord
from .ids import is_valid_id
from .telegram_auth import InitData, InitDataError, make_download_token, validate_init_data, verify_download_token

logger = logging.getLogger(__name__)

router = APIRouter()


class FileSource(Protocol):
    """Baixa um arquivo do Telegram (pelo file_id) para `dest`."""

    async def fetch(self, file_id: str, dest: Path) -> None: ...


class FileTooBig(Exception):
    pass


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _db(request: Request) -> Database:
    return request.app.state.db


def _check_init_data(request: Request, init_data: str | None) -> InitData:
    settings = _settings(request)
    try:
        data = validate_init_data(init_data, settings.bot_token, max_age=settings.init_data_max_age)
    except InitDataError as exc:
        logger.info("initData recusado: %s", exc)
        raise HTTPException(status_code=401, detail="initData inválido ou expirado") from exc
    user_id = data.user.id if data.user else None
    if not settings.is_user_allowed(user_id):
        logger.info("Usuário %s sem permissão", user_id)
        raise HTTPException(status_code=403, detail="Você não tem permissão para ver este PDF")
    return data


def require_init_data(
    request: Request,
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
) -> InitData:
    return _check_init_data(request, x_telegram_init_data)


def _get_record(request: Request, pdf_id: str) -> PdfRecord:
    record = _db(request).get(pdf_id) if is_valid_id(pdf_id) else None
    if record is None:
        raise HTTPException(status_code=404, detail="PDF não encontrado")
    return record


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    return {
        "status": "ok",
        "bot": "running" if getattr(request.app.state, "bot_running", False) else "disabled",
        "pdfs": _db(request).count(),
    }


@router.get("/api/pdf/{pdf_id}/meta")
async def pdf_meta(pdf_id: str, request: Request, _: Annotated[InitData, Depends(require_init_data)]) -> dict:
    record = _get_record(request, pdf_id)
    return {
        "id": record.id,
        "file_name": record.file_name,
        "file_size": record.file_size,
        "created_at": record.created_at,
    }


@router.post("/api/pdf/{pdf_id}/download-link")
async def pdf_download_link(
    pdf_id: str, request: Request, _: Annotated[InitData, Depends(require_init_data)]
) -> dict[str, str]:
    """URL temporária (sem header) para o download fora do WebView."""
    record = _get_record(request, pdf_id)
    settings = _settings(request)
    token = make_download_token(record.id, settings.bot_token, ttl=settings.download_link_ttl)
    return {"url": f"{settings.public_url}/api/pdf/{record.id}/file?token={token}", "file_name": record.file_name}


@router.get("/api/pdf/{pdf_id}/file")
async def pdf_file(
    pdf_id: str,
    request: Request,
    token: Annotated[str | None, Query()] = None,
    x_telegram_init_data: Annotated[str | None, Header(alias="X-Telegram-Init-Data")] = None,
) -> FileResponse:
    settings = _settings(request)
    is_download = token is not None
    if is_download:
        if not verify_download_token(token, pdf_id, settings.bot_token):
            raise HTTPException(status_code=401, detail="Link de download inválido ou expirado")
    else:
        _check_init_data(request, x_telegram_init_data)
    record = _get_record(request, pdf_id)

    cache: PdfCache = request.app.state.cache
    source: FileSource = request.app.state.file_source
    try:
        path = await cache.get_or_fetch(record.file_unique_id, lambda dest: source.fetch(record.file_id, dest))
    except FileTooBig as exc:
        raise HTTPException(status_code=413, detail="Arquivo grande demais para a Bot API (> 20 MB)") from exc
    except TelegramError as exc:
        logger.error("Falha ao baixar %s do Telegram: %s", record.id, exc)
        raise HTTPException(status_code=502, detail="Falha ao baixar o PDF do Telegram") from exc

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=record.file_name,
        content_disposition_type="attachment" if is_download else "inline",
        headers={
            "Cache-Control": "private, max-age=3600",
            # Sem isso o navegador poderia reaproveitar a resposta para outra sessão/initData.
            "Vary": "X-Telegram-Init-Data",
            "X-Content-Type-Options": "nosniff",
        },
    )
