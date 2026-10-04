"""SQLite-хранилище: просмотренные токены, проверки девов, найденные совпадения, счётчики."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_tokens (
    address     TEXT PRIMARY KEY,
    first_seen  REAL NOT NULL,
    status      TEXT NOT NULL,
    note        TEXT
);
CREATE TABLE IF NOT EXISTS dev_checks (
    wallet       TEXT PRIMARY KEY,
    checked_at   REAL NOT NULL,
    total        INTEGER NOT NULL,
    migrated     INTEGER NOT NULL,
    ratio        REAL NOT NULL,
    passed       INTEGER NOT NULL,
    payload      TEXT
);
CREATE TABLE IF NOT EXISTS matches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL NOT NULL,
    wallet      TEXT NOT NULL,
    token       TEXT NOT NULL,
    symbol      TEXT,
    fee         REAL,
    total       INTEGER,
    migrated    INTEGER,
    ratio       REAL,
    payload     TEXT
);
CREATE INDEX IF NOT EXISTS idx_matches_wallet ON matches(wallet, created_at);
CREATE TABLE IF NOT EXISTS counters (
    name   TEXT PRIMARY KEY,
    value  INTEGER NOT NULL
);
"""


class Storage:
    def __init__(self, path: Path | str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- просмотренные токены ----

    def is_seen(self, address: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM seen_tokens WHERE address = ?", (address,)).fetchone()
        return row is not None

    def mark_seen(self, address: str, status: str, note: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO seen_tokens(address, first_seen, status, note) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(address) DO UPDATE SET status = excluded.status, note = excluded.note",
                (address, time.time(), status, note),
            )
            self._conn.commit()

    def seen_status(self, address: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT status FROM seen_tokens WHERE address = ?", (address,)).fetchone()
        return row["status"] if row else None

    # ---- проверки девов ----

    def get_dev_check(self, wallet: str, max_age_sec: float | None = None) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM dev_checks WHERE wallet = ?", (wallet,)).fetchone()
        if row is None:
            return None
        if max_age_sec is not None and time.time() - row["checked_at"] > max_age_sec:
            return None
        d = dict(row)
        d["passed"] = bool(d["passed"])
        try:
            d["payload"] = json.loads(d["payload"]) if d.get("payload") else None
        except ValueError:
            d["payload"] = None
        return d

    def save_dev_check(self, wallet: str, total: int, migrated: int, ratio: float,
                       passed: bool, payload: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO dev_checks(wallet, checked_at, total, migrated, ratio, passed, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(wallet) DO UPDATE SET checked_at = excluded.checked_at, total = excluded.total, "
                "migrated = excluded.migrated, ratio = excluded.ratio, passed = excluded.passed, "
                "payload = excluded.payload",
                (wallet, time.time(), total, migrated, ratio, int(passed),
                 json.dumps(payload, ensure_ascii=False) if payload is not None else None),
            )
            self._conn.commit()

    # ---- совпадения ----

    def last_alert_at(self, wallet: str) -> float | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(created_at) AS t FROM matches WHERE wallet = ?", (wallet,)
            ).fetchone()
        return row["t"] if row and row["t"] is not None else None

    def record_match(self, wallet: str, token: str, symbol: str | None, fee: float | None,
                     total: int, migrated: int, ratio: float, payload: dict[str, Any] | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO matches(created_at, wallet, token, symbol, fee, total, migrated, ratio, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), wallet, token, symbol, fee, total, migrated, ratio,
                 json.dumps(payload, ensure_ascii=False) if payload is not None else None),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def recent_matches(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM matches ORDER BY created_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    def matches_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM matches").fetchone()
        return int(row["c"]) if row else 0

    # ---- счётчики ----

    def incr(self, name: str, by: int = 1) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO counters(name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = value + excluded.value",
                (name, by),
            )
            self._conn.commit()

    def counters(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT name, value FROM counters").fetchall()
        return {r["name"]: int(r["value"]) for r in rows}

    # ---- обслуживание ----

    def prune(self, older_than_days: float = 7.0) -> int:
        cutoff = time.time() - older_than_days * 86400
        with self._lock:
            cur = self._conn.execute("DELETE FROM seen_tokens WHERE first_seen < ?", (cutoff,))
            self._conn.commit()
        return cur.rowcount or 0


__all__ = ["Storage"]
