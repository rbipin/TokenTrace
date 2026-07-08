from __future__ import annotations

import pytest

from src.project_identity import (
    PROJECT_NAME_MODES,
    ProjectIdentityStore,
    ProjectNameResolver,
)


@pytest.fixture
def identity_store(tmp_path):
    s = ProjectIdentityStore(tmp_path / "usage.db")
    yield s
    s.close()


def test_modes_constant():
    assert PROJECT_NAME_MODES == ("yes", "no", "whimsical")


def test_invalid_mode_rejected():
    with pytest.raises(ValueError, match="whimsical"):
        ProjectNameResolver("true")


def test_yes_returns_display_name():
    resolver = ProjectNameResolver("yes")
    assert resolver.resolve("myrepo", "/work/myrepo") == "myrepo"


def test_yes_without_display_name_returns_none():
    assert ProjectNameResolver("yes").resolve(None, "/work/x") is None


def test_no_returns_stable_guid(identity_store):
    resolver = ProjectNameResolver("no", identity_store)
    first = resolver.resolve("myrepo", "/work/myrepo")
    assert first == identity_store.resolve_guid("/work/myrepo")
    assert resolver.resolve("other-display-name", "/Work/MyRepo") == first


def test_whimsical_returns_stable_name(identity_store):
    resolver = ProjectNameResolver("whimsical", identity_store)
    name = resolver.resolve("myrepo", "/work/myrepo")
    assert "_" in name
    assert resolver.resolve("myrepo", "/work/myrepo") == name


def test_masked_modes_ignore_display_name_as_key(identity_store):
    resolver = ProjectNameResolver("no", identity_store)
    a = resolver.resolve("same-display", "/work/a")
    b = resolver.resolve("same-display", "/work/b")
    assert a != b


def test_missing_cwd_returns_none_in_masked_modes(identity_store):
    assert ProjectNameResolver("no", identity_store).resolve("x", None) is None
    assert ProjectNameResolver("whimsical", identity_store).resolve("x", "") is None


def test_masked_mode_requires_identity_store():
    with pytest.raises(ValueError, match="identity_store"):
        ProjectNameResolver("no")


def test_identity_store_error_warns_and_returns_none(identity_store, capsys):
    def boom(cwd):
        raise RuntimeError("db locked")

    identity_store.resolve_guid = boom
    resolver = ProjectNameResolver("no", identity_store)
    assert resolver.resolve("x", "/work/x") is None
    assert "Warning" in capsys.readouterr().err
