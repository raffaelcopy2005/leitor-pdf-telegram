from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.config import Settings
from app.telegram_auth import sign_init_data

BOT_TOKEN = "123456:TEST-token-for-unit-tests"


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = dict(
        bot_token=BOT_TOKEN,
        bot_username="LeitorTesteBot",
        miniapp_short_name="leitor",
        public_url="https://exemplo.test",
        channel_id="-1001234567890",
        db_path=tmp_path / "test.db",
        cache_dir=tmp_path / "cache",
        cache_max_bytes=10 * 1024 * 1024,
        run_bot=False,
        log_level="WARNING",
    )
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def make_init_data(user_id: int = 42, *, auth_date: int | None = None, token: str = BOT_TOKEN, **extra: str) -> str:
    fields = {
        "query_id": "AAH-test",
        "user": f'{{"id":{user_id},"first_name":"Teste","username":"teste","language_code":"pt-br"}}',
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        **extra,
    }
    return sign_init_data(fields, token)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)
