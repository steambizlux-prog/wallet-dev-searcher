"""Точка входа: поднимает Telegram-бота и цикл сканера в одном процессе."""

from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from . import __version__
from .bot import BotContext, build_public_router, build_router, send_long
from .config import ConfigError, load_config
from .gmgn import GmgnClient
from .scanner import Scanner
from .settings import SettingsError, SettingsStore
from .storage import Storage

log = logging.getLogger("devsearcher")

BOT_COMMANDS = [
    BotCommand(command="status", description="Статус сканера"),
    BotCommand(command="settings", description="Настройки фильтра"),
    BotCommand(command="set_fee", description="Мин. fee токена (SOL)"),
    BotCommand(command="set_migrate", description="Мин. % мигрейтов у дева"),
    BotCommand(command="set_maxtokens", description="Макс. токенов у дева"),
    BotCommand(command="platforms", description="Лаунчпады"),
    BotCommand(command="pause", description="Пауза"),
    BotCommand(command="resume", description="Продолжить"),
    BotCommand(command="check", description="Проверить дева по кошельку"),
    BotCommand(command="token", description="Проверить дева по токену"),
    BotCommand(command="last", description="Последние совпадения"),
    BotCommand(command="help", description="Справка"),
]


async def run() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    log.info("wallet-dev-searcher %s, данные в %s", __version__, config.data_dir)

    try:
        store = SettingsStore(config.settings_path)
    except SettingsError as exc:
        print(f"Ошибка настроек: {exc}", file=sys.stderr)
        return 2
    storage = Storage(config.db_path)
    gmgn = GmgnClient(config.gmgn_api_key, config.gmgn_api_host, request_gap=store.get().request_gap_sec)

    bot = Bot(
        token=config.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )

    async def notify(text: str) -> None:
        try:
            await send_long(bot, config.telegram_chat_id, text)
        except Exception as exc:  # noqa: BLE001
            log.error("Не удалось отправить уведомление в Telegram: %s", exc)

    scanner = Scanner(gmgn, store, storage, notify)
    ctx = BotContext(config=config, store=store, storage=storage, scanner=scanner, gmgn=gmgn)

    dp = Dispatcher()
    dp.include_router(build_router(ctx))
    dp.include_router(build_public_router(ctx))

    scanner_task = asyncio.create_task(scanner.run(), name="scanner")

    def _scanner_done(task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception():
            log.error("Сканер упал: %r", task.exception())

    scanner_task.add_done_callback(_scanner_done)

    try:
        try:
            await bot.set_my_commands(BOT_COMMANDS)
        except Exception as exc:  # noqa: BLE001
            log.warning("set_my_commands: %s", exc)
        s = store.get()
        await notify(
            "🚀 <b>Dev Wallet Searcher запущен</b>\n"
            f"fee ≥ {s.min_fee_sol:g} SOL · мигрейтов ≥ {s.min_migrate_percent:g}% · "
            f"запусков {s.min_dev_tokens}–{s.max_dev_tokens} · платформы: {', '.join(s.platforms)}"
            + ("\n⏸ Сканер на паузе — /resume" if s.paused else "")
        )
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        scanner.stop()
        scanner_task.cancel()
        try:
            await scanner_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        storage.prune()
        storage.close()
        await gmgn.aclose()
        await bot.session.close()
    return 0


def main() -> None:
    try:
        code = asyncio.run(run())
    except KeyboardInterrupt:
        code = 0
    sys.exit(code)


if __name__ == "__main__":
    main()
