"""Асинхронный клиент GMGN OpenAPI (https://openapi.gmgn.ai).

Авторизация read-only маршрутов: заголовок X-APIKEY + query-параметры timestamp (unix sec,
сервер принимает ±5 с) и client_id (uuid, защита от повтора). Подпись приватным ключом нужна
только для торговых маршрутов, которые здесь не используются.

Ответ всегда {"code": 0, "data": ...}; code != 0 — ошибка. HTTP 429 — лимит, в заголовке
X-RateLimit-Reset или в теле reset_at лежит unix-время снятия бана.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "wallet-dev-searcher/0.1 (+python httpx)"
WSOL_ADDRESS = "So11111111111111111111111111111111111111112"

# quote_address_type для sol — как в gmgn-cli (buildTrenchesBody)
TRENCHES_QUOTE_ADDRESS_TYPES: dict[str, list[int]] = {
    "sol": [4, 5, 3, 1, 13, 0],
    "bsc": [6, 7, 1, 16, 8, 3, 9, 10, 2, 17, 18, 0],
    "base": [11, 3, 12, 13, 0],
    "eth": [20, 11, 8, 3, 12, 1, 0],
}


class GmgnError(RuntimeError):
    """Базовая ошибка клиента."""


class GmgnApiError(GmgnError):
    def __init__(self, method: str, path: str, status: int, code: Any = None,
                 error: str | None = None, message: str | None = None):
        self.method, self.path, self.status = method, path, status
        self.code, self.error, self.message = code, error, message
        parts = [f"{method} {path} -> HTTP {status}"]
        if code is not None:
            parts.append(f"code={code}")
        if error:
            parts.append(f"error={error}")
        if message:
            parts.append(f"message={message}")
        super().__init__(" ".join(parts))


class GmgnRateLimited(GmgnApiError):
    """HTTP 429. reset_at — unix-время, когда можно повторять (может быть None)."""

    def __init__(self, method: str, path: str, reset_at: float | None, **kw: Any):
        super().__init__(method, path, 429, **kw)
        self.reset_at = reset_at

    @property
    def wait_seconds(self) -> float:
        if self.reset_at is None:
            return 60.0
        return max(self.reset_at - time.time(), 0.0)


class GmgnAuthError(GmgnApiError):
    """401/403 — неверный ключ, IPv6 или сбитое время на сервере."""


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class GmgnClient:
    """Клиент с паузой между запросами и обработкой 429.

    request_gap — минимальный интервал между запросами (сек). На Free-тарифе лимит
    примерно 5 единиц/с; trenches и created_tokens стоят по 2 единицы, token info — 1.
    """

    def __init__(
        self,
        api_key: str,
        host: str = "https://openapi.gmgn.ai",
        request_gap: float = 0.5,
        timeout: float = 30.0,
        auto_wait_max: float = 90.0,
        client: httpx.AsyncClient | None = None,
    ):
        if not api_key:
            raise GmgnError("GMGN_API_KEY пуст")
        self._api_key = api_key
        self._host = host.rstrip("/")
        self.request_gap = request_gap
        self._auto_wait_max = auto_wait_max
        self._client = client or httpx.AsyncClient(timeout=timeout, http2=False)
        self._owns_client = client is None
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0
        self.banned_until: float = 0.0
        self.total_requests = 0
        self.total_errors = 0
        self.total_rate_limits = 0

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ---------- публичные методы ----------

    async def trenches(
        self,
        chain: str,
        types: tuple[str, ...] | list[str] = ("completed",),
        platforms: tuple[str, ...] | list[str] | None = None,
        limit: int = 80,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /v1/trenches. Возвращает data: {"new_creation": [...], "pump": [...], "completed": [...]}."""
        section: dict[str, Any] = {
            "filters": ["offchain", "onchain"],
            "launchpad_platform_v2": True,
            "limit": max(1, min(int(limit), 80)),
        }
        if filters:
            section.update(filters)
        if platforms:
            section["launchpad_platform"] = list(platforms)
        qat = TRENCHES_QUOTE_ADDRESS_TYPES.get(chain)
        if qat:
            section["quote_address_type"] = qat
        body: dict[str, Any] = {"version": "v2"}
        for t in types or ("completed",):
            body[t] = dict(section)
        data = await self._request("POST", "/v1/trenches", {"chain": chain}, body)
        return data if isinstance(data, dict) else {}

    async def completed_tokens(self, chain: str, platforms=None, limit: int = 80,
                               filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Список только что мигрировавших (graduated) токенов."""
        data = await self.trenches(chain, ("completed",), platforms, limit, filters)
        items = data.get("completed")
        if isinstance(items, dict):  # на всякий случай: {"rank": [...]} / {"list": [...]}
            items = items.get("rank") or items.get("list") or []
        return [i for i in (items or []) if isinstance(i, dict)]

    async def created_tokens(self, chain: str, wallet: str, migrate_state: str | None = None,
                             order_by: str | None = None, direction: str | None = None) -> dict[str, Any]:
        """GET /v1/user/created_tokens — токены, созданные кошельком (+ inner_count/open_count/open_ratio)."""
        query: dict[str, Any] = {"chain": chain, "wallet_address": wallet}
        if migrate_state:
            query["migrate_state"] = migrate_state
        if order_by:
            query["order_by"] = order_by
        if direction:
            query["direction"] = direction
        data = await self._request("GET", "/v1/user/created_tokens", query)
        return data if isinstance(data, dict) else {}

    async def token_info(self, chain: str, address: str) -> dict[str, Any]:
        """GET /v1/token/info — инфо о токене, включая блок dev (creator_address, ATH-токен, fund_from...)."""
        data = await self._request("GET", "/v1/token/info", {"chain": chain, "address": address})
        return data if isinstance(data, dict) else {}

    async def wallet_stats(self, chain: str, wallet: str, period: str = "7d") -> dict[str, Any]:
        data = await self._request("GET", "/v1/user/wallet_stats",
                                   {"chain": chain, "wallet_address": wallet, "period": period})
        return data if isinstance(data, dict) else {}

    async def sol_price_usd(self) -> float | None:
        """Текущая цена SOL в USD (через token info WSOL)."""
        try:
            info = await self.token_info("sol", WSOL_ADDRESS)
        except GmgnError as exc:
            log.warning("Не удалось получить цену SOL: %s", exc)
            return None
        price = info.get("price")
        if isinstance(price, dict):
            return _as_float(price.get("price"))
        return _as_float(price)

    # ---------- внутреннее ----------

    async def _pace(self) -> None:
        now = time.time()
        if self.banned_until > now:
            wait = self.banned_until - now
            if wait > self._auto_wait_max:
                raise GmgnRateLimited("WAIT", "-", self.banned_until, error="RATE_LIMIT_BANNED",
                                      message=f"бан ещё {wait:.0f} с")
            log.info("GMGN: ждём снятия лимита %.0f с", wait)
            await asyncio.sleep(wait)
        gap = self.request_gap - (time.time() - self._last_request_at)
        if gap > 0:
            await asyncio.sleep(gap)

    def _build(self, path: str, query: dict[str, Any]) -> tuple[str, list[tuple[str, str]]]:
        params: list[tuple[str, str]] = []
        for k, v in query.items():
            if isinstance(v, (list, tuple)):
                params.extend((k, str(i)) for i in v)
            elif v is not None:
                params.append((k, str(v)))
        params.append(("timestamp", str(int(time.time()))))
        params.append(("client_id", str(uuid.uuid4())))
        return self._host + path, params

    async def _request(self, method: str, path: str, query: dict[str, Any],
                       body: Any = None, _retry: bool = True) -> Any:
        async with self._lock:
            await self._pace()
            url, params = self._build(path, query)
            headers = {
                "X-APIKEY": self._api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            }
            content = json.dumps(body, separators=(",", ":")) if body is not None else None
            self._last_request_at = time.time()
            self.total_requests += 1
            try:
                resp = await self._client.request(method, url, params=params, headers=headers, content=content)
            except httpx.HTTPError as exc:
                self.total_errors += 1
                raise GmgnError(f"{method} {path}: сетевая ошибка: {exc}") from exc

        try:
            payload = resp.json()
        except ValueError:
            payload = None

        if resp.status_code == 429 or (isinstance(payload, dict) and payload.get("code") == 429):
            self.total_rate_limits += 1
            reset_at = _as_float(resp.headers.get("x-ratelimit-reset"))
            if reset_at is None and isinstance(payload, dict):
                reset_at = _as_float(payload.get("reset_at"))
            if reset_at is None:
                reset_at = time.time() + 30
            self.banned_until = max(self.banned_until, reset_at + 2.0)
            err = GmgnRateLimited(method, path, reset_at,
                                  code=(payload or {}).get("code") if isinstance(payload, dict) else None,
                                  error=(payload or {}).get("error") if isinstance(payload, dict) else None,
                                  message=(payload or {}).get("message") if isinstance(payload, dict) else None)
            wait = err.wait_seconds + 2.0
            if _retry and wait <= self._auto_wait_max:
                log.warning("GMGN 429 на %s, повтор через %.0f с", path, wait)
                await asyncio.sleep(wait)
                return await self._request(method, path, query, body, _retry=False)
            raise err

        if not isinstance(payload, dict):
            self.total_errors += 1
            snippet = resp.text[:200].replace("\n", " ")
            raise GmgnApiError(method, path, resp.status_code, message=f"не-JSON ответ: {snippet}")

        if resp.status_code in (401, 403):
            self.total_errors += 1
            raise GmgnAuthError(method, path, resp.status_code, code=payload.get("code"),
                                error=payload.get("error"), message=payload.get("message"))

        if payload.get("code") not in (0, "0"):
            self.total_errors += 1
            raise GmgnApiError(method, path, resp.status_code, code=payload.get("code"),
                               error=payload.get("error"), message=payload.get("message"))
        return payload.get("data")


__all__ = ["GmgnClient", "GmgnError", "GmgnApiError", "GmgnRateLimited", "GmgnAuthError", "WSOL_ADDRESS"]
