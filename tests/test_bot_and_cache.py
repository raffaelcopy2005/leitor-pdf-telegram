from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from telegram import Document, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden

from app.bot import BUTTON_TEXT, build_application, build_markup, explain_error, is_pdf
from app.cache import PdfCache
from app.config import ConfigError, Settings, parse_id_list
from app.db import Database

from .conftest import make_settings

PREFIX = "https://t.me/LeitorTesteBot/leitor?startapp="


def _doc(mime: str | None, name: str | None) -> Document:
    return Document(file_id="f", file_unique_id="u", mime_type=mime, file_name=name)


def test_is_pdf() -> None:
    assert is_pdf(_doc("application/pdf", "a.pdf"))
    assert is_pdf(_doc(None, "LIVRO.PDF"))
    assert not is_pdf(_doc("application/zip", "a.zip"))
    assert not is_pdf(_doc("image/png", None))
    assert not is_pdf(None)


def test_build_markup_without_existing_buttons() -> None:
    markup = build_markup(None, PREFIX + "abc", PREFIX)
    assert markup is not None
    (row,) = markup.inline_keyboard
    assert row[0].text == BUTTON_TEXT and row[0].url == PREFIX + "abc"


def test_build_markup_preserves_existing_buttons() -> None:
    existing = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Site", url="https://exemplo.com"), InlineKeyboardButton("Outro", url="https://b.com")]]
    )
    markup = build_markup(existing, PREFIX + "abc", PREFIX)
    assert markup is not None
    assert [b.text for b in markup.inline_keyboard[0]] == ["Site", "Outro"]
    assert markup.inline_keyboard[1][0].url == PREFIX + "abc"


def test_build_markup_skips_when_button_already_present() -> None:
    existing = InlineKeyboardMarkup([[InlineKeyboardButton(BUTTON_TEXT, url=PREFIX + "old")]])
    assert build_markup(existing, PREFIX + "new", PREFIX) is None


def test_explain_error_messages() -> None:
    assert "Editar mensagens" in explain_error(BadRequest("Not enough rights to edit the message"))
    assert "álbum" in explain_error(BadRequest("Message can't be edited"))
    assert "apagada" in explain_error(BadRequest("Message to edit not found"))
    assert "CHANNEL_ID" in explain_error(BadRequest("Chat not found"))
    assert "removido" in explain_error(Forbidden("bot was kicked"))


def test_build_application_registers_handlers(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    application = build_application(settings, Database(tmp_path / "x.db"))
    assert application.bot_data["settings"] is settings
    assert sum(len(h) for h in application.handlers.values()) >= 6


def test_settings_helpers(tmp_path: Path) -> None:
    s = make_settings(tmp_path, allowed_user_ids=(10, 20))
    assert s.effective_owner_id == 10
    assert s.is_owner(10) and not s.is_owner(20)
    assert s.is_user_allowed(20) and not s.is_user_allowed(30) and not s.is_user_allowed(None)
    assert s.miniapp_link("xyz") == PREFIX + "xyz"
    assert s.is_target_channel(-1001234567890, None)
    assert not s.is_target_channel(-100, None)
    open_s = make_settings(tmp_path)
    assert open_s.is_user_allowed(12345) and open_s.effective_owner_id is None
    by_name = make_settings(tmp_path, channel_id="@MeuCanal")
    assert by_name.is_target_channel(-1, "meucanal") and by_name.channel_chat_id == "@MeuCanal"


def test_parse_id_list() -> None:
    assert parse_id_list("1, 2,,3 ") == (1, 2, 3)
    assert parse_id_list("") == ()
    with pytest.raises(ConfigError):
        parse_id_list("1,abc")


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {
        "BOT_TOKEN": "1:abc", "BOT_USERNAME": "@MeuBot", "MINIAPP_SHORT_NAME": "leitor",
        "PUBLIC_URL": "https://x.example/", "CHANNEL_ID": "-100", "ALLOWED_USER_IDS": "5,6", "OWNER_ID": "",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    s = Settings.from_env(env_file=None)
    assert s.bot_username == "MeuBot" and s.public_url == "https://x.example"
    assert s.effective_owner_id == 5
    monkeypatch.delenv("BOT_TOKEN")
    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        Settings.from_env(env_file=None)


def test_cache_fetches_once_and_evicts_lru(tmp_path: Path) -> None:
    cache = PdfCache(tmp_path / "c", max_bytes=250)
    calls: list[str] = []

    def fetcher(key: str):
        async def fetch(dest: Path) -> None:
            calls.append(key)
            dest.write_bytes(b"x" * 100)
        return fetch

    async def scenario() -> None:
        a = await cache.get_or_fetch("a", fetcher("a"))
        os.utime(a, (1, 1))  # "a" é o mais antigo
        b = await cache.get_or_fetch("b", fetcher("b"))
        os.utime(b, (2, 2))
        await cache.get_or_fetch("b", fetcher("b"))  # cache hit, atualiza acesso
        await cache.get_or_fetch("c", fetcher("c"))  # 300 bytes > 250: remove "a"

    asyncio.run(scenario())
    assert calls == ["a", "b", "c"]
    assert sorted(p.stem for p in (tmp_path / "c").glob("*.pdf")) == ["b", "c"]
    assert cache.total_size() == 200


def test_cache_rejects_unsafe_keys(tmp_path: Path) -> None:
    cache = PdfCache(tmp_path / "c", max_bytes=100)
    with pytest.raises(ValueError):
        cache.path_for("../segredo")


def test_cache_cleans_partial_file_on_error(tmp_path: Path) -> None:
    cache = PdfCache(tmp_path / "c", max_bytes=1000)

    async def failing(dest: Path) -> None:
        dest.write_bytes(b"meio")
        raise RuntimeError("falhou")

    with pytest.raises(RuntimeError):
        asyncio.run(cache.get_or_fetch("z", failing))
    assert list((tmp_path / "c").iterdir()) == []
