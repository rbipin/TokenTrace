from __future__ import annotations

import sqlite3

from src.stores.sqlite import SqliteStore


def test_record_run_inserts_single_row(tmp_db):
    store = SqliteStore(tmp_db)
    store.record_run()
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute("SELECT id, ran_at FROM run_log").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 1
    assert rows[0][1] is not None


def test_record_run_upserts_not_grows(tmp_db):
    store = SqliteStore(tmp_db)
    store.record_run("2026-01-01T00:00:00")
    store.record_run("2026-01-02T00:00:00")
    conn = sqlite3.connect(tmp_db)
    rows = conn.execute("SELECT ran_at FROM run_log").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "2026-01-02T00:00:00"


def test_record_run_accepts_explicit_timestamp(tmp_db):
    store = SqliteStore(tmp_db)
    store.record_run("2026-07-19T10:00:00+00:00")
    conn = sqlite3.connect(tmp_db)
    row = conn.execute("SELECT ran_at FROM run_log WHERE id = 1").fetchone()
    assert row[0] == "2026-07-19T10:00:00+00:00"
