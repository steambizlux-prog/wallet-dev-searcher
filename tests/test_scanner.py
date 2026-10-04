import time

import pytest

from devsearcher.gmgn import GmgnClient
from devsearcher.scanner import Scanner
from devsearcher.settings import SettingsStore
from devsearcher.storage import Storage

DEV_GOOD = "DevGood11111111111111111111111111111111111"
DEV_BAD = "DevBad111111111111111111111111111111111111"
TOK = {
    "good": "TokGood1111111111111111111111111111111111",
    "lowfee": "TokLow11111111111111111111111111111111111",
    "baddev": "TokBad11111111111111111111111111111111111",
    "nofee": "TokNoFee111111111111111111111111111111111",
    "old": "TokOld11111111111111111111111111111111111",
    "other": "TokOther111111111111111111111111111111111",
}


class FakeGmgn(GmgnClient):
    """Подменяет сетевые методы, остальное (счётчики, banned_until) — от настоящего клиента."""

    def __init__(self):
        super().__init__("key", request_gap=0.0)
        self.now = int(time.time())
        self.calls: list[tuple] = []
        self.completed = [
            {"address": TOK["good"], "symbol": "GOOD", "creator": DEV_GOOD, "launchpad_platform": "Pump.fun",
             "total_fee": "2.5", "open_timestamp": self.now - 60, "usd_market_cap": "50000"},
            {"address": TOK["lowfee"], "symbol": "LOW", "creator": DEV_GOOD, "launchpad_platform": "Pump.fun",
             "total_fee": "0.4", "open_timestamp": self.now - 60},
            {"address": TOK["baddev"], "symbol": "BAD", "creator": DEV_BAD, "launchpad_platform": "Pump.fun",
             "total_fee": "9", "open_timestamp": self.now - 60},
            {"address": TOK["nofee"], "symbol": "NOFEE", "creator": DEV_GOOD, "launchpad_platform": "Pump.fun",
             "open_timestamp": self.now - 60},
            {"address": TOK["old"], "symbol": "OLD", "creator": DEV_GOOD, "launchpad_platform": "Pump.fun",
             "total_fee": "9", "open_timestamp": self.now - 10 * 3600},
            {"address": TOK["other"], "symbol": "OTH", "creator": DEV_GOOD, "launchpad_platform": "letsbonk",
             "total_fee": "9", "open_timestamp": self.now - 60},
        ]
        self.created = {
            DEV_GOOD: {"inner_count": 90, "open_count": 10, "open_ratio": "0.1",
                       "creator_ath_info": {"ath_mc": "500000", "token_symbol": "ATH", "ath_token": TOK["good"]},
                       "tokens": [{"token_address": TOK["nofee"], "is_open": True, "total_fee": "5"}]},
            DEV_BAD: {"inner_count": 990, "open_count": 10, "tokens": []},
        }

    async def completed_tokens(self, chain, platforms=None, limit=80, filters=None):
        self.calls.append(("completed", tuple(platforms or ()), filters))
        return list(self.completed)

    async def created_tokens(self, chain, wallet, migrate_state=None, order_by=None, direction=None):
        self.calls.append(("created", wallet))
        return self.created.get(wallet, {})

    async def token_info(self, chain, address):
        self.calls.append(("info", address))
        return {"symbol": "X", "dev": {"creator_address": DEV_GOOD, "creator_token_status": "creator_hold",
                                       "fund_from": "Funder1111111111111111111111111111111111111"}}


@pytest.fixture
def env(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    storage = Storage(tmp_path / "db.sqlite")
    sent: list[str] = []

    async def notify(text):
        sent.append(text)

    gmgn = FakeGmgn()
    return store, storage, sent, gmgn, Scanner(gmgn, store, storage, notify)


@pytest.mark.asyncio
async def test_poll_once_filters_and_alerts(env):
    store, storage, sent, gmgn, scanner = env
    alerted = await scanner.poll_once(store.get())

    assert alerted == [TOK["good"]]
    assert len(sent) == 1
    msg = sent[0]
    assert f"<code>{DEV_GOOD}</code>" in msg
    assert f"https://gmgn.ai/sol/address/{DEV_GOOD}" in msg
    assert "10</b> (10.0%)" in msg and "2.50 SOL" in msg
    assert "Фандинг с" in msg

    assert storage.seen_status(TOK["good"]) == "match"
    assert storage.seen_status(TOK["lowfee"]) == "low_fee"
    assert storage.seen_status(TOK["baddev"]) == "dev_rejected"
    assert storage.seen_status(TOK["old"]) == "old"
    assert storage.seen_status(TOK["other"]) == "platform"
    # fee взят из строки created_tokens (5 SOL), но дев уже отправлялся → dup_dev
    assert storage.seen_status(TOK["nofee"]) == "dup_dev"

    c = storage.counters()
    assert c["tokens_seen"] == 6 and c["matches"] == 1 and c["fee_rejected"] == 1
    assert c["devs_checked"] == 3 and c["dup_dev"] == 1
    # created_tokens для DEV_GOOD кэшируется: вызван 1 раз, для DEV_BAD — 1 раз
    assert sum(1 for x in gmgn.calls if x == ("created", DEV_GOOD)) == 1
    assert sum(1 for x in gmgn.calls if x == ("created", DEV_BAD)) == 1

    # повторный опрос ничего не шлёт
    assert await scanner.poll_once(store.get()) == []
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_fee_unknown_policy_skip(env):
    store, storage, sent, gmgn, scanner = env
    gmgn.created[DEV_GOOD]["tokens"] = []  # fee взять неоткуда
    gmgn.completed = [c for c in gmgn.completed if c["address"] == TOK["nofee"]]
    await scanner.poll_once(store.get())
    assert sent == [] and storage.seen_status(TOK["nofee"]) == "fee_unknown"

    store.update(fee_unknown_policy="check")
    storage.prune(0)
    await scanner.poll_once(store.get())
    assert len(sent) == 1 and storage.seen_status(TOK["nofee"]) == "match"


@pytest.mark.asyncio
async def test_settings_change_thresholds(env):
    store, storage, sent, gmgn, scanner = env
    store.update(min_migrate_percent=20)  # DEV_GOOD даёт 10 % — не проходит
    await scanner.poll_once(store.get())
    assert sent == [] and storage.seen_status(TOK["good"]) == "dev_rejected"

    store.update(min_migrate_percent=1, max_dev_tokens=50)  # 100 запусков > 50
    storage.prune(0)
    await scanner.poll_once(store.get())
    assert sent == []


@pytest.mark.asyncio
async def test_server_filters_passed(env):
    store, storage, sent, gmgn, scanner = env
    store.update(server_filters="on", min_dev_tokens=3)
    await scanner.poll_once(store.get())
    _, platforms, filters = gmgn.calls[0]
    assert platforms == ("Pump.fun",)
    assert filters == {"max_creator_created_count": 1000, "min_creator_created_open_ratio": 0.05,
                       "min_creator_created_count": 3, "min_total_fee": 2.0}


@pytest.mark.asyncio
async def test_creator_resolved_via_token_info(env):
    store, storage, sent, gmgn, scanner = env
    gmgn.completed = [{"address": TOK["good"], "symbol": "G", "launchpad_platform": "Pump.fun",
                       "total_fee": "3", "open_timestamp": gmgn.now}]
    await scanner.poll_once(store.get())
    assert len(sent) == 1 and ("info", TOK["good"]) in gmgn.calls


@pytest.mark.asyncio
async def test_check_wallet(env):
    store, storage, sent, gmgn, scanner = env
    stats, verdict = await scanner.check_wallet(DEV_GOOD)
    assert verdict.passed and stats.total == 100 and stats.migrated == 10
    stats, verdict = await scanner.check_wallet(DEV_BAD)
    assert not verdict.passed and "1000" in verdict.reasons[0]
