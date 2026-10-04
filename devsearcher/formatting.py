"""Форматирование сообщений для Telegram (HTML parse mode)."""

from __future__ import annotations

import html
import time
from typing import Any

from .analyzer import DevExtra, DevStats, TokenCandidate, Verdict
from .settings import SETTING_LABELS, Settings

GMGN_BASE = "https://gmgn.ai"


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def gmgn_wallet_url(chain: str, wallet: str) -> str:
    return f"{GMGN_BASE}/{chain}/address/{wallet}"


def gmgn_token_url(chain: str, address: str) -> str:
    return f"{GMGN_BASE}/{chain}/token/{address}"


def fmt_usd(value: float | None) -> str:
    if value is None:
        return "—"
    v = float(value)
    if abs(v) >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"


def fmt_sol(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f} SOL"


def fmt_ago(ts: float | int | None, now: float | None = None) -> str:
    if not ts:
        return "—"
    delta = max((now or time.time()) - float(ts), 0)
    if delta < 60:
        return f"{int(delta)} с назад"
    if delta < 3600:
        return f"{int(delta // 60)} мин назад"
    if delta < 86400:
        return f"{delta / 3600:.1f} ч назад"
    return f"{delta / 86400:.1f} д назад"


def fmt_setting_value(name: str, value: Any) -> str:
    if name == "platforms":
        return ", ".join(value)
    if isinstance(value, bool):
        return "вкл" if value else "выкл"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def format_settings(s: Settings) -> str:
    lines = ["⚙️ <b>Настройки фильтра</b>", ""]
    for name, label, unit in SETTING_LABELS:
        val = fmt_setting_value(name, getattr(s, name))
        unit_s = f" {unit}" if unit else ""
        lines.append(f"• {esc(label)}: <b>{esc(val)}</b>{esc(unit_s)}  <code>{name}</code>")
    lines.append("")
    lines.append("Изменить: <code>/set &lt;имя&gt; &lt;значение&gt;</code>, например <code>/set min_fee_sol 2.5</code>")
    lines.append("Быстрые команды: /set_fee, /set_migrate, /set_minmigrated, /set_maxtokens, /platforms, /pause, /resume")
    return "\n".join(lines)


def _fee_line(fee_sol: float | None, fee_raw: float | None, fee_key: str | None, unit: str) -> str:
    if fee_sol is not None:
        return fmt_sol(fee_sol)
    if fee_raw is not None:
        return f"{fee_raw:g} {unit.upper()}" + (f" ({fee_key})" if fee_key else "")
    return "неизвестно"


def format_match(cand: TokenCandidate, stats: DevStats, settings: Settings,
                 fee_sol: float | None, extra: DevExtra | None = None) -> str:
    chain = settings.chain
    wallet = stats.wallet
    lines = [
        "🎯 <b>Найден дев-кошелёк</b>",
        "",
        f"👛 Кошелёк: <code>{esc(wallet)}</code>",
        f"🔗 <a href=\"{gmgn_wallet_url(chain, wallet)}\">Открыть кошелёк на GMGN</a>",
        "",
        "📊 <b>Статистика дева</b>",
        f"• Запусков: <b>{stats.total}</b>",
        f"• Мигрейтов: <b>{stats.migrated}</b> ({stats.ratio_percent:.1f}%)",
    ]
    ath_symbol = stats.ath_symbol or (extra.ath_symbol if extra else None)
    ath_mc = stats.ath_mc if stats.ath_mc is not None else (extra.ath_mc if extra else None)
    if ath_symbol or ath_mc is not None:
        ath_link = ""
        if stats.ath_token:
            ath_link = f" — <a href=\"{gmgn_token_url(chain, stats.ath_token)}\">GMGN</a>"
        lines.append(f"• Лучший токен: <b>{esc(ath_symbol or '?')}</b> ATH {fmt_usd(ath_mc)}{ath_link}")
    if stats.last_create_timestamp:
        lines.append(f"• Последний запуск: {fmt_ago(stats.last_create_timestamp)}")
    if extra:
        if extra.creator_token_status:
            status = extra.creator_token_status
            human = {"hold": "держит", "creator_hold": "держит", "sell": "продал",
                     "creator_sell": "продал", "creator_close": "позиция закрыта"}.get(status, status)
            lines.append(f"• Дев по токену: {esc(human)}")
        if extra.fund_from:
            lines.append(f"• Фандинг с: <code>{esc(extra.fund_from)}</code>")
        flags = []
        if extra.cto_flag:
            flags.append("CTO")
        if extra.dexscr_ad:
            flags.append("DEX ads")
        if extra.dexscr_boost:
            flags.append("DEX boost")
        if extra.twitter_renames:
            flags.append(f"Twitter rename ×{extra.twitter_renames}")
        if flags:
            lines.append("• Флаги: " + esc(", ".join(flags)))

    lines += [
        "",
        "🪙 <b>Токен-триггер</b>",
        f"• {esc(cand.symbol or '?')} — {esc(cand.name or '')}".rstrip(" —"),
        f"• CA: <code>{esc(cand.address)}</code>",
        f"• Fee: <b>{esc(_fee_line(fee_sol, cand.fee, cand.fee_key, settings.fee_unit))}</b>",
        f"• Mcap: {fmt_usd(cand.market_cap)} | Ликв.: {fmt_usd(cand.liquidity)}"
        + (f" | Холдеров: {cand.holder_count}" if cand.holder_count is not None else ""),
        f"• Платформа: {esc(cand.platform or '?')} | Мигрейт: {fmt_ago(cand.event_timestamp)}",
        f"🔗 <a href=\"{gmgn_token_url(chain, cand.address)}\">Токен на GMGN</a>",
    ]
    return "\n".join(lines)


def format_dev_check(stats: DevStats, verdict: Verdict, settings: Settings) -> str:
    chain = settings.chain
    lines = [
        ("✅ <b>Дев подходит</b>" if verdict.passed else "❌ <b>Дев не подходит</b>"),
        "",
        f"👛 <code>{esc(stats.wallet)}</code>",
        f"🔗 <a href=\"{gmgn_wallet_url(chain, stats.wallet)}\">Кошелёк на GMGN</a>",
        "",
        f"• Запусков: <b>{stats.total}</b> (на кривой: {stats.on_curve})",
        f"• Мигрейтов: <b>{stats.migrated}</b> ({stats.ratio_percent:.1f}%)",
    ]
    if stats.ath_symbol or stats.ath_mc is not None:
        lines.append(f"• Лучший токен: <b>{esc(stats.ath_symbol or '?')}</b> ATH {fmt_usd(stats.ath_mc)}")
    if not stats.counters_present:
        lines.append("• ⚠️ GMGN не вернул счётчики, считаем по списку токенов")
    if verdict.reasons:
        lines.append("")
        lines.append("Причины:")
        lines += [f"• {esc(r)}" for r in verdict.reasons]
    lines.append("")
    lines.append(
        f"Критерии: мигрейтов ≥ {settings.min_migrate_percent:g}% и ≥ {settings.min_migrated_count} шт, "
        f"запусков {settings.min_dev_tokens}–{settings.max_dev_tokens}"
    )
    return "\n".join(lines)


def format_status(info: dict[str, Any], settings: Settings, counters: dict[str, int]) -> str:
    now = time.time()
    state = "⏸ пауза" if settings.paused else ("🟢 работает" if info.get("running") else "🔴 остановлен")
    lines = [f"📡 <b>Статус сканера</b>: {state}", ""]
    lines.append(f"• Последний опрос: {fmt_ago(info.get('last_poll_at'), now)}")
    if info.get("last_poll_items") is not None:
        lines.append(f"• Токенов в последнем ответе: {info['last_poll_items']}")
    if info.get("last_error"):
        lines.append(f"• Последняя ошибка: <code>{esc(str(info['last_error'])[:300])}</code> "
                     f"({fmt_ago(info.get('last_error_at'), now)})")
    banned_until = info.get("banned_until") or 0
    if banned_until > now:
        lines.append(f"• ⚠️ Лимит GMGN, ждём ещё {int(banned_until - now)} с")
    if info.get("sol_price"):
        lines.append(f"• Курс SOL: ${info['sol_price']:.2f}")
    lines.append(f"• Запросов к GMGN: {info.get('requests', 0)} (ошибок {info.get('errors', 0)}, "
                 f"429: {info.get('rate_limits', 0)})")
    lines.append(f"• Аптайм: {fmt_ago(info.get('started_at'), now).replace(' назад', '')}")
    if info.get("alert_target") is not None:
        lines.append(f"• Находки идут в: <code>{esc(info['alert_target'])}</code>")
    lines.append("")
    lines.append("📈 <b>Счётчики</b>")
    lines.append(f"• Мигрейтов увидено: {counters.get('tokens_seen', 0)}")
    lines.append(f"• Прошли фильтр fee: {counters.get('fee_passed', 0)} "
                 f"(отсеяно по fee: {counters.get('fee_rejected', 0)}, без fee: {counters.get('fee_unknown', 0)})")
    lines.append(f"• Девов проверено: {counters.get('devs_checked', 0)}")
    lines.append(f"• Совпадений: {counters.get('matches', 0)} (повторы дева: {counters.get('dup_dev', 0)})")
    lines.append("")
    lines.append(f"Фильтр: fee ≥ {settings.min_fee_sol:g} SOL, мигрейтов ≥ {settings.min_migrate_percent:g}% "
                 f"и ≥ {settings.min_migrated_count} шт, "
                 f"запусков {settings.min_dev_tokens}–{settings.max_dev_tokens}, "
                 f"платформы: {esc(', '.join(settings.platforms))}")
    return "\n".join(lines)


def format_matches_list(rows: list[dict[str, Any]], chain: str) -> str:
    if not rows:
        return "Совпадений пока нет."
    lines = [f"🗂 <b>Последние совпадения</b> ({len(rows)})", ""]
    for r in rows:
        lines.append(
            f"• {fmt_ago(r.get('created_at'))} — <code>{esc(r['wallet'])}</code> "
            f"({r.get('migrated')}/{r.get('total')}, {float(r.get('ratio') or 0):.1f}%) "
            f"по {esc(r.get('symbol') or '?')} · <a href=\"{gmgn_wallet_url(chain, r['wallet'])}\">GMGN</a>"
        )
    return "\n".join(lines)


HELP_TEXT = """🤖 <b>Dev Wallet Searcher</b>

Бот смотрит свежие мигрейты (bonding curve → DEX) на выбранных лаунчпадах через GMGN, у токенов с fee ≥ порога берёт кошелёк дева и проверяет его историю запусков. Находки уходят в канал (/channel), управление и служебные сообщения — только сюда, админам.

<b>Команды</b>
/status — состояние сканера и счётчики
/settings — текущие настройки + кнопки
/set &lt;имя&gt; &lt;значение&gt; — изменить любую настройку
/set_fee &lt;SOL&gt; — минимальный fee токена
/set_migrate &lt;%&gt; — минимальная доля мигрейтов у дева
/set_minmigrated &lt;N&gt; — минимум мигрейтов у дева в штуках
/set_maxtokens &lt;N&gt; — максимум запусков у дева
/set_mintokens &lt;N&gt; — минимум запусков у дева
/platforms [список] — показать/задать лаунчпады (pump, bonk, stonk, ...)
/platforms_seen — под какими именами GMGN отдаёт лаунчпады сейчас
/channel [id или @name] — куда слать находки (канал); можно просто переслать боту пост из канала
/pause, /resume — остановить/продолжить сканирование
/check &lt;кошелёк&gt; — проверить дева вручную
/token &lt;CA&gt; — найти дева по токену и проверить
/raw &lt;CA&gt; — сырые поля GMGN по токену (чтобы сверить fee)
/last [N] — последние совпадения
/test — тестовое уведомление
/help — эта справка"""


__all__ = [
    "esc", "gmgn_wallet_url", "gmgn_token_url", "fmt_usd", "fmt_sol", "fmt_ago",
    "format_settings", "format_match", "format_dev_check", "format_status", "format_matches_list",
    "HELP_TEXT",
]
