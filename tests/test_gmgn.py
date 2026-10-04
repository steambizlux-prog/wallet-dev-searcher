import json
import time

import httpx
import pytest

from devsearcher.gmgn import GmgnApiError, GmgnAuthError, GmgnClient, GmgnRateLimited


def make_client(handler, **kw):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return GmgnClient("key", "https://api.test", request_gap=0.0, client=http, **kw)


@pytest.mark.asyncio
async def test_trenches_request_shape_and_auth():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"completed": [{"address": "x", "total_fee": "3"}]}})

    client = make_client(handler)
    items = await client.completed_tokens("sol", ("Pump.fun",), limit=50, filters={"min_total_fee": 2})
    assert items == [{"address": "x", "total_fee": "3"}]
    assert captured["headers"]["x-apikey"] == "key"
    assert "chain=sol" in captured["url"] and "timestamp=" in captured["url"] and "client_id=" in captured["url"]
    body = captured["body"]
    assert body["version"] == "v2" and set(body) == {"version", "completed"}
    sec = body["completed"]
    assert sec["launchpad_platform"] == ["Pump.fun"] and sec["limit"] == 50
    assert sec["min_total_fee"] == 2 and sec["launchpad_platform_v2"] is True
    assert sec["quote_address_type"] == [4, 5, 3, 1, 13, 0]
    await client.aclose()


@pytest.mark.asyncio
async def test_created_tokens_query():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"code": 0, "data": {"inner_count": 1, "open_count": 2, "tokens": []}})

    client = make_client(handler)
    data = await client.created_tokens("sol", "wallet1")
    assert data["open_count"] == 2
    assert captured["params"]["wallet_address"] == "wallet1" and captured["params"]["chain"] == "sol"


@pytest.mark.asyncio
async def test_api_error_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 40001, "error": "INVALID_PARAM", "message": "bad"})

    client = make_client(handler)
    with pytest.raises(GmgnApiError) as exc:
        await client.token_info("sol", "x")
    assert "INVALID_PARAM" in str(exc.value)


@pytest.mark.asyncio
async def test_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": 401, "error": "UNAUTHORIZED"})

    client = make_client(handler)
    with pytest.raises(GmgnAuthError):
        await client.token_info("sol", "x")


@pytest.mark.asyncio
async def test_rate_limit_retries_once_then_raises():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        reset = int(time.time())  # уже истёк — ждать почти не надо
        return httpx.Response(429, json={"code": 429, "error": "RATE_LIMIT_EXCEEDED", "reset_at": reset},
                              headers={"x-ratelimit-reset": str(reset)})

    client = make_client(handler, auto_wait_max=5)
    with pytest.raises(GmgnRateLimited) as exc:
        await client.token_info("sol", "x")
    assert calls["n"] == 2
    assert client.total_rate_limits == 2
    assert client.banned_until > time.time() - 5
    assert exc.value.wait_seconds >= 0


@pytest.mark.asyncio
async def test_rate_limit_long_ban_not_waited():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": 429, "error": "RATE_LIMIT_BANNED",
                                         "reset_at": int(time.time()) + 300})

    client = make_client(handler, auto_wait_max=5)
    with pytest.raises(GmgnRateLimited) as exc:
        await client.token_info("sol", "x")
    assert exc.value.wait_seconds > 250
    # следующий запрос не уходит в сеть, пока бан не снят
    with pytest.raises(GmgnRateLimited):
        await client.token_info("sol", "x")
    assert client.total_requests == 1


@pytest.mark.asyncio
async def test_non_json_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>bad gateway</html>")

    client = make_client(handler)
    with pytest.raises(GmgnApiError) as exc:
        await client.token_info("sol", "x")
    assert "502" in str(exc.value)


@pytest.mark.asyncio
async def test_sol_price():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "data": {"price": {"price": "187.5"}}})

    client = make_client(handler)
    assert await client.sol_price_usd() == 187.5
