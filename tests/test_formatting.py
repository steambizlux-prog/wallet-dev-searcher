from devsearcher.analyzer import DevStats, Verdict, parse_candidate
from devsearcher.bot import split_message
from devsearcher.formatting import fmt_usd, format_dev_check, format_match, format_settings, format_status
from devsearcher.settings import Settings


def test_fmt_usd():
    assert fmt_usd(None) == "—"
    assert fmt_usd(950) == "$950"
    assert fmt_usd(85_400) == "$85.4K"
    assert fmt_usd(1_250_000) == "$1.25M"
    assert fmt_usd(2_000_000_000) == "$2.00B"


def test_format_match_escapes_and_links():
    cand = parse_candidate({"address": "Tok111", "symbol": "<b>EVIL</b>", "name": "a&b", "creator": "Dev111",
                            "launchpad_platform": "Pump.fun", "total_fee": "2.5"})
    stats = DevStats(wallet="Dev111", total=200, migrated=20, on_curve=180, ratio_percent=10.0,
                     ath_symbol="TOP", ath_mc=1_000_000, ath_token="Ath111")
    text = format_match(cand, stats, Settings(), 2.5)
    assert "<code>Dev111</code>" in text
    assert 'href="https://gmgn.ai/sol/address/Dev111"' in text
    assert 'href="https://gmgn.ai/sol/token/Tok111"' in text
    assert "&lt;b&gt;EVIL&lt;/b&gt;" in text and "<b>EVIL" not in text
    assert "a&amp;b" in text
    assert "2.50 SOL" in text and "$1.00M" in text


def test_format_dev_check_reasons():
    stats = DevStats(wallet="W", total=10, migrated=0, on_curve=10, ratio_percent=0.0)
    text = format_dev_check(stats, Verdict(False, ["мигрейтов 0.0% < минимума 5%"]), Settings())
    assert "не подходит" in text and "мигрейтов 0.0%" in text


def test_format_settings_and_status():
    s = Settings()
    text = format_settings(s)
    assert "min_fee_sol" in text and "Pump.fun" in text
    status = format_status({"running": True, "requests": 5, "errors": 0, "rate_limits": 0}, s, {"matches": 2})
    assert "работает" in status and "Совпадений: 2" in status


def test_split_message():
    text = "\n".join("x" * 100 for _ in range(100))
    parts = split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")
