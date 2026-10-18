"""Validação do initData de Mini Apps e tokens de download assinados.

Referência: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode

MAX_FUTURE_SKEW = 5 * 60  # tolerância de relógio para auth_date no futuro


class InitDataError(ValueError):
    """initData ausente, malformado, com assinatura inválida ou expirado."""


@dataclass(frozen=True)
class WebAppUser:
    id: int
    first_name: str = ""
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


@dataclass(frozen=True)
class InitData:
    user: WebAppUser | None
    auth_date: int
    start_param: str | None
    fields: dict[str, str]


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def _data_check_string(fields: dict[str, str]) -> str:
    return "\n".join(f"{key}={fields[key]}" for key in sorted(fields))


def compute_hash(fields: dict[str, str], bot_token: str) -> str:
    """Hash esperado para os campos (todos exceto "hash")."""
    return hmac.new(_secret_key(bot_token), _data_check_string(fields).encode(), hashlib.sha256).hexdigest()


def sign_init_data(fields: dict[str, str], bot_token: str) -> str:
    """Gera uma string initData assinada (útil para testes e desenvolvimento local)."""
    signed = dict(fields)
    signed["hash"] = compute_hash(fields, bot_token)
    return urlencode(signed)


def validate_init_data(
    init_data: str | None,
    bot_token: str,
    *,
    max_age: int = 86400,
    now: float | None = None,
) -> InitData:
    if not init_data:
        raise InitDataError("initData ausente")

    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise InitDataError("initData malformado") from exc

    fields: dict[str, str] = {}
    for key, value in pairs:
        if key in fields:
            raise InitDataError(f"campo duplicado: {key}")
        fields[key] = value

    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise InitDataError("hash ausente")

    expected = compute_hash(fields, bot_token)
    if not hmac.compare_digest(expected, received_hash.lower()):
        raise InitDataError("assinatura inválida")

    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, ValueError) as exc:
        raise InitDataError("auth_date ausente ou inválido") from exc

    current = time.time() if now is None else now
    if current - auth_date > max_age:
        raise InitDataError("initData expirado")
    if auth_date - current > MAX_FUTURE_SKEW:
        raise InitDataError("auth_date no futuro")

    user: WebAppUser | None = None
    if "user" in fields:
        try:
            raw_user = json.loads(fields["user"])
            user = WebAppUser(
                id=int(raw_user["id"]),
                first_name=str(raw_user.get("first_name", "")),
                last_name=raw_user.get("last_name"),
                username=raw_user.get("username"),
                language_code=raw_user.get("language_code"),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise InitDataError("campo user inválido") from exc

    return InitData(user=user, auth_date=auth_date, start_param=fields.get("start_param"), fields=fields)


# --- Tokens de download -----------------------------------------------------
# O botão de download do Mini App abre uma URL fora do WebView (sem o header
# X-Telegram-Init-Data). Por isso emitimos um token curto, assinado com uma
# chave derivada do BOT_TOKEN, válido só para um PDF e por poucos minutos.


def _download_key(bot_token: str) -> bytes:
    return hashlib.sha256(b"leitor-pdf-download:" + bot_token.encode()).digest()


def _download_sig(pdf_id: str, expires: int, bot_token: str) -> str:
    msg = f"{pdf_id}:{expires}".encode()
    return hmac.new(_download_key(bot_token), msg, hashlib.sha256).hexdigest()[:32]


def make_download_token(pdf_id: str, bot_token: str, *, ttl: int = 600, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + ttl)
    return f"{expires}.{_download_sig(pdf_id, expires, bot_token)}"


def verify_download_token(token: str | None, pdf_id: str, bot_token: str, *, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expires_raw, sig = token.split(".", 1)
    try:
        expires = int(expires_raw)
    except ValueError:
        return False
    if (time.time() if now is None else now) > expires:
        return False
    return hmac.compare_digest(_download_sig(pdf_id, expires, bot_token), sig)
