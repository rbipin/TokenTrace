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
    assert store.resolve_guid("/work/myproj") == store.resolve_guid("/work/myproj")


def test_cwd_lookup_is_case_insensitive(store):
    assert store.resolve_guid("C:/Work/MyProj") == store.resolve_guid("c:/work/myproj")


def test_cwd_lookup_strips_whitespace(store):
    assert store.resolve_guid("/work/myproj") == store.resolve_guid("  /work/myproj  ")


def test_distinct_cwds_distinct_guids(store):
    assert store.resolve_guid("/work/a") != store.resolve_guid("/work/b")


def test_guid_is_short_hex(store):
    guid = store.resolve_guid("/work/myproj")
    assert len(guid) == 12
    int(guid, 16)  # raises if not hex


def test_missing_cwd_returns_none(store):
    assert store.resolve_guid(None) is None
    assert store.resolve_guid("") is None
    assert store.resolve_whimsical(None) is None


def test_same_guid_same_whimsical(store):
    name = store.resolve_whimsical("/work/myproj")
    assert "_" in name
    assert store.resolve_whimsical("/Work/MyProj") == name


def test_distinct_projects_distinct_whimsical(store):
    assert store.resolve_whimsical("/work/a") != store.resolve_whimsical("/work/b")


def test_whimsical_stable_across_reopen(tmp_path):
    db = tmp_path / "usage.db"
    s1 = ProjectIdentityStore(db)
    name = s1.resolve_whimsical("/work/myproj")
    s1.close()
    s2 = ProjectIdentityStore(db)
    assert s2.resolve_whimsical("/work/myproj") == name
    s2.close()


def test_table_schema(tmp_path):
    db = tmp_path / "usage.db"
    ProjectIdentityStore(db).resolve_guid("/work/x")
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(project_identities)")}
    assert cols == {"cwd_key", "guid", "whimsical_name", "created_at"}
