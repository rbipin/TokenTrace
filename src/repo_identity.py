"""Resolve a working directory to its git repo slug (``owner/repo``).

Strictly read-only and best-effort: walks up from the cwd looking for a
``.git`` directory (or worktree pointer file), parses the origin remote URL
from the git ``config`` file with :mod:`configparser` (git config is
INI-compatible for this purpose), and returns the last two path segments of
the remote URL. Returns ``None`` on any failure — never raises, never runs
a ``git`` subprocess.
"""
from __future__ import annotations

import configparser
from functools import lru_cache
from pathlib import Path

_MAX_WALK_UP = 64


def resolve_repo_slug(cwd: str | None) -> str | None:
    """Return ``owner/repo`` for *cwd*, or ``None`` if unresolvable."""
    if not cwd or not cwd.strip():
        return None
    return _resolve_cached(cwd.strip())


@lru_cache(maxsize=1024)
def _resolve_cached(cwd: str) -> str | None:
    try:
        start = Path(cwd)
        if not start.is_dir():
            return None
        git_dir = _find_git_dir(start)
        if git_dir is None:
            return None
        url = _origin_url(git_dir / "config")
        if not url:
            return None
        return _slug_from_url(url)
    except Exception:
        # Public contract is best-effort, never-raise. Any unexpected failure
        # (e.g., RuntimeError from Path.resolve() on symlink loops) resolves to None.
        return None


def _find_git_dir(start: Path) -> Path | None:
    current = start
    for _ in range(_MAX_WALK_UP):
        candidate = current / ".git"
        if candidate.is_dir():
            return candidate
        if candidate.is_file():
            return _follow_gitdir_pointer(candidate)
        if current.parent == current:
            return None
        current = current.parent
    return None


def _follow_gitdir_pointer(pointer: Path) -> Path | None:
    """Follow a worktree ``.git`` file (``gitdir: <path>``) to the git dir
    that holds the remote config."""
    try:
        text = pointer.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    target = Path(text[len("gitdir:"):].strip())
    if not target.is_absolute():
        target = (pointer.parent / target).resolve()
    # Worktree git dirs live at <main>/.git/worktrees/<name>; the config
    # with the remotes is the main .git dir two levels up.
    if target.parent.name == "worktrees" and target.parent.parent.name == ".git":
        target = target.parent.parent
    return target if target.is_dir() else None


def _origin_url(config_path: Path) -> str | None:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        with config_path.open(encoding="utf-8", errors="replace") as fh:
            parser.read_file(fh)
    except (OSError, configparser.Error, UnicodeError):
        return None
    try:
        return parser.get('remote "origin"', "url", fallback=None)
    except configparser.Error:
        return None


def _slug_from_url(url: str) -> str | None:
    """Extract ``owner/repo`` from a git remote URL (host-agnostic)."""
    url = url.strip()
    if not url:
        return None
    if "://" in url:
        # scheme://[user@]host[:port]/path/to/owner/repo
        rest = url.split("://", 1)[1]
        if "/" not in rest:
            return None
        path = rest.split("/", 1)[1]
    elif ":" in url:
        # scp-style: [user@]host:owner/repo
        path = url.split(":", 1)[1]
    else:
        path = url
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[-2], parts[-1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"
