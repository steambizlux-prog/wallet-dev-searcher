"""Telegram-бот управления (aiogram 3). Доступ только для admin_ids из конфига."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .analyzer import extract_fee, find_token_row, is_valid_sol_address, parse_dev_extra
from .config import AppConfig
from .formatting import (
    HELP_TEXT, esc, format_dev_check, format_matches_list, format_settings, format_status, gmgn_wallet_url,
)
from .gmgn import GmgnClient, GmgnError, GmgnRateLimited
from .scanner import Scanner
from .settings import KNOWN_SOL_PLATFORMS, SettingsError, SettingsStore
from .storage import Storage

log = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4000


@dataclass
class BotContext:
    config: AppConfig
    store: SettingsStore
    storage: Storage
    scanner: Scanner
    gmgn: GmgnClient


class AdminFilter(BaseFilter):
    def __init__(self, admin_ids: frozenset[int]):
        self.admin_ids = admin_ids

    async def __call__(self, event: Message | CallbackQuery) -> bool:  # type: ignore[override]
        user = event.from_user
        return bool(user and user.id in self.admin_ids)


def split_message(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    while text:
        if len(text) <= limit:
            parts.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts


async def send_long(bot: Bot, chat_id: int, text: str, **kwargs: Any) -> None:
    for part in split_message(text):
        await bot.send_message(chat_id, part, **kwargs)


def settings_keyboard(ctx: BotContext) -> InlineKeyboardMarkup:
    s = ctx.store.get()
    rows = [
        [
            InlineKeyboardButton(text="−0.5", callback_data="adj:min_fee_sol:-0.5"),
            InlineKeyboardButton(text=f"fee ≥ {s.min_fee_sol:g} SOL", callback_data="noop"),
            InlineKeyboardButton(text="+0.5", callback_data="adj:min_fee_sol:0.5"),
        ],
        [
            InlineKeyboardButton(text="−1%", callback_data="adj:min_migrate_percent:-1"),
            InlineKeyboardButton(text=f"мигрейтов ≥ {s.min_migrate_percent:g}%", callback_data="noop"),
            InlineKeyboardButton(text="+1%", callback_data="adj:min_migrate_percent:1"),
        ],
        [
            InlineKeyboardButton(text="−100", callback_data="adj:max_dev_tokens:-100"),
            InlineKeyboardButton(text=f"токенов ≤ {s.max_dev_tokens}", callback_data="noop"),
            InlineKeyboardButton(text="+100", callback_data="adj:max_dev_tokens:100"),
        ],
        [
            InlineKeyboardButton(text="▶ Продолжить" if s.paused else "⏸ Пауза", callback_data="toggle:paused"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _gmgn_error_text(exc: Exception) -> str:
    if isinstance(exc, GmgnRateLimited):
        return f"⚠️ Лимит GMGN, повторите через ~{int(exc.wait_seconds) + 2} с"
    return f"⚠️ Ошибка GMGN: <code>{esc(str(exc)[:400])}</code>"


def build_router(ctx: BotContext) -> Router:
    router = Router(name="admin")
    admin = AdminFilter(ctx.config.admin_ids)
    router.message.filter(admin)
    router.callback_query.filter(admin)

    # ---- справка / статус ----

    @router.message(CommandStart())
    @router.message(Command("help"))
    async def cmd_help(message: Message) -> None:
        await message.answer(HELP_TEXT)

    @router.message(Command("status"))
    async def cmd_status(message: Message) -> None:
        await message.answer(format_status(ctx.scanner.status(), ctx.store.get(), ctx.storage.counters()))

    @router.message(Command("settings"))
    async def cmd_settings(message: Message) -> None:
        await message.answer(format_settings(ctx.store.get()), reply_markup=settings_keyboard(ctx))

    # ---- изменение настроек ----

    async def _apply(message: Message, **changes: Any) -> None:
        try:
            s = ctx.store.update(**changes)
        except SettingsError as exc:
            await message.answer(f"❌ {esc(exc)}")
            return
        ctx.scanner.wake()
        changed = ", ".join(f"{k} = {getattr(s, k) if k != 'platforms' else ', '.join(s.platforms)}" for k in changes)
        await message.answer(f"✅ Сохранено: <b>{esc(changed)}</b>")

    @router.message(Command("set"))
    async def cmd_set(message: Message, command: CommandObject) -> None:
        args = (command.args or "").split(maxsplit=1)
        if len(args) < 2:
            await message.answer("Формат: <code>/set &lt;имя&gt; &lt;значение&gt;</code>\nИмена смотрите в /settings")
            return
        await _apply(message, **{args[0].strip().lower(): args[1].strip()})

    async def _single(message: Message, command: CommandObject, name: str, usage: str) -> None:
        value = (command.args or "").strip()
        if not value:
            await message.answer(f"Формат: <code>{usage}</code>\nСейчас: <b>{esc(getattr(ctx.store.get(), name))}</b>")
            return
        await _apply(message, **{name: value})

    @router.message(Command("set_fee"))
    async def cmd_set_fee(message: Message, command: CommandObject) -> None:
        await _single(message, command, "min_fee_sol", "/set_fee 2")

    @router.message(Command("set_migrate"))
    async def cmd_set_migrate(message: Message, command: CommandObject) -> None:
        await _single(message, command, "min_migrate_percent", "/set_migrate 5")

    @router.message(Command("set_maxtokens"))
    async def cmd_set_maxtokens(message: Message, command: CommandObject) -> None:
        await _single(message, command, "max_dev_tokens", "/set_maxtokens 1000")

    @router.message(Command("set_mintokens"))
    async def cmd_set_mintokens(message: Message, command: CommandObject) -> None:
        await _single(message, command, "min_dev_tokens", "/set_mintokens 5")

    @router.message(Command("set_interval"))
    async def cmd_set_interval(message: Message, command: CommandObject) -> None:
        await _single(message, command, "poll_interval_sec", "/set_interval 30")

    @router.message(Command("platforms"))
    async def cmd_platforms(message: Message, command: CommandObject) -> None:
        value = (command.args or "").strip()
        if not value:
            s = ctx.store.get()
            await message.answer(
                f"Сейчас: <b>{esc(', '.join(s.platforms))}</b>\n\n"
                f"Задать: <code>/platforms Pump.fun letsbonk</code>\n"
                f"Известные для SOL: {esc(', '.join(KNOWN_SOL_PLATFORMS))}"
            )
            return
        await _apply(message, platforms=value)

    @router.message(Command("platforms_seen"))
    async def cmd_platforms_seen(message: Message) -> None:
        """Показать, под какими именами GMGN отдаёт лаунчпады в trenches (без фильтра по платформе)."""
        wait = await message.answer("⏳ Запрашиваю список лаунчпадов у GMGN…")
        settings = ctx.store.get()
        try:
            data = await ctx.gmgn.trenches(settings.chain, ("new_creation", "near_completion", "completed"),
                                           platforms=None, limit=80)
        except GmgnError as exc:
            await wait.edit_text(_gmgn_error_text(exc))
            return
        counts: dict[str, dict[str, int]] = {}
        for section, items in data.items():
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                name = str(it.get("launchpad_platform") or it.get("launchpad") or "?")
                counts.setdefault(name, {})
                counts[name][section] = counts[name].get(section, 0) + 1
        if not counts:
            await wait.edit_text("GMGN вернул пустой список.")
            return
        lines = ["🏷 <b>Лаунчпады в ответе GMGN</b> (имя → сколько токенов по категориям)", ""]
        for name, per in sorted(counts.items(), key=lambda kv: -sum(kv[1].values())):
            per_s = ", ".join(f"{k}: {v}" for k, v in sorted(per.items()))
            lines.append(f"• <code>{esc(name)}</code> — {esc(per_s)}")
        lines += ["", "Добавить платформу: <code>/platforms Pump.fun stonkfun</code> (имена как выше).",
                  "Если нужного лаунчпада нет в списке, GMGN не отдаёт его в выдаче по умолчанию: "
                  "возьмите любой токен с него и выполните <code>/raw &lt;CA&gt;</code>, поле launchpad_platform "
                  "покажет точное имя."]
        await wait.edit_text("\n".join(lines))

    @router.message(Command("pause"))
    async def cmd_pause(message: Message) -> None:
        await _apply(message, paused=True)

    @router.message(Command("resume"))
    async def cmd_resume(message: Message) -> None:
        await _apply(message, paused=False)

    # ---- кнопки ----

    @router.callback_query(F.data == "noop")
    async def cb_noop(query: CallbackQuery) -> None:
        await query.answer()

    @router.callback_query(F.data.startswith("adj:"))
    async def cb_adjust(query: CallbackQuery) -> None:
        try:
            _, name, delta_s = (query.data or "").split(":")
            delta = float(delta_s)
        except ValueError:
            await query.answer("Некорректная кнопка")
            return
        current = getattr(ctx.store.get(), name)
        new_value = current + delta
        if isinstance(current, int):
            new_value = int(round(new_value))
        new_value = max(new_value, 0)
        try:
            ctx.store.update(**{name: new_value})
        except SettingsError as exc:
            await query.answer(str(exc)[:190], show_alert=True)
            return
        ctx.scanner.wake()
        await _refresh(query)
        await query.answer("Сохранено")

    @router.callback_query(F.data == "toggle:paused")
    async def cb_toggle(query: CallbackQuery) -> None:
        s = ctx.store.update(paused=not ctx.store.get().paused)
        ctx.scanner.wake()
        await _refresh(query)
        await query.answer("Пауза" if s.paused else "Сканер продолжает")

    @router.callback_query(F.data == "refresh")
    async def cb_refresh(query: CallbackQuery) -> None:
        await _refresh(query)
        await query.answer()

    async def _refresh(query: CallbackQuery) -> None:
        if isinstance(query.message, Message):
            try:
                await query.message.edit_text(format_settings(ctx.store.get()), reply_markup=settings_keyboard(ctx))
            except Exception as exc:  # noqa: BLE001 — "message is not modified" и т.п.
                log.debug("edit_text: %s", exc)

    # ---- ручные проверки ----

    @router.message(Command("check"))
    async def cmd_check(message: Message, command: CommandObject) -> None:
        wallet = (command.args or "").strip()
        if not is_valid_sol_address(wallet):
            await message.answer("Формат: <code>/check &lt;адрес кошелька&gt;</code>")
            return
        wait = await message.answer("⏳ Проверяю дева…")
        try:
            stats, verdict = await ctx.scanner.check_wallet(wallet)
        except GmgnError as exc:
            await wait.edit_text(_gmgn_error_text(exc))
            return
        await wait.edit_text(format_dev_check(stats, verdict, ctx.store.get()))

    @router.message(Command("token"))
    async def cmd_token(message: Message, command: CommandObject) -> None:
        address = (command.args or "").strip()
        if not is_valid_sol_address(address):
            await message.answer("Формат: <code>/token &lt;адрес токена&gt;</code>")
            return
        wait = await message.answer("⏳ Ищу дева токена…")
        settings = ctx.store.get()
        try:
            info = await ctx.gmgn.token_info(settings.chain, address)
            extra = parse_dev_extra(info)
            creator = extra.creator_address
            if not creator or not is_valid_sol_address(creator):
                await wait.edit_text("GMGN не вернул адрес создателя для этого токена.")
                return
            stats, verdict = await ctx.scanner.check_wallet(creator, settings)
        except GmgnError as exc:
            await wait.edit_text(_gmgn_error_text(exc))
            return
        head = (
            f"🪙 <b>{esc(info.get('symbol') or '?')}</b> — {esc(info.get('name') or '')}\n"
            f"Статус лаунчпада: {esc(info.get('launchpad_status'))} (2 = мигрировал), "
            f"платформа: {esc(info.get('launchpad_platform') or info.get('launchpad') or '?')}\n"
            f"Дев держит: {esc(extra.creator_token_status or '?')}"
            + (f", фандинг с <code>{esc(extra.fund_from)}</code>" if extra.fund_from else "")
            + "\n\n"
        )
        await send_long(message.bot, message.chat.id, head + format_dev_check(stats, verdict, settings))  # type: ignore[arg-type]
        await wait.delete()

    @router.message(Command("raw"))
    async def cmd_raw(message: Message, command: CommandObject) -> None:
        address = (command.args or "").strip()
        if not is_valid_sol_address(address):
            await message.answer("Формат: <code>/raw &lt;адрес токена&gt;</code>")
            return
        wait = await message.answer("⏳ Собираю сырые данные…")
        settings = ctx.store.get()
        out: dict[str, Any] = {}
        cand = ctx.scanner.cached_candidate(address)
        if cand is not None:
            raw = cand.raw
            keys = [k for k in raw if "fee" in k.lower() or k in (
                "address", "symbol", "creator", "launchpad_platform", "usd_market_cap", "liquidity",
                "open_timestamp", "complete_timestamp", "creator_token_status", "creator_balance_rate",
            ) or "creator" in k.lower()]
            out["trenches_item"] = {k: raw[k] for k in keys}
            out["trenches_all_keys"] = sorted(raw.keys())
        try:
            info = await ctx.gmgn.token_info(settings.chain, address)
            out["token_info"] = {
                "symbol": info.get("symbol"), "launchpad": info.get("launchpad"),
                "launchpad_status": info.get("launchpad_status"),
                "launchpad_platform": info.get("launchpad_platform"),
                "dev": info.get("dev"), "fee_distribution": info.get("fee_distribution"),
            }
            creator = parse_dev_extra(info).creator_address
            if creator and is_valid_sol_address(creator):
                created = await ctx.gmgn.created_tokens(settings.chain, creator)
                row = find_token_row(created, address)
                out["created_tokens_summary"] = {k: created.get(k) for k in
                                                 ("inner_count", "open_count", "open_ratio", "last_create_timestamp")}
                out["created_tokens_row"] = row
                out["fee_detected"] = dict(zip(("value", "key"), extract_fee(cand.raw if cand else None)))
                out["fee_detected_in_row"] = dict(zip(("value", "key"), extract_fee(row)))
        except GmgnError as exc:
            out["error"] = str(exc)
        text = json.dumps(out, ensure_ascii=False, indent=1, default=str)
        await wait.delete()
        await send_long(message.bot, message.chat.id, f"<pre>{esc(text[:12000])}</pre>")  # type: ignore[arg-type]

    @router.message(Command("last"))
    async def cmd_last(message: Message, command: CommandObject) -> None:
        try:
            n = int((command.args or "10").strip())
        except ValueError:
            n = 10
        n = max(1, min(n, 50))
        await message.answer(format_matches_list(ctx.storage.recent_matches(n), ctx.store.get().chain))

    @router.message(Command("test"))
    async def cmd_test(message: Message) -> None:
        wallet = "11111111111111111111111111111111"
        await ctx.scanner.notify(
            "🧪 <b>Тестовое уведомление</b>\n\n"
            f"👛 Кошелёк: <code>{wallet}</code>\n"
            f"🔗 <a href=\"{gmgn_wallet_url(ctx.store.get().chain, wallet)}\">Открыть кошелёк на GMGN</a>"
        )
        if message.chat.id != ctx.config.telegram_chat_id:
            await message.answer("Отправлено в чат уведомлений.")

    return router


def build_public_router(ctx: BotContext) -> Router:
    """Для всех остальных: подсказываем id, чтобы его можно было добавить в TELEGRAM_ADMIN_IDS."""
    router = Router(name="public")

    @router.message(CommandStart())
    async def start_denied(message: Message) -> None:
        uid = message.from_user.id if message.from_user else "?"
        await message.answer(
            f"⛔ Нет доступа. Ваш id: <code>{uid}</code> — добавьте его в TELEGRAM_ADMIN_IDS в .env и перезапустите бота."
        )

    return router


__all__ = ["BotContext", "build_router", "build_public_router", "send_long", "split_message"]
