"""Configuração carregada de variáveis de ambiente (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Configuração ausente ou inválida."""


def parse_id_list(raw: str | None) -> tuple[int, ...]:
    """Converte "123, 456" em (123, 456). Valores vazios são ignorados."""
    if not raw:
        return ()
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError as exc:
            raise ConfigError(f"ID de usuário inválido: {part!r} (use apenas números)") from exc
    return tuple(ids)


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "sim", "on"}


def _parse_int(name: str, raw: str | None, default: int) -> int:
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} deve ser um número inteiro (recebido {raw!r})") from exc


@dataclass(frozen=True)
class Settings:
    bot_token: str
    bot_username: str
    miniapp_short_name: str
    public_url: str
    channel_id: str
    allowed_user_ids: tuple[int, ...] = ()
    owner_id: int | None = None
    db_path: Path = Path("data/leitor.db")
    cache_dir: Path = Path("data/cache")
    cache_max_bytes: int = 300 * 1024 * 1024
    init_data_max_age: int = 24 * 60 * 60
    download_link_ttl: int = 10 * 60
    run_bot: bool = True
    log_level: str = "INFO"

    @property
    def effective_owner_id(self) -> int | None:
        """Dono do bot: OWNER_ID ou, se ausente, o primeiro de ALLOWED_USER_IDS."""
        if self.owner_id is not None:
            return self.owner_id
        return self.allowed_user_ids[0] if self.allowed_user_ids else None

    @property
    def channel_chat_id(self) -> int | str:
        """CHANNEL_ID no formato aceito pela Bot API (int ou @username)."""
        try:
            return int(self.channel_id)
        except ValueError:
            return self.channel_id

    @property
    def miniapp_link_prefix(self) -> str:
        return f"https://t.me/{self.bot_username}/{self.miniapp_short_name}?startapp="

    def miniapp_link(self, pdf_id: str) -> str:
        return self.miniapp_link_prefix + pdf_id

    def is_owner(self, user_id: int | None) -> bool:
        owner = self.effective_owner_id
        return owner is not None and user_id == owner

    def is_user_allowed(self, user_id: int | None) -> bool:
        """Sem ALLOWED_USER_IDS, qualquer usuário autenticado pode ver."""
        if not self.allowed_user_ids:
            return True
        return user_id is not None and (user_id in self.allowed_user_ids or self.is_owner(user_id))

    def is_target_channel(self, chat_id: int, username: str | None) -> bool:
        target = self.channel_chat_id
        if isinstance(target, int):
            return chat_id == target
        return username is not None and target.lstrip("@").lower() == username.lower()

    @classmethod
    def from_env(cls, env_file: str | os.PathLike[str] | None = ".env") -> "Settings":
        if env_file is not None:
            load_dotenv(env_file, override=False)
        env = os.environ

        missing = [
            name
            for name in ("BOT_TOKEN", "BOT_USERNAME", "MINIAPP_SHORT_NAME", "PUBLIC_URL", "CHANNEL_ID")
            if not env.get(name, "").strip()
        ]
        if missing:
            raise ConfigError(
                "Variáveis obrigatórias ausentes: " + ", ".join(missing) + ". Veja o arquivo .env.example."
            )

        public_url = env["PUBLIC_URL"].strip().rstrip("/")
        if not public_url.startswith("https://") and not public_url.startswith("http://localhost"):
            raise ConfigError("PUBLIC_URL deve começar com https:// (o Telegram exige HTTPS para Mini Apps).")

        owner_raw = env.get("OWNER_ID", "").strip()
        owner_id = parse_id_list(owner_raw)[0] if owner_raw else None

        return cls(
            bot_token=env["BOT_TOKEN"].strip(),
            bot_username=env["BOT_USERNAME"].strip().lstrip("@"),
            miniapp_short_name=env["MINIAPP_SHORT_NAME"].strip(),
            public_url=public_url,
            channel_id=env["CHANNEL_ID"].strip(),
            allowed_user_ids=parse_id_list(env.get("ALLOWED_USER_IDS")),
            owner_id=owner_id,
            db_path=Path(env.get("DB_PATH", "").strip() or "data/leitor.db"),
            cache_dir=Path(env.get("CACHE_DIR", "").strip() or "data/cache"),
            cache_max_bytes=_parse_int("CACHE_MAX_MB", env.get("CACHE_MAX_MB"), 300) * 1024 * 1024,
            init_data_max_age=_parse_int("INIT_DATA_MAX_AGE", env.get("INIT_DATA_MAX_AGE"), 86400),
            run_bot=_parse_bool(env.get("RUN_BOT"), True),
            log_level=(env.get("LOG_LEVEL", "").strip() or "INFO").upper(),
        )
