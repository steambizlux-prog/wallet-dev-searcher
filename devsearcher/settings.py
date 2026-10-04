"""Рантайм-настройки фильтра. Хранятся в JSON, меняются из Telegram без перезапуска."""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

# Названия платформ в том виде, в каком их принимает GMGN (`launchpad_platform` в trenches).
KNOWN_SOL_PLATFORMS = (
    "Pump.fun", "pump_mayhem", "pump_mayhem_agent", "pump_agent",
    "letsbonk", "bonkers", "bags", "memoo", "liquid", "bankr", "zora", "surge",
    "anoncoin", "moonshot_app", "wendotdev", "heaven", "sugar", "token_mill",
    "believe", "trendsfun", "trends_fun", "jup_studio", "Moonshot", "boop",
    "ray_launchpad", "meteora_virtual_curve", "xstocks", "stonkfun",
)

PLATFORM_ALIASES = {
    "pump": "Pump.fun", "pumpfun": "Pump.fun", "pump.fun": "Pump.fun", "пампфан": "Pump.fun",
    "bonk": "letsbonk", "bonkfun": "letsbonk", "bonk.fun": "letsbonk", "letsbonk.fun": "letsbonk",
    "бонк": "letsbonk", "бонкфан": "letsbonk",
    "stonk": "stonkfun", "stonk.fun": "stonkfun", "stonkfun.xyz": "stonkfun", "stonk_fun": "stonkfun",
    "стонк": "stonkfun", "стонкфан": "stonkfun", "стонк.фан": "stonkfun",
}


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    # Минимальный fee токена-триггера (в SOL). Токены с меньшим fee не проверяются.
    min_fee_sol: float = 2.0
    # Минимальная доля мигрейтов у дева, в процентах (5 = 5 %).
    min_migrate_percent: float = 5.0
    # Минимальное число мигрейтов у дева в штуках (0 = не проверять). Работает вместе с процентом.
    min_migrated_count: int = 0
    # Максимум запущенных токенов на одном кошельке.
    max_dev_tokens: int = 1000
    # Минимум запущенных токенов (чтобы отсеять девов с 1–2 запусками).
    min_dev_tokens: int = 1
    # Лаунчпады, которые смотрим (имена как в GMGN).
    platforms: tuple[str, ...] = ("Pump.fun",)
    chain: str = "sol"
    # Пауза между опросами списка мигрейтов, сек.
    poll_interval_sec: int = 30
    # Не трогать мигрейты старше N минут (чтобы при старте не разбирать всю историю).
    max_token_age_min: int = 120
    # Не слать повторно того же дева чаще, чем раз в N часов.
    dev_cooldown_hours: int = 24
    # В чём GMGN отдаёт fee токена: "sol" или "usd" (если usd — порог пересчитывается по курсу SOL).
    fee_unit: str = "sol"
    # Что делать, если у токена нет поля fee: "skip" — пропустить, "check" — всё равно проверить дева.
    fee_unknown_policy: str = "skip"
    # Дополнительно фильтровать на стороне GMGN (меньше запросов, но зависит от их данных).
    server_filters: bool = False
    # Минимальный интервал между запросами к GMGN, сек (лимит Free-тарифа ~5 ед/с).
    request_gap_sec: float = 0.5
    paused: bool = False
    # Куда слать находки: id канала/группы (-100...) или @username публичного канала.
    # Пусто = TELEGRAM_CHAT_ID из .env. Служебные сообщения в канал не идут, только в личку админам.
    alert_chat_id: str = ""

    def validate(self) -> None:
        if self.min_fee_sol < 0:
            raise SettingsError("min_fee_sol не может быть отрицательным")
        if not 0 <= self.min_migrate_percent <= 100:
            raise SettingsError("min_migrate_percent должен быть от 0 до 100")
        if self.max_dev_tokens < 1:
            raise SettingsError("max_dev_tokens должен быть >= 1")
        if self.min_migrated_count < 0:
            raise SettingsError("min_migrated_count должен быть >= 0")
        if self.min_migrated_count > self.max_dev_tokens:
            raise SettingsError("min_migrated_count не может быть больше max_dev_tokens")
        if self.min_dev_tokens < 0:
            raise SettingsError("min_dev_tokens должен быть >= 0")
        if self.min_dev_tokens > self.max_dev_tokens:
            raise SettingsError("min_dev_tokens не может быть больше max_dev_tokens")
        if not self.platforms:
            raise SettingsError("Нужна хотя бы одна платформа")
        if not 5 <= self.poll_interval_sec <= 3600:
            raise SettingsError("poll_interval_sec должен быть от 5 до 3600")
        if not 1 <= self.max_token_age_min <= 7 * 24 * 60:
            raise SettingsError("max_token_age_min должен быть от 1 минуты до 7 дней")
        if not 0 <= self.dev_cooldown_hours <= 24 * 30:
            raise SettingsError("dev_cooldown_hours должен быть от 0 до 720")
        if self.fee_unit not in ("sol", "usd"):
            raise SettingsError("fee_unit: sol или usd")
        if self.fee_unknown_policy not in ("skip", "check"):
            raise SettingsError("fee_unknown_policy: skip или check")
        if not 0.1 <= self.request_gap_sec <= 10:
            raise SettingsError("request_gap_sec должен быть от 0.1 до 10")
        if self.chain != "sol":
            raise SettingsError("Поддерживается только chain=sol")
        if self.alert_chat_id and not is_valid_chat_ref(self.alert_chat_id):
            raise SettingsError("alert_chat_id: укажите id вида -1001234567890 или @username канала")


_FIELD_TYPES = {f.name: f.type for f in fields(Settings)}

_CHAT_USERNAME_RE = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{3,31}$")


def is_valid_chat_ref(value: str) -> bool:
    """Ссылка на чат Telegram: числовой id (можно отрицательный) или @username."""
    v = value.strip()
    if _CHAT_USERNAME_RE.match(v):
        return True
    try:
        int(v)
        return True
    except ValueError:
        return False


def chat_ref_to_target(value: str) -> int | str:
    """Для Bot API: числовые id отдаём int, @username — строкой."""
    v = value.strip()
    try:
        return int(v)
    except ValueError:
        return v


def normalize_platform(name: str) -> str:
    key = name.strip()
    low = key.lower()
    if low in PLATFORM_ALIASES:
        return PLATFORM_ALIASES[low]
    for known in KNOWN_SOL_PLATFORMS:
        if known.lower() == low:
            return known
    return key  # неизвестное имя передаём как есть (вдруг GMGN добавил новую платформу)


def coerce_value(name: str, raw: Any) -> Any:
    """Приводит сырое значение (строку из Telegram) к типу поля Settings."""
    if name not in _FIELD_TYPES:
        raise SettingsError(f"Неизвестная настройка: {name}")
    if name == "platforms":
        if isinstance(raw, str):
            parts = [p for p in raw.replace(",", " ").split() if p]
        else:
            parts = [str(p) for p in raw]
        if not parts:
            raise SettingsError("Список платформ пуст")
        return tuple(dict.fromkeys(normalize_platform(p) for p in parts))
    if name in ("server_filters", "paused"):
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("1", "on", "true", "yes", "да", "вкл"):
            return True
        if s in ("0", "off", "false", "no", "нет", "выкл"):
            return False
        raise SettingsError(f"{name}: укажите on или off")
    if name in ("min_fee_sol", "min_migrate_percent", "request_gap_sec"):
        try:
            return float(str(raw).replace(",", ".").replace("%", "").strip())
        except ValueError as exc:
            raise SettingsError(f"{name}: нужно число, получено {raw!r}") from exc
    if name in ("max_dev_tokens", "min_dev_tokens", "min_migrated_count", "poll_interval_sec",
                "max_token_age_min", "dev_cooldown_hours"):
        try:
            return int(float(str(raw).strip()))
        except ValueError as exc:
            raise SettingsError(f"{name}: нужно целое число, получено {raw!r}") from exc
    if name in ("fee_unit", "fee_unknown_policy", "chain"):
        return str(raw).strip().lower()
    if name == "alert_chat_id":
        v = str(raw).strip()
        if v.lower() in ("", "default", "off", "none", "-", "сброс"):
            return ""
        return v
    raise SettingsError(f"Настройка {name} не редактируется")


def settings_from_dict(data: dict[str, Any]) -> Settings:
    known = {f.name for f in fields(Settings)}
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in known:
            continue
        kwargs[key] = coerce_value(key, value)
    s = Settings(**kwargs)
    s.validate()
    return s


def settings_to_dict(s: Settings) -> dict[str, Any]:
    d = asdict(s)
    d["platforms"] = list(s.platforms)
    return d


class SettingsStore:
    """Потокобезопасное хранилище настроек с атомарной записью в JSON."""

    def __init__(self, path: Path | str, defaults: Settings | None = None):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._settings = defaults or Settings()
        self._settings.validate()
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    def get(self) -> Settings:
        with self._lock:
            return self._settings

    def load(self) -> Settings:
        with self._lock:
            if self._path.exists():
                try:
                    data = json.loads(self._path.read_text(encoding="utf-8"))
                    self._settings = settings_from_dict(data)
                except (OSError, ValueError, SettingsError) as exc:
                    raise SettingsError(f"Не удалось прочитать {self._path}: {exc}") from exc
            else:
                self._write_locked()
            return self._settings

    def update(self, **changes: Any) -> Settings:
        with self._lock:
            coerced = {k: coerce_value(k, v) for k, v in changes.items()}
            candidate = replace(self._settings, **coerced)
            candidate.validate()
            self._settings = candidate
            self._write_locked()
            return candidate

    def _write_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(settings_to_dict(self._settings), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


# Описание настроек для вывода в Telegram: (поле, подпись, единица)
SETTING_LABELS: tuple[tuple[str, str, str], ...] = (
    ("min_fee_sol", "Мин. fee токена", "SOL"),
    ("min_migrate_percent", "Мин. доля мигрейтов", "%"),
    ("min_migrated_count", "Мин. мигрейтов", "шт"),
    ("max_dev_tokens", "Макс. токенов у дева", "шт"),
    ("min_dev_tokens", "Мин. токенов у дева", "шт"),
    ("platforms", "Платформы", ""),
    ("poll_interval_sec", "Интервал опроса", "сек"),
    ("max_token_age_min", "Макс. возраст мигрейта", "мин"),
    ("dev_cooldown_hours", "Не повторять дева", "ч"),
    ("fee_unit", "Единица fee в GMGN", ""),
    ("fee_unknown_policy", "Если fee неизвестен", ""),
    ("server_filters", "Фильтры на стороне GMGN", ""),
    ("request_gap_sec", "Пауза между запросами", "сек"),
    ("paused", "Пауза сканера", ""),
    ("alert_chat_id", "Куда слать находки", ""),
)

__all__ = [
    "Settings", "SettingsError", "SettingsStore", "SETTING_LABELS", "KNOWN_SOL_PLATFORMS",
    "coerce_value", "normalize_platform", "settings_from_dict", "settings_to_dict",
    "is_valid_chat_ref", "chat_ref_to_target",
]
