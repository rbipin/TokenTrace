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


def _seed_row(db, cwd_key, guid, whimsical, created_at):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO project_identities (cwd_key, guid, whimsical_name, created_at) "
        "VALUES (?, ?, ?, ?)",
        (cwd_key, guid, whimsical, created_at),
    )
    conn.commit()
    conn.close()


def test_migration_rekeys_path_to_folder_name(tmp_path):
    db = tmp_path / "usage.db"
    ProjectIdentityStore(db).close()  # create table
    _seed_row(db, r"c:\work\myproj", "aaaaaaaaaaaa", "brave_turing", "2026-01-01 00:00:00")
    store = ProjectIdentityStore(db)  # triggers migration
    rows = store.list_identities()
    assert [r["cwd_key"] for r in rows] == ["myproj"]
    assert rows[0]["guid"] == "aaaaaaaaaaaa"
    assert rows[0]["whimsical_name"] == "brave_turing"


def test_migration_rekeys_path_to_repo_slug(tmp_path):
    repo_dir = tmp_path / "checkout"
    git = repo_dir / ".git"
    git.mkdir(parents=True)
    (git / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/acme/widgets.git\n',
        encoding="utf-8",
    )
    db = tmp_path / "usage.db"
    ProjectIdentityStore(db).close()
    _seed_row(db, str(repo_dir).casefold(), "bbbbbbbbbbbb", None, "2026-01-01 00:00:00")
    store = ProjectIdentityStore(db)
    assert [r["cwd_key"] for r in store.list_identities()] == ["acme/widgets"]


def test_migration_merges_clones_keeping_older_row(tmp_path):
    db = tmp_path / "usage.db"
    ProjectIdentityStore(db).close()
    _seed_row(db, "/old/clone-a/myproj", "aaaaaaaaaaaa", "old_name", "2026-01-01 00:00:00")
    _seed_row(db, "/new/clone-b/myproj", "bbbbbbbbbbbb", "new_name", "2026-06-01 00:00:00")
    store = ProjectIdentityStore(db)
    rows = store.list_identities()
    assert len(rows) == 1
    assert rows[0]["cwd_key"] == "myproj"
    assert rows[0]["guid"] == "aaaaaaaaaaaa"
    assert rows[0]["whimsical_name"] == "old_name"


def test_migration_is_idempotent_and_ignores_new_keys(tmp_path):
    db = tmp_path / "usage.db"
    s1 = ProjectIdentityStore(db)
    guid = s1.resolve_guid("acme/myproj")
    s1.close()
    _seed_row(db, r"c:\work\other", "cccccccccccc", None, "2026-01-01 00:00:00")
    s2 = ProjectIdentityStore(db)
    s2.close()
    s3 = ProjectIdentityStore(db)  # second run: no-op
    rows = s3.list_identities()
    assert {r["cwd_key"] for r in rows} == {"acme/myproj", "other"}
    assert s3.resolve_guid("acme/myproj") == guid


def test_trailing_separator_paths_migrate_cleanly(tmp_path):
    db = tmp_path / "usage.db"
    ProjectIdentityStore(db).close()
    _seed_row(db, "/work/trailing/", "dddddddddddd", None, "2026-01-01 00:00:00")
    store = ProjectIdentityStore(db)
    assert [r["cwd_key"] for r in store.list_identities()] == ["trailing"]


def test_migration_failure_leaves_rows_untouched(tmp_path, monkeypatch, capsys):
    """Mid-migration failure rolls back all changes; original rows are preserved."""
    import src.project_identity as pi_mod

    db = tmp_path / "usage.db"
    ProjectIdentityStore(db).close()  # create table only
    key_a = r"c:\work\alpha"
    key_b = r"c:\work\beta"
    _seed_row(db, key_a, "aaaaaaaaaaaa", "alpha_name", "2026-01-01 00:00:00")
    _seed_row(db, key_b, "bbbbbbbbbbbb", "beta_name", "2026-01-01 00:00:00")

    call_count = {"n": 0}
    real_migrated_key = pi_mod._migrated_key

    def raise_on_second(old_key: str) -> str:
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("injected failure")
        return real_migrated_key(old_key)

    monkeypatch.setattr(pi_mod, "_migrated_key", raise_on_second)

    store = ProjectIdentityStore(db)
    captured = capsys.readouterr()
    assert "Warning [project-identity]: key migration failed" in captured.err
    assert "injected failure" in captured.err

    rows = store.list_identities()
    assert {r["cwd_key"] for r in rows} == {key_a, key_b}
