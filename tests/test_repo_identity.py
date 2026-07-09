from __future__ import annotations

from src.repo_identity import resolve_repo_slug, _slug_from_url


def _make_repo(root, url: str | None) -> None:
    """Create a fake git repo at *root* with an optional origin url."""
    git = root / ".git"
    git.mkdir(parents=True)
    lines = ["[core]", "\trepositoryformatversion = 0"]
    if url is not None:
        lines += ['[remote "origin"]', f"\turl = {url}",
                  "\tfetch = +refs/heads/*:refs/remotes/origin/*"]
    (git / "config").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_https_url(tmp_path):
    _make_repo(tmp_path, "https://github.com/rbipin/TokenTrace.git")
    assert resolve_repo_slug(str(tmp_path)) == "rbipin/TokenTrace"


def test_scp_style_url(tmp_path):
    _make_repo(tmp_path, "git@github.com:rbipin/TokenTrace.git")
    assert resolve_repo_slug(str(tmp_path)) == "rbipin/TokenTrace"


def test_ssh_url_with_port(tmp_path):
    _make_repo(tmp_path, "ssh://git@gitlab.example.com:2222/team/proj.git")
    assert resolve_repo_slug(str(tmp_path)) == "team/proj"


def test_url_without_dot_git_suffix(tmp_path):
    _make_repo(tmp_path, "https://github.com/rbipin/TokenTrace")
    assert resolve_repo_slug(str(tmp_path)) == "rbipin/TokenTrace"


def test_walks_up_from_subdirectory(tmp_path):
    _make_repo(tmp_path, "https://github.com/acme/widgets.git")
    sub = tmp_path / "src" / "deep"
    sub.mkdir(parents=True)
    assert resolve_repo_slug(str(sub)) == "acme/widgets"


def test_no_origin_remote(tmp_path):
    _make_repo(tmp_path, None)
    assert resolve_repo_slug(str(tmp_path)) is None


def test_no_git_dir(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert resolve_repo_slug(str(plain)) is None


def test_nonexistent_path(tmp_path):
    assert resolve_repo_slug(str(tmp_path / "gone")) is None


def test_none_and_empty_input():
    assert resolve_repo_slug(None) is None
    assert resolve_repo_slug("") is None
    assert resolve_repo_slug("   ") is None


def test_worktree_gitdir_pointer(tmp_path):
    main = tmp_path / "main"
    _make_repo(main, "https://github.com/acme/widgets.git")
    wt_gitdir = main / ".git" / "worktrees" / "wt1"
    wt_gitdir.mkdir(parents=True)
    worktree = tmp_path / "wt1"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")
    assert resolve_repo_slug(str(worktree)) == "acme/widgets"


def test_malformed_config_returns_none(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text("not [ valid % ini \x00", encoding="utf-8")
    assert resolve_repo_slug(str(tmp_path)) is None


def test_slug_from_url_edge_cases():
    assert _slug_from_url("https://github.com/a/b.git") == "a/b"
    assert _slug_from_url("git@github.com:a/b") == "a/b"
    assert _slug_from_url("https://host.com/group/sub/repo.git") == "sub/repo"
    assert _slug_from_url("https://host.com/onlyrepo") is None
    assert _slug_from_url("") is None
