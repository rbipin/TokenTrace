from __future__ import annotations

import sqlite3

import pytest

from src.project_identity import ProjectIdentityStore


@pytest.fixture
def store(tmp_path):
    s = ProjectIdentityStore(tmp_path / "usage.db")
    yield s
    s.close()


def test_same_cwd_same_guid(store):
    assert store.resolve_guid("acme/myproj") == store.resolve_guid("acme/myproj")


def test_cwd_lookup_is_case_insensitive(store):
    assert store.resolve_guid("Acme/MyProj") == store.resolve_guid("acme/myproj")


def test_cwd_lookup_strips_whitespace(store):
    assert store.resolve_guid("acme/myproj") == store.resolve_guid("  acme/myproj  ")


def test_distinct_cwds_distinct_guids(store):
    assert store.resolve_guid("acme/a") != store.resolve_guid("acme/b")


def test_guid_is_short_hex(store):
    guid = store.resolve_guid("acme/myproj")
    assert len(guid) == 12
    int(guid, 16)  # raises if not hex


def test_missing_cwd_returns_none(store):
    assert store.resolve_guid(None) is None
    assert store.resolve_guid("") is None
    assert store.resolve_whimsical(None) is None


def test_same_guid_same_whimsical(store):
    name = store.resolve_whimsical("acme/myproj")
    assert "_" in name
    assert store.resolve_whimsical("Acme/MyProj") == name


def test_distinct_projects_distinct_whimsical(store):
    assert store.resolve_whimsical("acme/a") != store.resolve_whimsical("acme/b")


def test_whimsical_stable_across_reopen(tmp_path):
    db = tmp_path / "usage.db"
    s1 = ProjectIdentityStore(db)
    name = s1.resolve_whimsical("acme/myproj")
    s1.close()
    s2 = ProjectIdentityStore(db)
    assert s2.resolve_whimsical("acme/myproj") == name
    s2.close()


def test_table_schema(tmp_path):
    db = tmp_path / "usage.db"
    ProjectIdentityStore(db).resolve_guid("acme/x")
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(project_identities)")}
    assert cols == {"cwd_key", "guid", "whimsical_name", "created_at"}


def test_concurrent_resolution_is_stable(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    db = tmp_path / "usage.db"
    store = ProjectIdentityStore(db)
    with ThreadPoolExecutor(max_workers=8) as pool:
        guids = list(pool.map(lambda _: store.resolve_guid("acme/shared"), range(16)))
        names = list(pool.map(lambda _: store.resolve_whimsical("acme/shared"), range(16)))
    assert len(set(guids)) == 1
    assert len(set(names)) == 1
    assert None not in guids and None not in names


def test_list_identities_empty(store):
    assert store.list_identities() == []


def test_list_identities_returns_rows_sorted_by_cwd(store):
    store.resolve_whimsical("acme/beta")
    store.resolve_guid("acme/alpha")
    rows = store.list_identities()
    assert [r["cwd_key"] for r in rows] == ["acme/alpha", "acme/beta"]
    assert rows[0]["whimsical_name"] is None
    assert rows[1]["whimsical_name"]
    assert all(len(r["guid"]) == 12 for r in rows)
    assert all(r["created_at"] for r in rows)


