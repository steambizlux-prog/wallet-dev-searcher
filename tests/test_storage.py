from devsearcher.storage import Storage


def test_seen_and_counters(tmp_path):
    st = Storage(tmp_path / "db.sqlite")
    assert not st.is_seen("a")
    st.mark_seen("a", "match", "dev")
    assert st.is_seen("a") and st.seen_status("a") == "match"
    st.mark_seen("a", "old")
    assert st.seen_status("a") == "old"
    st.incr("tokens_seen")
    st.incr("tokens_seen", 2)
    assert st.counters() == {"tokens_seen": 3}
    st.close()


def test_dev_checks_and_matches(tmp_path):
    st = Storage(tmp_path / "db.sqlite")
    assert st.get_dev_check("w") is None
    st.save_dev_check("w", 100, 10, 10.0, True, {"token": "t"})
    d = st.get_dev_check("w")
    assert d["total"] == 100 and d["passed"] is True and d["payload"] == {"token": "t"}
    assert st.get_dev_check("w", max_age_sec=3600) is not None

    assert st.last_alert_at("w") is None
    st.record_match("w", "t", "SYM", 2.5, 100, 10, 10.0)
    assert st.last_alert_at("w") is not None
    rows = st.recent_matches(5)
    assert len(rows) == 1 and rows[0]["wallet"] == "w" and rows[0]["symbol"] == "SYM"
    assert st.matches_count() == 1
    st.close()


def test_prune(tmp_path):
    st = Storage(tmp_path / "db.sqlite")
    st.mark_seen("a", "old")
    assert st.prune(older_than_days=0) == 1
    assert not st.is_seen("a")
    st.close()
