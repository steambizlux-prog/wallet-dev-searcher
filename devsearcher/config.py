"""Статическая конфигурация из переменных окружения / .env (меняется только перезапуском)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


def _parse_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError as exc:
            raise ConfigError(f"Некорректный Telegram id: {part!r}") from exc
    return ids


@dataclass(frozen=True)
class AppConfig:
    telegram_bot_token: str
    telegram_chat_id: int | str   # id чата/канала или @username публичного канала
    admin_ids: frozenset[int]
    gmgn_api_key: str
    gmgn_api_host: str = "https://openapi.gmgn.ai"
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    log_level: str = "INFO"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "devsearcher.db"


def load_config(env_file: str | os.PathLike[str] | None = None) -> AppConfig:
    """Читает .env (если есть) и переменные окружения. Бросает ConfigError при нехватке."""
    if env_file is not None:
        load_dotenv(env_file, override=False)
    else:
        load_dotenv(override=False)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("Не задан TELEGRAM_BOT_TOKEN")

    chat_raw = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not chat_raw:
        raise ConfigError("Не задан TELEGRAM_CHAT_ID")
    chat_id: int | str
    if chat_raw.startswith("@"):
        chat_id = chat_raw
    else:
        try:
            chat_id = int(chat_raw)
        except ValueError as exc:
            raise ConfigError(f"TELEGRAM_CHAT_ID должен быть числом или @username, получено {chat_raw!r}") from exc

    admins = set(_parse_ids(os.environ.get("TELEGRAM_ADMIN_IDS", "")))
    if not admins:
        if isinstance(chat_id, int) and chat_id > 0:
            admins = {chat_id}
        else:
            raise ConfigError("TELEGRAM_CHAT_ID указывает на канал/группу — задайте TELEGRAM_ADMIN_IDS (ваш личный id)")

    api_key = os.environ.get("GMGN_API_KEY", "").strip()
    if not api_key:
        raise ConfigError("Не задан GMGN_API_KEY (создать: https://gmgn.ai/ai)")

    host = os.environ.get("GMGN_API_HOST", "https://openapi.gmgn.ai").strip().rstrip("/")
    data_dir = Path(os.environ.get("DATA_DIR", "./data")).expanduser()
    log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"

    return AppConfig(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        admin_ids=frozenset(admins),
        gmgn_api_key=api_key,
        gmgn_api_host=host,
        data_dir=data_dir,
        log_level=log_level,
    )
