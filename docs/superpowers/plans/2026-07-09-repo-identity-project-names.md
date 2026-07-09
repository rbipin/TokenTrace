# Repo-Identity Project Names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Identify projects by git repo slug (`owner/repo`) instead of full cwd paths, with folder-name fallback, migrating existing identities in place.

**Architecture:** A new stdlib-only `src/repo_identity.py` resolves `cwd → owner/repo` by walking up to `.git` and parsing the origin remote from `config`. Collectors compute one `project_key` per session (slug, else folder name) and pass it as both display name and identity key to the unchanged `ProjectNameResolver`. `ProjectIdentityStore.__init__` gains a one-time idempotent migration that re-keys old full-path rows to the new key form, preserving guids/whimsical names and merging clones of the same repo.

**Tech Stack:** Python 3 stdlib only (`configparser`, `pathlib`, `functools.lru_cache`, `sqlite3`). Tests with pytest + `tmp_path`.

**Spec:** `docs/superpowers/specs/2026-07-09-repo-identity-project-names-design.md`

## Global Constraints

- Standard library only at runtime — no new dependencies.
- Collectors remain read-only with respect to their source files; the git-config lookup is a plain file read (no `git` subprocess).
- `resolve_repo_slug` must never raise — return `None` on any failure.
- `ProjectNameResolver.resolve(display_name, key)` signature is unchanged.
- Run tests with `python -m pytest <file> -q` from the repo root `C:\Repo\me\TokenTrace`.
- Commit after each task. Do NOT add a "Co-authored-by: Copilot" trailer.

---

### Task 1: `src/repo_identity.py` — cwd → owner/repo resolution

**Files:**
- Create: `src/repo_identity.py`
- Test: `tests/test_repo_identity.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `resolve_repo_slug(cwd: str | None) -> str | None` in `src.repo_identity`, plus (used internally by Task 4) `_slug_from_url(url: str) -> str | None`. Tasks 2–4 import `resolve_repo_slug` from `..repo_identity` / `.repo_identity`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_repo_identity.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_repo_identity.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.repo_identity'`

- [ ] **Step 3: Write the implementation**

Create `src/repo_identity.py`:

```python
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
    except OSError:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_repo_identity.py -q`
Expected: PASS (13 tests)

Note: tests share an `lru_cache` across a session, but every test uses a unique `tmp_path`, so cache collisions cannot occur.

- [ ] **Step 5: Commit**

```bash
git add src/repo_identity.py tests/test_repo_identity.py
git commit -m "feat: add repo_identity module resolving cwd to owner/repo slug"
```

---

### Task 2: Claude collector uses repo slug as project key

**Files:**
- Modify: `src/collectors/claude_cli.py` (imports + `_parse_session` project block, currently lines 102–104)
- Test: `tests/test_claude_cli_collector.py`

**Interfaces:**
- Consumes: `resolve_repo_slug(cwd)` from `src.repo_identity` (Task 1).
- Produces: `ClaudeCliCollector` now calls `resolver.resolve(project_key, project_key)` where `project_key` is the repo slug or the cwd folder name. No signature changes.

- [ ] **Step 1: Update the tests (failing first)**

In `tests/test_claude_cli_collector.py`, replace the assertion in `test_project_inputs_from_cwd` (the cwd `/home/user/my-app` does not exist on disk, so the slug lookup falls back to the folder name, which becomes both resolver arguments):

```python
def test_project_inputs_from_cwd(tmp_path):
    _write_session(tmp_path, "sess-proj", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5, cwd="/home/user/my-app"),
    ])
    stub = StubResolver()
    r = list(ClaudeCliCollector(tmp_path, resolver=stub).collect(date(2026, 7, 3)))[0]
    assert r.project == "RESOLVED"
    assert stub.calls[0] == ("my-app", "my-app")
```

And add a new test at the end of the file (a real git repo inside `tmp_path` so the slug resolves):

```python
def test_project_uses_repo_slug_when_cwd_is_git_repo(tmp_path):
    repo_dir = tmp_path / "checkout"
    git = repo_dir / ".git"
    git.mkdir(parents=True)
    (git / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/rbipin/TokenTrace.git\n',
        encoding="utf-8",
    )
    _write_session(tmp_path, "sess-git", [
        _asst("2026-07-03T10:00:00.000Z", "claude-sonnet-4-6", 10, 5,
              cwd=str(repo_dir)),
    ])
    stub = StubResolver()
    r = list(ClaudeCliCollector(tmp_path, resolver=stub).collect(date(2026, 7, 3)))[0]
    assert r.project == "RESOLVED"
    assert stub.calls[0] == ("rbipin/TokenTrace", "rbipin/TokenTrace")
```

Caution: `_write_session` writes JSONL files under `tmp_path` and the collector rglobs `*.jsonl` — the `.git` directory contains no `.jsonl` files, so it does not interfere.

- [ ] **Step 2: Run tests to verify the new/changed ones fail**

Run: `python -m pytest tests/test_claude_cli_collector.py -q`
Expected: FAIL — `test_project_inputs_from_cwd` (stub called with `("my-app", "/home/user/my-app")`) and `test_project_uses_repo_slug_when_cwd_is_git_repo` (stub called with folder name, not slug).

- [ ] **Step 3: Implement the collector change**

In `src/collectors/claude_cli.py` add the import (after the existing `from ..models import ...` line):

```python
from ..repo_identity import resolve_repo_slug
```

Replace the project block in `_parse_session`:

```python
        project: str | None = None
        if self._resolver is not None and cwd_seen:
            project_key = resolve_repo_slug(cwd_seen) or Path(cwd_seen).name or None
            project = self._resolver.resolve(project_key, project_key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_claude_cli_collector.py -q`
Expected: PASS. Note `test_end_to_end_with_real_resolver_yes_mode` still passes unchanged: cwd `/home/user/my-app` doesn't exist → folder-name fallback → `"my-app"`.

- [ ] **Step 5: Commit**

```bash
git add src/collectors/claude_cli.py tests/test_claude_cli_collector.py
git commit -m "feat: claude collector keys projects by repo slug with folder fallback"
```

---

### Task 3: Copilot collector uses full repo slug as project key

**Files:**
- Modify: `src/collectors/copilot_cli.py` (imports + project block in `collect`, currently lines 58–63)
- Test: `tests/test_cli_collector.py`

**Interfaces:**
- Consumes: `resolve_repo_slug(cwd)` from `src.repo_identity` (Task 1).
- Produces: `CopilotCliCollector` calls `resolver.resolve(project_key, project_key)` where `project_key` is the `repository` column verbatim (full `owner/repo`), else `resolve_repo_slug(cwd)`, else `Path(cwd).name`.

- [ ] **Step 1: Update the tests (failing first)**

In `tests/test_cli_collector.py`, change the assertions of the three project-input tests:

```python
def test_project_inputs_prefer_repository(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/myrepo",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    stub = StubResolver()
    r = list(CopilotCliCollector(home, resolver=stub).collect(date(2026, 6, 10)))[0]
    assert r.project == "RESOLVED"
    assert stub.calls == [("owner/myrepo", "owner/myrepo")]


def test_project_inputs_fall_back_to_cwd_basename(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/localproject", "",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    stub = StubResolver()
    list(CopilotCliCollector(home, resolver=stub).collect(date(2026, 6, 10)))
    assert stub.calls == [("localproject", "localproject")]


def test_end_to_end_with_real_resolver_yes_mode(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/myrepo",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    resolver = ProjectNameResolver("yes")
    r = list(CopilotCliCollector(home, resolver=resolver).collect(date(2026, 6, 10)))[0]
    assert r.project == "owner/myrepo"
```

And add one new test for the slug-from-disk fallback when the repository column is empty:

```python
def test_project_slug_resolved_from_cwd_git_config(tmp_path):
    home = _make_home(tmp_path)
    repo_dir = tmp_path / "checkout"
    git = repo_dir / ".git"
    git.mkdir(parents=True)
    (git / "config").write_text(
        '[remote "origin"]\n\turl = git@github.com:acme/widgets.git\n',
        encoding="utf-8",
    )
    _add_session(home, "s1", str(repo_dir), "",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    stub = StubResolver()
    list(CopilotCliCollector(home, resolver=stub).collect(date(2026, 6, 10)))
    assert stub.calls == [("acme/widgets", "acme/widgets")]
```

- [ ] **Step 2: Run tests to verify the new/changed ones fail**

Run: `python -m pytest tests/test_cli_collector.py -q`
Expected: FAIL on the four tests above (old code passes `("myrepo", "/work/x")`-style tuples and displays `"myrepo"` in yes mode).

- [ ] **Step 3: Implement the collector change**

In `src/collectors/copilot_cli.py` add the import (after `from ..models import ...`):

```python
from ..repo_identity import resolve_repo_slug
```

Replace the project block in `collect`:

```python
            project: str | None = None
            if self._resolver is not None:
                repo: str = row["repository"] or ""
                cwd: str = row["cwd"] or ""
                project_key = (
                    repo
                    or resolve_repo_slug(cwd)
                    or (Path(cwd).name if cwd else None)
                    or None
                )
                project = self._resolver.resolve(project_key, project_key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_collector.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/collectors/copilot_cli.py tests/test_cli_collector.py
git commit -m "feat: copilot collector keys projects by full repo slug"
```

---

### Task 4: Identity store migration from path keys to project keys

**Files:**
- Modify: `src/project_identity.py` (module docstring, new helpers, `ProjectIdentityStore.__init__`)
- Modify: `tests/test_project_identity.py` (re-key existing tests to slug keys, add migration tests)
- Modify: `tests/test_tracker_cli.py:118` (path key → slug key)

**Interfaces:**
- Consumes: `resolve_repo_slug` from `src.repo_identity` (Task 1).
- Produces: `ProjectIdentityStore` public API unchanged (`resolve_guid`, `resolve_whimsical`, `list_identities`, `close`). New private helpers `_looks_like_path(key: str) -> bool`, `_folder_name(path_str: str) -> str`, and method `_migrate_path_keys(conn)` run once per `__init__`.

**Background for the implementer:** the `cwd_key` column used to hold full normalized paths (e.g. `c:\repo\me\tokentrace`). After Tasks 2–3, collectors pass repo slugs (`owner/repo`) or bare folder names. This task re-keys old rows in place so existing guids/whimsical names survive, and merges duplicates when two old paths were clones of the same repo.

- [ ] **Step 1: Update existing tests to the new key form and add migration tests (failing first)**

In `tests/test_project_identity.py`, replace every path-style key with a slug/folder key. New keys never look like paths, so the migration ignores them:

- `"/work/myproj"` → `"acme/myproj"` (all occurrences, including the reopen and concurrency tests)
- `"C:/Work/MyProj"` → `"Acme/MyProj"` and `"c:/work/myproj"` → `"acme/myproj"` (case-insensitivity test)
- `"  /work/myproj  "` → `"  acme/myproj  "`
- `"/work/a"` → `"acme/a"`, `"/work/b"` → `"acme/b"`
- `"/Work/MyProj"` → `"Acme/MyProj"`
- `"/work/x"` → `"acme/x"`, `"/work/shared"` → `"acme/shared"`
- `"/work/beta"` → `"acme/beta"`, `"/work/alpha"` → `"acme/alpha"` (and the sorted-list assertion to `["acme/alpha", "acme/beta"]`)

In `tests/test_tracker_cli.py` line 118, change `store.resolve_whimsical("C:/Work/MyProj")` to `store.resolve_whimsical("Acme/MyProj")` (keep the rest of that test intact; if it asserts the printed cwd key, update the expected string to `acme/myproj`).

Then append migration tests to `tests/test_project_identity.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify the migration tests fail**

Run: `python -m pytest tests/test_project_identity.py tests/test_tracker_cli.py -q`
Expected: the five new `test_migration_*` / trailing-separator tests FAIL (path keys are left untouched today); the re-keyed existing tests PASS.

- [ ] **Step 3: Implement the migration**

In `src/project_identity.py`:

1. Add imports at the top (after `import uuid`):

```python
import re
```

and after the existing `from .whimsy import generate_name`:

```python
from .repo_identity import resolve_repo_slug
```

2. Update the module docstring first paragraph to:

```python
"""Local-only project identity mapping: project key -> guid -> whimsical name.

Project keys are repo slugs (``owner/repo``) or cwd folder names — full
paths are no longer stored. Legacy full-path keys are migrated in place on
startup (guids and whimsical names preserved; clones of the same repo are
merged, keeping the oldest row).

The ``project_identities`` table lives in the same SQLite file as the
session store but is intentionally invisible to the sync machinery — it is
never pushed to remote stores.
"""
```

3. Add module-level helpers after `_normalize`:

```python
_PATH_SEGMENT_RE = re.compile(r"[\\/]+")


def _looks_like_path(key: str) -> bool:
    """True for legacy full-path keys; False for slug or folder-name keys.

    Slug keys contain exactly one interior ``/`` (``owner/repo``); folder
    names contain no separators. Paths contain ``\\``, ``:``, a leading
    ``/``, or more than one ``/``.
    """
    return (
        "\\" in key
        or ":" in key
        or key.startswith("/")
        or key.count("/") > 1
    )


def _folder_name(path_str: str) -> str:
    segments = [s for s in _PATH_SEGMENT_RE.split(path_str) if s]
    return segments[-1] if segments else path_str


def _migrated_key(old_key: str) -> str:
    slug = resolve_repo_slug(old_key)
    if slug:
        return _normalize(slug)
    return _normalize(_folder_name(old_key))
```

Note: `old_key` is a casefolded path; on case-insensitive filesystems (Windows/macOS) `resolve_repo_slug` still finds the repo, on Linux it usually falls back to the folder name — acceptable per spec.

4. Change `ProjectIdentityStore.__init__` to run the migration after table creation:

```python
    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        with closing(self._connect()) as conn, conn:
            conn.execute(_CREATE_IDENTITIES)
            try:
                self._migrate_path_keys(conn)
            except Exception as exc:
                print(
                    f"Warning [project-identity]: key migration failed: {exc}; "
                    "existing identities left unchanged",
                    file=sys.stderr,
                )
```

5. Add the method to `ProjectIdentityStore`:

```python
    @staticmethod
    def _migrate_path_keys(conn: sqlite3.Connection) -> None:
        """One-time re-key of legacy full-path rows to project keys."""
        rows = conn.execute(
            "SELECT cwd_key, created_at FROM project_identities"
        ).fetchall()
        for old_key, created_at in rows:
            if not _looks_like_path(old_key):
                continue
            new_key = _migrated_key(old_key)
            existing = conn.execute(
                "SELECT created_at FROM project_identities WHERE cwd_key = ?",
                (new_key,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "UPDATE project_identities SET cwd_key = ? WHERE cwd_key = ?",
                    (new_key, old_key),
                )
            elif existing[0] <= created_at:
                # A row already owns the new key and is older — drop this one.
                conn.execute(
                    "DELETE FROM project_identities WHERE cwd_key = ?", (old_key,)
                )
            else:
                # This row is older — it wins the key.
                conn.execute(
                    "DELETE FROM project_identities WHERE cwd_key = ?", (new_key,)
                )
                conn.execute(
                    "UPDATE project_identities SET cwd_key = ? WHERE cwd_key = ?",
                    (new_key, old_key),
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_project_identity.py tests/test_tracker_cli.py tests/test_project_resolver.py tests/test_sync_excludes_identities.py -q`
Expected: PASS. (`test_project_resolver.py` and `test_sync_excludes_identities.py` use path-style keys within a single store instance — migration only runs at init, before those keys exist, so they are unaffected.)

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS. If any other test fails because it seeds path-style keys and reopens the store, change its keys to slug form (`owner/name`) following the same substitutions as Step 1.

- [ ] **Step 6: Commit**

```bash
git add src/project_identity.py tests/test_project_identity.py tests/test_tracker_cli.py
git commit -m "feat: migrate project identities from path keys to repo-slug keys"
```

---

### Task 5: Documentation update

**Files:**
- Modify: `CLAUDE.md` (architecture table + project-identity notes)

**Interfaces:**
- Consumes: final behavior of Tasks 1–4.
- Produces: docs only; no code.

- [ ] **Step 1: Update CLAUDE.md**

In the architecture tree, add below the `project_identity.py` line:

```
  repo_identity.py       resolve_repo_slug(cwd): walks up to .git, parses origin remote from config -> owner/repo (read-only, cached, never raises)
```

Update the `project_identity.py` line to:

```
  project_identity.py    ProjectIdentityStore (local-only project-key→guid→whimsical table; keys are repo slugs or folder names, legacy path keys migrated on init) + ProjectNameResolver (tri-state naming policy)
```

In the **Config file** section, extend the `track_project_names` sentence: after the enum description, add:

```
Project identity is keyed by git repo slug (`owner/repo` — from the Copilot session `repository` column, or by reading `<cwd>/.git/config` origin remote for Claude sessions), falling back to the cwd folder name; full paths are never stored. Two clones of the same repo therefore map to one project. `yes` mode displays the full slug (e.g. `rbipin/TokenTrace`).
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: describe repo-slug project identity in CLAUDE.md"
```
