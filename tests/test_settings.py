import json

import pytest

from devsearcher.settings import (
    Settings, SettingsError, SettingsStore, chat_ref_to_target, coerce_value, is_valid_chat_ref, normalize_platform,
)


def test_defaults_match_task():
    s = Settings()
    assert s.min_fee_sol == 2.0
    assert s.min_migrate_percent == 5.0
    assert s.max_dev_tokens == 1000
    assert s.platforms == ("Pump.fun",)
    s.validate()


def test_coerce_values():
    assert coerce_value("min_fee_sol", "2,5") == 2.5
    assert coerce_value("min_migrate_percent", "7%") == 7.0
    assert coerce_value("max_dev_tokens", "500") == 500
    assert coerce_value("paused", "on") is True
    assert coerce_value("server_filters", "выкл") is False
    assert coerce_value("platforms", "pump, bonk letsbonk") == ("Pump.fun", "letsbonk")
    assert coerce_value("platforms", "pump stonk") == ("Pump.fun", "stonkfun")
    with pytest.raises(SettingsError):
        coerce_value("max_dev_tokens", "abc")
    with pytest.raises(SettingsError):
        coerce_value("nope", "1")
    with pytest.raises(SettingsError):
        coerce_value("paused", "maybe")


def test_normalize_platform():
    assert normalize_platform("PUMP.FUN") == "Pump.fun"
    assert normalize_platform("пампфан") == "Pump.fun"
    assert normalize_platform("bonk.fun") == "letsbonk"
    assert normalize_platform("stonk") == "stonkfun"
    assert normalize_platform("СТОНКФАН") == "stonkfun"
    assert normalize_platform("stonk.fun") == "stonkfun"
    assert normalize_platform("newpad") == "newpad"


def test_validation():
    with pytest.raises(SettingsError):
        Settings(min_migrate_percent=150).validate()
    with pytest.raises(SettingsError):
        Settings(max_dev_tokens=0).validate()
    with pytest.raises(SettingsError):
        Settings(min_dev_tokens=20, max_dev_tokens=10).validate()
    with pytest.raises(SettingsError):
        Settings(fee_unit="eur").validate()
    with pytest.raises(SettingsError):
        Settings(min_migrated_count=-1).validate()
    assert coerce_value("min_migrated_count", "3") == 3
    with pytest.raises(SettingsError):
        Settings(poll_interval_sec=1).validate()


def test_store_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    assert path.exists()
    s = store.update(min_fee_sol="3", platforms="pump letsbonk", paused="on")
    assert s.min_fee_sol == 3 and s.platforms == ("Pump.fun", "letsbonk") and s.paused

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["min_fee_sol"] == 3 and data["platforms"] == ["Pump.fun", "letsbonk"]

    store2 = SettingsStore(path)
    assert store2.get() == s

    with pytest.raises(SettingsError):
        store.update(max_dev_tokens=0)
    assert store.get().max_dev_tokens == 1000  # неудачное обновление не применилось


def test_store_rejects_broken_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SettingsError):
        SettingsStore(path)


def test_alert_chat_id():
    assert coerce_value("alert_chat_id", " -1001234567890 ") == "-1001234567890"
    assert coerce_value("alert_chat_id", "@my_channel") == "@my_channel"
    assert coerce_value("alert_chat_id", "default") == ""
    assert is_valid_chat_ref("-1001234567890") and is_valid_chat_ref("@abcde")
    assert not is_valid_chat_ref("my channel") and not is_valid_chat_ref("@ab")
    assert chat_ref_to_target("-100123") == -100123 and chat_ref_to_target("@name") == "@name"
    with pytest.raises(SettingsError):
        Settings(alert_chat_id="not a chat").validate()
    Settings(alert_chat_id="@chan_1").validate()
