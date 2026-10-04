from devsearcher.analyzer import (
    evaluate_dev, extract_fee, fee_in_sol, fee_passes, find_token_row, is_valid_sol_address,
    parse_candidate, parse_dev_extra, parse_dev_stats, platform_matches,
)
from devsearcher.settings import Settings


def test_extract_fee_prefers_total_fee_and_parses_strings():
    assert extract_fee({"total_fee": "2.5", "coin_creator_fee": 0}) == (2.5, "total_fee")
    assert extract_fee({"coin_creator_fee": "0.7"}) == (0.7, "coin_creator_fee")
    assert extract_fee({"total_fee": "", "fee": "1"}) == (1.0, "fee")
    assert extract_fee({"symbol": "X"}) == (None, None)
    assert extract_fee(None) == (None, None)


def test_parse_candidate_reads_trenches_item():
    item = {
        "address": "So11111111111111111111111111111111111111112", "symbol": "WSOL", "name": "Wrapped",
        "creator": "11111111111111111111111111111111", "launchpad_platform": "Pump.fun",
        "total_fee": "3.1", "usd_market_cap": "85000.5", "liquidity": 40000, "holder_count": "120",
        "open_timestamp": 1700000000, "created_timestamp": 1699990000,
    }
    c = parse_candidate(item)
    assert c.address == item["address"]
    assert c.creator == item["creator"]
    assert c.platform == "Pump.fun"
    assert c.fee == 3.1 and c.fee_key == "total_fee"
    assert c.market_cap == 85000.5 and c.liquidity == 40000 and c.holder_count == 120
    assert c.event_timestamp == 1700000000


def test_parse_candidate_falls_back_to_complete_timestamp():
    c = parse_candidate({"address": "a", "complete_timestamp": 5, "created_timestamp": 1})
    assert c.event_timestamp == 5


def test_parse_dev_stats_uses_counters():
    data = {
        "inner_count": 950, "open_count": 50, "open_ratio": "0.05",
        "creator_ath_info": {"ath_mc": "1200000", "token_symbol": "BEST", "ath_token": "tok"},
        "tokens": [{"token_address": "t1", "is_open": True}],
        "last_create_timestamp": 123,
    }
    s = parse_dev_stats(data, "wallet")
    assert s.total == 1000 and s.migrated == 50 and s.on_curve == 950
    assert abs(s.ratio_percent - 5.0) < 1e-9
    assert s.ath_symbol == "BEST" and s.ath_mc == 1200000 and s.ath_token == "tok"
    assert s.counters_present and s.tokens_sampled == 1 and s.last_create_timestamp == 123


def test_parse_dev_stats_takes_max_of_counters_and_array():
    tokens = [{"token_address": f"t{i}", "is_open": i < 17} for i in range(95)]
    s = parse_dev_stats({"inner_count": 63, "open_count": 17, "tokens": tokens}, "w")
    assert s.total == 95 and s.migrated == 17


def test_parse_dev_stats_without_counters_counts_array():
    tokens = [{"token_address": "a", "is_open": True}, {"token_address": "b", "is_open": False}]
    s = parse_dev_stats({"tokens": tokens}, "w")
    assert s.total == 2 and s.migrated == 1 and not s.counters_present
    assert s.ratio_percent == 50


def test_parse_dev_stats_empty():
    s = parse_dev_stats({}, "w")
    assert s.total == 0 and s.migrated == 0 and s.ratio_percent == 0
    assert parse_dev_stats(None, "w").total == 0


def test_evaluate_dev_thresholds():
    st = Settings(min_migrate_percent=5, max_dev_tokens=1000, min_dev_tokens=1)
    assert evaluate_dev(parse_dev_stats({"inner_count": 950, "open_count": 50}, "w"), st).passed
    assert evaluate_dev(parse_dev_stats({"inner_count": 951, "open_count": 50}, "w"), st).passed is False
    v = evaluate_dev(parse_dev_stats({"inner_count": 96, "open_count": 4}, "w"), st)
    assert not v.passed and "мигрейтов" in v.reasons[0]
    assert evaluate_dev(parse_dev_stats({}, "w"), st).reasons == ["нет данных о запусках дева"]
    st2 = Settings(min_dev_tokens=10)
    v2 = evaluate_dev(parse_dev_stats({"inner_count": 1, "open_count": 1}, "w"), st2)
    assert not v2.passed and "минимума 10" in v2.reasons[0]


def test_fee_passes_sol_and_usd():
    st = Settings(min_fee_sol=2.0, fee_unit="sol")
    assert fee_passes(2.0, st, None) is True
    assert fee_passes(1.99, st, None) is False
    assert fee_passes(None, st, None) is None
    usd = Settings(min_fee_sol=2.0, fee_unit="usd")
    assert fee_passes(500, usd, 200.0) is True     # порог 400 USD
    assert fee_passes(300, usd, 200.0) is False
    assert fee_passes(300, usd, None) is None       # нет курса
    assert fee_in_sol(500, usd, 200.0) == 2.5
    assert fee_in_sol(500, usd, None) is None
    assert fee_in_sol(3, st, None) == 3


def test_find_token_row():
    data = {"tokens": [{"token_address": "a", "total_fee": "1"}, {"token_address": "b"}]}
    assert find_token_row(data, "b") == {"token_address": "b"}
    assert find_token_row(data, "zzz") is None
    assert find_token_row(None, "a") is None


def test_parse_dev_extra():
    info = {"launchpad_status": "2", "dev": {
        "creator_address": "dev", "creator_token_status": "creator_hold", "creator_open_count": "7",
        "fund_from": "funder", "cto_flag": 1, "dexscr_ad": 0, "dexscr_boost_fee": "1",
        "twitter_name_change_history": [{"twitter_username": "x"}, {"twitter_username": "y"}],
        "ath_token_info": {"symbol": "TOP", "ath_mc": "99.5"},
    }}
    e = parse_dev_extra(info)
    assert e.creator_address == "dev" and e.creator_token_status == "creator_hold"
    assert e.creator_open_count == 7 and e.fund_from == "funder"
    assert e.cto_flag is True and e.dexscr_ad is False and e.dexscr_boost is True
    assert e.twitter_renames == 2 and e.ath_symbol == "TOP" and e.ath_mc == 99.5
    assert e.launchpad_status == 2
    assert parse_dev_extra({}).creator_address is None


def test_is_valid_sol_address():
    assert is_valid_sol_address("So11111111111111111111111111111111111111112")
    assert not is_valid_sol_address("0x0000000000000000000000000000000000000000")
    assert not is_valid_sol_address("")
    assert not is_valid_sol_address("abc")


def test_platform_matches_is_lenient_about_spelling():
    assert platform_matches("Pump.fun", ("Pump.fun",))
    assert platform_matches("pump", ("Pump.fun",))
    assert platform_matches("pumpfun", ("pump",))
    assert platform_matches("letsbonk", ("Pump.fun", "letsbonk.fun"))
    assert platform_matches(None, ("Pump.fun",))
    assert platform_matches("", ("Pump.fun",))
    assert not platform_matches("letsbonk", ("Pump.fun",))
    assert not platform_matches("pump_mayhem", ("Pump.fun",))


def test_evaluate_dev_min_migrated_count():
    st = Settings(min_migrate_percent=0, min_migrated_count=2)
    assert evaluate_dev(parse_dev_stats({"inner_count": 98, "open_count": 2}, "w"), st).passed
    v = evaluate_dev(parse_dev_stats({"inner_count": 99, "open_count": 1}, "w"), st)
    assert not v.passed and "1 шт < минимума 2 шт" in v.reasons[0]
    # штуки и процент работают вместе: 2 из 100 = 2 % < 5 %
    both = Settings(min_migrate_percent=5, min_migrated_count=2)
    v2 = evaluate_dev(parse_dev_stats({"inner_count": 98, "open_count": 2}, "w"), both)
    assert not v2.passed and len(v2.reasons) == 1 and "%" in v2.reasons[0]
