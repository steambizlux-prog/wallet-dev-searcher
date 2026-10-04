"""Цикл сканирования: мигрейты → фильтр fee → дев-кошелёк → проверка истории → уведомление."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from .analyzer import (
    DevExtra, DevStats, TokenCandidate, Verdict, evaluate_dev, extract_fee, fee_in_sol, fee_passes,
    find_token_row, is_valid_sol_address, parse_candidate, parse_dev_extra, parse_dev_stats, platform_matches,
)
from .formatting import format_match
from .gmgn import GmgnAuthError, GmgnClient, GmgnError, GmgnRateLimited
from .settings import Settings, SettingsStore
from .storage import Storage

log = logging.getLogger(__name__)

Notifier = Callable[[str], Awaitable[None]]

DEV_CACHE_TTL_SEC = 10 * 60
SOL_PRICE_TTL_SEC = 5 * 60
ERROR_BACKOFF_SEC = 15


class Scanner:
    def __init__(self, gmgn: GmgnClient, store: SettingsStore, storage: Storage, notify: Notifier):
        self.gmgn = gmgn
        self.store = store
        self.storage = storage
        self.notify = notify
        self.running = False
        self.started_at = time.time()
        self.last_poll_at: float | None = None
        self.last_poll_items: int | None = None
        self.last_error: str | None = None
        self.last_error_at: float | None = None
        self._sol_price: float | None = None
        self._sol_price_at = 0.0
        self._dev_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._last_candidates: dict[str, TokenCandidate] = {}
        self._warned_no_fee_at = 0.0
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()

    # ---------- управление ----------

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        """Разбудить цикл (например, после /resume)."""
        self._wake.set()

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "started_at": self.started_at,
            "last_poll_at": self.last_poll_at,
            "last_poll_items": self.last_poll_items,
            "last_error": self.last_error,
            "last_error_at": self.last_error_at,
            "banned_until": self.gmgn.banned_until,
            "sol_price": self._sol_price,
            "requests": self.gmgn.total_requests,
            "errors": self.gmgn.total_errors,
            "rate_limits": self.gmgn.total_rate_limits,
        }

    def cached_candidate(self, address: str) -> TokenCandidate | None:
        return self._last_candidates.get(address)

    # ---------- основной цикл ----------

    async def run(self) -> None:
        self.running = True
        log.info("Сканер запущен")
        try:
            while not self._stop.is_set():
                settings = self.store.get()
                self.gmgn.request_gap = settings.request_gap_sec
                delay = settings.poll_interval_sec
                if settings.paused:
                    delay = 5
                else:
                    try:
                        await self.poll_once(settings)
                    except GmgnRateLimited as exc:
                        self._set_error(f"лимит GMGN: {exc}")
                        delay = max(delay, exc.wait_seconds + 3)
                    except GmgnAuthError as exc:
                        self._set_error(f"ошибка авторизации GMGN: {exc}")
                        delay = max(delay, 120)
                    except GmgnError as exc:
                        self._set_error(str(exc))
                        delay = max(delay, ERROR_BACKOFF_SEC)
                    except Exception as exc:  # noqa: BLE001 — цикл не должен умирать
                        log.exception("Непредвиденная ошибка сканера")
                        self._set_error(f"{type(exc).__name__}: {exc}")
                        delay = max(delay, ERROR_BACKOFF_SEC)
                await self._sleep(delay)
        finally:
            self.running = False
            log.info("Сканер остановлен")

    async def _sleep(self, seconds: float) -> None:
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def _set_error(self, text: str) -> None:
        self.last_error = text
        self.last_error_at = time.time()
        log.warning("Сканер: %s", text)

    # ---------- один проход ----------

    def _server_filters(self, settings: Settings) -> dict[str, Any] | None:
        if not settings.server_filters:
            return None
        f: dict[str, Any] = {
            "max_creator_created_count": settings.max_dev_tokens,
            "min_creator_created_open_ratio": settings.min_migrate_percent / 100.0,
        }
        if settings.min_dev_tokens > 0:
            f["min_creator_created_count"] = settings.min_dev_tokens
        if settings.fee_unit == "sol" and settings.min_fee_sol > 0:
            f["min_total_fee"] = settings.min_fee_sol
        return f

    async def poll_once(self, settings: Settings) -> list[str]:
        """Один опрос списка мигрейтов. Возвращает адреса токенов, по которым ушли уведомления."""
        items = await self.gmgn.completed_tokens(
            settings.chain, settings.platforms, limit=80, filters=self._server_filters(settings)
        )
        self.last_poll_at = time.time()
        self.last_poll_items = len(items)
        self.last_error = None
        candidates = [parse_candidate(i) for i in items]
        # сначала самые свежие мигрейты
        candidates.sort(key=lambda c: c.event_timestamp or 0, reverse=True)
        alerted: list[str] = []
        now = time.time()
        for cand in candidates:
            if self._stop.is_set():
                break
            if not cand.address or self.storage.is_seen(cand.address):
                continue
            self._last_candidates[cand.address] = cand
            if len(self._last_candidates) > 500:
                oldest = next(iter(self._last_candidates))
                self._last_candidates.pop(oldest, None)
            self.storage.incr("tokens_seen")

            if not platform_matches(cand.platform, settings.platforms):
                self.storage.mark_seen(cand.address, "platform", cand.platform)
                continue
            ts = cand.event_timestamp
            if ts and now - ts > settings.max_token_age_min * 60:
                self.storage.mark_seen(cand.address, "old")
                continue
            try:
                sent = await self.process_candidate(cand, settings)
            except GmgnRateLimited:
                raise
            except GmgnError as exc:
                # не помечаем как seen — попробуем на следующем проходе
                self._set_error(f"{cand.symbol or cand.address}: {exc}")
                continue
            if sent:
                alerted.append(cand.address)
        return alerted

    async def process_candidate(self, cand: TokenCandidate, settings: Settings) -> bool:
        """Полная проверка одного мигрейта. Возвращает True, если отправлено уведомление."""
        sol_price = await self._sol_price_if_needed(settings)

        # 1) fee из списка мигрейтов (если поле есть)
        fee_ok = fee_passes(cand.fee, settings, sol_price)
        if fee_ok is False:
            self.storage.incr("fee_rejected")
            self.storage.mark_seen(cand.address, "low_fee", f"{cand.fee}")
            return False

        # 2) дев-кошелёк
        creator = cand.creator
        info: dict[str, Any] | None = None
        if not creator or not is_valid_sol_address(creator):
            info = await self.gmgn.token_info(settings.chain, cand.address)
            creator = parse_dev_extra(info).creator_address
        if not creator or not is_valid_sol_address(creator):
            self.storage.mark_seen(cand.address, "no_creator")
            log.info("%s: GMGN не отдал адрес создателя", cand.symbol or cand.address)
            return False
        cand.creator = creator

        # 3) история запусков дева
        created = await self._created_tokens_cached(settings.chain, creator)
        stats = parse_dev_stats(created, creator)

        # 4) fee, если в списке мигрейтов его не было — берём из строки этого токена у дева
        if fee_ok is None:
            row = find_token_row(created, cand.address)
            fee, key = extract_fee(row)
            if fee is not None:
                cand.fee, cand.fee_key = fee, key
                fee_ok = fee_passes(fee, settings, sol_price)
        if fee_ok is None:
            self.storage.incr("fee_unknown")
            self._warn_no_fee(cand, settings, sol_price)
            if settings.fee_unknown_policy == "skip":
                self.storage.mark_seen(cand.address, "fee_unknown")
                return False
        elif fee_ok is False:
            self.storage.incr("fee_rejected")
            self.storage.mark_seen(cand.address, "low_fee", f"{cand.fee}")
            return False
        else:
            self.storage.incr("fee_passed")

        # 5) критерии дева
        verdict = evaluate_dev(stats, settings)
        self.storage.incr("devs_checked")
        self.storage.save_dev_check(creator, stats.total, stats.migrated, stats.ratio_percent, verdict.passed,
                                    {"token": cand.address, "reasons": verdict.reasons})
        if not verdict.passed:
            self.storage.mark_seen(cand.address, "dev_rejected", "; ".join(verdict.reasons))
            log.info("%s: дев %s не прошёл: %s", cand.symbol or cand.address, creator, "; ".join(verdict.reasons))
            return False

        # 6) не спамить одним и тем же девом
        last = self.storage.last_alert_at(creator)
        if last and settings.dev_cooldown_hours > 0 and time.time() - last < settings.dev_cooldown_hours * 3600:
            self.storage.incr("dup_dev")
            self.storage.mark_seen(cand.address, "dup_dev", creator)
            log.info("%s: дев %s уже отправлялся, пропускаем", cand.symbol or cand.address, creator)
            return False

        # 7) доп. инфо о деве (best effort) и уведомление
        extra = parse_dev_extra(info) if info is not None else await self._dev_extra(settings.chain, cand.address)
        text = format_match(cand, stats, settings, fee_in_sol(cand.fee, settings, sol_price), extra)
        await self.notify(text)
        self.storage.record_match(creator, cand.address, cand.symbol, cand.fee, stats.total, stats.migrated,
                                  stats.ratio_percent, {"fee_key": cand.fee_key, "platform": cand.platform})
        self.storage.incr("matches")
        self.storage.mark_seen(cand.address, "match", creator)
        log.info("СОВПАДЕНИЕ: дев %s (%d/%d, %.1f%%) по токену %s", creator, stats.migrated, stats.total,
                 stats.ratio_percent, cand.symbol or cand.address)
        return True

    # ---------- вспомогательное ----------

    async def check_wallet(self, wallet: str, settings: Settings | None = None) -> tuple[DevStats, Verdict]:
        settings = settings or self.store.get()
        created = await self._created_tokens_cached(settings.chain, wallet)
        stats = parse_dev_stats(created, wallet)
        return stats, evaluate_dev(stats, settings)

    async def _created_tokens_cached(self, chain: str, wallet: str) -> dict[str, Any]:
        hit = self._dev_cache.get(wallet)
        if hit and time.time() - hit[0] < DEV_CACHE_TTL_SEC:
            return hit[1]
        data = await self.gmgn.created_tokens(chain, wallet)
        self._dev_cache[wallet] = (time.time(), data)
        if len(self._dev_cache) > 2000:
            oldest = min(self._dev_cache, key=lambda k: self._dev_cache[k][0])
            self._dev_cache.pop(oldest, None)
        return data

    async def _dev_extra(self, chain: str, token: str) -> DevExtra | None:
        try:
            return parse_dev_extra(await self.gmgn.token_info(chain, token))
        except GmgnRateLimited:
            raise
        except GmgnError as exc:
            log.info("token info %s недоступен: %s", token, exc)
            return None

    async def _sol_price_if_needed(self, settings: Settings) -> float | None:
        if settings.fee_unit != "usd":
            return self._sol_price
        if time.time() - self._sol_price_at > SOL_PRICE_TTL_SEC or self._sol_price is None:
            price = await self.gmgn.sol_price_usd()
            if price:
                self._sol_price = price
                self._sol_price_at = time.time()
        return self._sol_price

    def _warn_no_fee(self, cand: TokenCandidate, settings: Settings, sol_price: float | None) -> None:
        if time.time() - self._warned_no_fee_at < 3600:
            return
        self._warned_no_fee_at = time.time()
        if cand.fee is None:
            keys = ", ".join(sorted(cand.raw.keys()))[:800]
            log.warning("У токена %s нет поля fee. Поля ответа GMGN: %s", cand.address, keys)
        elif settings.fee_unit == "usd" and not sol_price:
            log.warning("fee_unit=usd, но курс SOL недоступен — fee нельзя сравнить с порогом")


__all__ = ["Scanner", "Notifier"]
