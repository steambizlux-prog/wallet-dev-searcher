"""Разбор ответов GMGN и логика проверки дева. Чистые функции — без сети, легко тестировать."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .settings import Settings

_SOL_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")

# В каком порядке ищем поле fee в объекте токена (строка created_tokens / элемент trenches).
# coin_creator_fee — заработок дева с токена, GMGN явно указывает валюту (coin_creator_fee_token_symbol = SOL).
# total_fee — суммарный fee токена (по наблюдениям тоже в SOL). Берём первое ПОЛОЖИТЕЛЬНОЕ значение по порядку,
# если все нули — первое найденное (0).
FEE_KEYS: tuple[str, ...] = ("coin_creator_fee", "total_fee", "creator_fee", "dev_fee", "fee", "fees")
_SOL_SYMBOLS = ("SOL", "WSOL")


def is_valid_sol_address(value: str) -> bool:
    return bool(value) and bool(_SOL_ADDRESS_RE.match(value.strip()))


def _norm_platform(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def platform_matches(platform: str | None, wanted: tuple[str, ...] | list[str]) -> bool:
    """Мягкое сравнение названия лаунчпада: 'pump' ~ 'Pump.fun', 'letsbonk' ~ 'letsbonk.fun'.

    Если платформа неизвестна (пустая) — считаем, что подходит: список уже отфильтрован GMGN
    по launchpad_platform из запроса.
    """
    if not platform or not wanted:
        return True
    got = _norm_platform(platform)
    if not got:
        return True
    for w in wanted:
        want = _norm_platform(w)
        if not want or got == want or got.startswith(want) or want.startswith(got):
            return True
    return False


def to_float(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    f = to_float(value)
    return int(f) if f is not None else None


def fee_fields(obj: dict[str, Any] | None) -> dict[str, float]:
    """Все известные fee-поля объекта с числовыми значениями (для показа в сообщении)."""
    out: dict[str, float] = {}
    for key in FEE_KEYS:
        if obj and key in obj:
            val = to_float(obj.get(key))
            if val is not None:
                out[key] = val
    return out


def extract_fee(obj: dict[str, Any] | None) -> tuple[float | None, str | None]:
    """Возвращает (fee в SOL, имя_поля): первое положительное поле из FEE_KEYS, иначе первое найденное.

    coin_creator_fee учитывается, только если его валюта SOL (или не указана).
    """
    if not obj:
        return None, None
    first: tuple[float | None, str | None] = (None, None)
    for key in FEE_KEYS:
        if key not in obj:
            continue
        val = to_float(obj.get(key))
        if val is None:
            continue
        if key == "coin_creator_fee":
            sym = str(obj.get("coin_creator_fee_token_symbol") or "").upper()
            if sym and sym not in _SOL_SYMBOLS:
                continue
        if val > 0:
            return val, key
        if first[0] is None:
            first = (val, key)
    return first


@dataclass
class TokenCandidate:
    address: str
    symbol: str = ""
    name: str = ""
    creator: str | None = None
    platform: str | None = None
    fee: float | None = None
    fee_key: str | None = None
    market_cap: float | None = None
    liquidity: float | None = None
    holder_count: int | None = None
    open_timestamp: int | None = None
    created_timestamp: int | None = None
    fee_details: dict[str, float] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def event_timestamp(self) -> int | None:
        """Момент мигрейта (или создания, если open_timestamp нет)."""
        return self.open_timestamp or to_int(self.raw.get("complete_timestamp")) or self.created_timestamp


def parse_candidate(item: dict[str, Any]) -> TokenCandidate:
    address = str(item.get("address") or item.get("token_address") or "").strip()
    fee, fee_key = extract_fee(item)
    creator = item.get("creator") or item.get("creator_address") or item.get("dev_address")
    if isinstance(creator, dict):
        creator = creator.get("address")
    return TokenCandidate(
        address=address,
        symbol=str(item.get("symbol") or ""),
        name=str(item.get("name") or ""),
        creator=str(creator).strip() if creator else None,
        platform=item.get("launchpad_platform") or item.get("launchpad"),
        fee=fee,
        fee_key=fee_key,
        market_cap=to_float(item.get("usd_market_cap") or item.get("market_cap")),
        liquidity=to_float(item.get("liquidity")),
        holder_count=to_int(item.get("holder_count")),
        open_timestamp=to_int(item.get("open_timestamp")),
        created_timestamp=to_int(item.get("created_timestamp") or item.get("creation_timestamp")),
        fee_details=fee_fields(item),
        raw=item,
    )


@dataclass
class DevStats:
    wallet: str
    total: int
    migrated: int
    on_curve: int
    ratio_percent: float
    ath_symbol: str | None = None
    ath_mc: float | None = None
    ath_token: str | None = None
    tokens_sampled: int = 0
    counters_present: bool = False
    last_create_timestamp: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def parse_dev_stats(data: dict[str, Any] | None, wallet: str) -> DevStats:
    """Собирает статистику дева из ответа /v1/user/created_tokens.

    Всего запусков = inner_count + open_count; массив tokens обрезан (~100 последних),
    поэтому берём максимум из счётчиков и длины массива.
    """
    data = data or {}
    tokens = data.get("tokens") or []
    if not isinstance(tokens, list):
        tokens = []
    inner = to_int(data.get("inner_count"))
    open_ = to_int(data.get("open_count"))
    counters_present = inner is not None or open_ is not None
    inner = inner or 0
    open_ = open_ or 0
    open_in_sample = sum(1 for t in tokens if isinstance(t, dict) and bool(t.get("is_open")))
    migrated = max(open_, open_in_sample)
    total = max(inner + open_, len(tokens), migrated)
    ratio = (migrated / total * 100.0) if total else 0.0

    ath = data.get("creator_ath_info") or {}
    if not isinstance(ath, dict):
        ath = {}
    return DevStats(
        wallet=wallet,
        total=total,
        migrated=migrated,
        on_curve=max(total - migrated, 0),
        ratio_percent=ratio,
        ath_symbol=ath.get("token_symbol") or ath.get("symbol"),
        ath_mc=to_float(ath.get("ath_mc")),
        ath_token=ath.get("ath_token"),
        tokens_sampled=len(tokens),
        counters_present=counters_present,
        last_create_timestamp=to_int(data.get("last_create_timestamp")),
        raw=data,
    )


def find_token_row(data: dict[str, Any] | None, address: str) -> dict[str, Any] | None:
    for t in (data or {}).get("tokens") or []:
        if isinstance(t, dict) and str(t.get("token_address") or t.get("address") or "") == address:
            return t
    return None


@dataclass
class Verdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)


def evaluate_dev(stats: DevStats, settings: Settings) -> Verdict:
    reasons: list[str] = []
    if stats.total <= 0:
        reasons.append("нет данных о запусках дева")
        return Verdict(False, reasons)
    if stats.total < settings.min_dev_tokens:
        reasons.append(f"запусков {stats.total} < минимума {settings.min_dev_tokens}")
    if stats.total > settings.max_dev_tokens:
        reasons.append(f"запусков {stats.total} > максимума {settings.max_dev_tokens}")
    if stats.migrated < settings.min_migrated_count:
        reasons.append(f"мигрейтов {stats.migrated} шт < минимума {settings.min_migrated_count} шт")
    if stats.ratio_percent < settings.min_migrate_percent:
        reasons.append(
            f"мигрейтов {stats.ratio_percent:.1f}% ({stats.migrated}/{stats.total}) "
            f"< минимума {settings.min_migrate_percent:g}%"
        )
    return Verdict(not reasons, reasons)


def fee_threshold(settings: Settings, sol_price_usd: float | None) -> float | None:
    """Порог в тех единицах, в которых GMGN отдаёт fee. None — посчитать нельзя (нет курса)."""
    if settings.fee_unit == "sol":
        return settings.min_fee_sol
    if sol_price_usd:
        return settings.min_fee_sol * sol_price_usd
    return None


def fee_passes(fee: float | None, settings: Settings, sol_price_usd: float | None) -> bool | None:
    """True/False — решение; None — fee или порог неизвестны."""
    if fee is None:
        return None
    threshold = fee_threshold(settings, sol_price_usd)
    if threshold is None:
        return None
    return fee >= threshold


def fee_in_sol(fee: float | None, settings: Settings, sol_price_usd: float | None) -> float | None:
    if fee is None:
        return None
    if settings.fee_unit == "sol":
        return fee
    return fee / sol_price_usd if sol_price_usd else None


@dataclass
class DevExtra:
    """Дополнительные данные о деве из блока dev в /v1/token/info (только для найденных)."""
    creator_address: str | None = None
    creator_token_status: str | None = None
    creator_open_count: int | None = None
    fund_from: str | None = None
    cto_flag: bool | None = None
    dexscr_ad: bool | None = None
    dexscr_boost: bool | None = None
    twitter_renames: int | None = None
    ath_symbol: str | None = None
    ath_mc: float | None = None
    launchpad_status: int | None = None


def _flag(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return str(value).lower() in ("yes", "true")


def parse_dev_extra(info: dict[str, Any] | None) -> DevExtra:
    info = info or {}
    dev = info.get("dev") or {}
    if not isinstance(dev, dict):
        dev = {}
    ath = dev.get("ath_token_info") or {}
    if not isinstance(ath, dict):
        ath = {}
    renames = dev.get("twitter_name_change_history")
    return DevExtra(
        creator_address=dev.get("creator_address") or info.get("creator"),
        creator_token_status=dev.get("creator_token_status"),
        creator_open_count=to_int(dev.get("creator_open_count")),
        fund_from=dev.get("fund_from") or None,
        cto_flag=_flag(dev.get("cto_flag")),
        dexscr_ad=_flag(dev.get("dexscr_ad")),
        dexscr_boost=_flag(dev.get("dexscr_boost_fee")),
        twitter_renames=len(renames) if isinstance(renames, list) else None,
        ath_symbol=ath.get("symbol"),
        ath_mc=to_float(ath.get("ath_mc")),
        launchpad_status=to_int(info.get("launchpad_status")),
    )


__all__ = [
    "FEE_KEYS", "TokenCandidate", "DevStats", "Verdict", "DevExtra",
    "is_valid_sol_address", "platform_matches", "to_float", "to_int", "extract_fee", "fee_fields", "parse_candidate",
    "parse_dev_stats", "find_token_row", "evaluate_dev", "fee_threshold", "fee_passes",
    "fee_in_sol", "parse_dev_extra",
]
