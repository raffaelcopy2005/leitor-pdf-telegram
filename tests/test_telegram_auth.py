from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote, urlencode

import pytest

from app.telegram_auth import (
    InitDataError,
    make_download_token,
    validate_init_data,
    verify_download_token,
)

from .conftest import BOT_TOKEN, make_init_data


def _reference_hash(fields: dict[str, str], token: str) -> str:
    """Implementação independente, linha a linha conforme a documentação oficial."""
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(key=b"WebAppData", msg=token.encode(), digestmod=hashlib.sha256).digest()
    return hmac.new(key=secret_key, msg=data_check_string.encode(), digestmod=hashlib.sha256).hexdigest()


def test_valid_init_data_is_accepted() -> None:
    data = validate_init_data(make_init_data(777, start_param="abc_DEF-123"), BOT_TOKEN)
    assert data.user is not None
    assert data.user.id == 777
    assert data.user.username == "teste"
    assert data.start_param == "abc_DEF-123"


def test_matches_reference_algorithm_with_url_encoded_values() -> None:
    now = int(time.time())
    fields = {
        "user": '{"id":1,"first_name":"Ação & \\"aspas\\"","last_name":"","username":"x"}',
        "auth_date": str(now),
        "chat_instance": "-8888",
        "signature": "ZmFrZS1zaWduYXR1cmU",  # campo novo: entra na data-check-string
    }
    raw = urlencode({**fields, "hash": _reference_hash(fields, BOT_TOKEN)}, quote_via=quote)
    data = validate_init_data(raw, BOT_TOKEN)
    assert data.user is not None and data.user.first_name == 'Ação & "aspas"'


def test_tampered_field_is_rejected() -> None:
    original = make_init_data(42)
    raw = original.replace("%22id%22%3A42", "%22id%22%3A43")
    assert raw != original
    with pytest.raises(InitDataError, match="assinatura"):
        validate_init_data(raw, BOT_TOKEN)


def test_wrong_bot_token_is_rejected() -> None:
    with pytest.raises(InitDataError, match="assinatura"):
        validate_init_data(make_init_data(42), "999:outro-token")


def test_missing_hash_is_rejected() -> None:
    with pytest.raises(InitDataError, match="hash"):
        validate_init_data("auth_date=1&user=%7B%7D", BOT_TOKEN)


@pytest.mark.parametrize("raw", [None, ""])
def test_empty_init_data_is_rejected(raw: str | None) -> None:
    with pytest.raises(InitDataError):
        validate_init_data(raw, BOT_TOKEN)


def test_duplicate_fields_are_rejected() -> None:
    raw = make_init_data(42) + "&auth_date=1"
    with pytest.raises(InitDataError):
        validate_init_data(raw, BOT_TOKEN)


def test_expired_init_data_is_rejected() -> None:
    old = int(time.time()) - 24 * 3600 - 10
    with pytest.raises(InitDataError, match="expirado"):
        validate_init_data(make_init_data(42, auth_date=old), BOT_TOKEN)


def test_init_data_just_inside_24h_is_accepted() -> None:
    now = 1_700_000_000
    raw = make_init_data(42, auth_date=now - 24 * 3600 + 5)
    assert validate_init_data(raw, BOT_TOKEN, now=now).auth_date == now - 24 * 3600 + 5


def test_future_auth_date_is_rejected() -> None:
    future = int(time.time()) + 3600
    with pytest.raises(InitDataError, match="futuro"):
        validate_init_data(make_init_data(42, auth_date=future), BOT_TOKEN)


def test_download_token_roundtrip_and_expiry() -> None:
    token = make_download_token("abc", BOT_TOKEN, ttl=60, now=1000)
    assert verify_download_token(token, "abc", BOT_TOKEN, now=1030)
    assert not verify_download_token(token, "abc", BOT_TOKEN, now=1061)
    assert not verify_download_token(token, "outro", BOT_TOKEN, now=1030)
    assert not verify_download_token(token, "abc", "1:outro", now=1030)
    assert not verify_download_token("lixo", "abc", BOT_TOKEN)
    assert not verify_download_token(None, "abc", BOT_TOKEN)
