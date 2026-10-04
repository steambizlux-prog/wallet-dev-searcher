import pytest

from devsearcher.config import ConfigError, load_config


def _env(monkeypatch, **kw):
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_ADMIN_IDS", "GMGN_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def test_private_chat_is_admin_by_default(monkeypatch, tmp_path):
    _env(monkeypatch, TELEGRAM_BOT_TOKEN="1:x", TELEGRAM_CHAT_ID="42", GMGN_API_KEY="k")
    cfg = load_config(tmp_path / "none.env")
    assert cfg.telegram_chat_id == 42 and cfg.admin_ids == frozenset({42})


def test_channel_requires_admin_ids(monkeypatch, tmp_path):
    _env(monkeypatch, TELEGRAM_BOT_TOKEN="1:x", TELEGRAM_CHAT_ID="-100500", GMGN_API_KEY="k")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "none.env")
    _env(monkeypatch, TELEGRAM_BOT_TOKEN="1:x", TELEGRAM_CHAT_ID="@chan", GMGN_API_KEY="k", TELEGRAM_ADMIN_IDS="7, 8")
    cfg = load_config(tmp_path / "none.env")
    assert cfg.telegram_chat_id == "@chan" and cfg.admin_ids == frozenset({7, 8})


def test_missing_values(monkeypatch, tmp_path):
    _env(monkeypatch, TELEGRAM_BOT_TOKEN="1:x", TELEGRAM_CHAT_ID="42")
    with pytest.raises(ConfigError):
        load_config(tmp_path / "none.env")
