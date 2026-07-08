# Project Name Masking (tri-state `track_project_names`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `track_project_names` into a tri-state string setting (`"yes"` = real name, `"no"` = stable guid, `"whimsical"` = docker-style masked name), backed by a local-only cwd→guid→name identity table.

**Architecture:** A standalone `src/whimsy/` package generates docker-style names (extractable to its own repo later). A `ProjectIdentityStore` persists cwd→guid→whimsical mappings in a new `project_identities` table inside `usage.db` (never synced). A shared `ProjectNameResolver` owns the tri-state naming policy; both collectors receive it via constructor injection and only supply source-specific `display_name`/`cwd` inputs.

**Tech Stack:** Python 3 stdlib only (sqlite3, uuid, random). pytest for tests.

**Spec:** `docs/superpowers/specs/2026-07-08-project-name-masking-design.md`

## Global Constraints

- `track_project_names` accepts exactly `"yes" | "no" | "whimsical"`; default `"no"`. Booleans are **not** accepted (breaking change, no migration).
- `src/whimsy/` must import **nothing** from the rest of `src/` — stdlib only. Its tests (`tests/whimsy/`) import only `src.whimsy`.
- `project_identities` table must never appear in any sync path (`unsynced_for` / `mark_synced` / remote upserts).
- cwd identity keys are case-insensitive (strip + casefold); same cwd → same guid → same whimsical name, forever.
- Collectors stay read-only with respect to their source files.
- No changes to the `sessions` table schema.
- Run tests with `python -m pytest <file> -q` from the repo root.

---

### Task 1: Standalone whimsy package — word lists

**Files:**
- Create: `src/whimsy/__init__.py`
- Create: `src/whimsy/wordlists.py`
- Create: `src/whimsy/LICENSE-NOTICE.md`
- Create: `tests/whimsy/__init__.py` (empty)
- Test: `tests/whimsy/test_wordlists.py`

**Interfaces:**
- Produces: `src.whimsy.wordlists.ADJECTIVES: tuple[str, ...]` and `src.whimsy.wordlists.SURNAMES: tuple[str, ...]` (used by Task 2's generator).

- [ ] **Step 1: Write the failing test**

Create `tests/whimsy/__init__.py` (empty file) and `tests/whimsy/test_wordlists.py`:

```python
"""Tests for the whimsy word lists. Imports only from src.whimsy (standalone component)."""
from src.whimsy.wordlists import ADJECTIVES, SURNAMES


def test_adjectives_nonempty_and_lowercase():
    assert len(ADJECTIVES) >= 100
    assert all(a == a.lower() and a.isascii() for a in ADJECTIVES)


def test_surnames_nonempty_and_no_duplicates():
    assert len(SURNAMES) >= 200
    assert len(set(SURNAMES)) == len(SURNAMES)


def test_supplementary_surnames_present():
    for name in ("bose_jc", "sarabhai", "hassabis", "doudna", "godel",
                 "hinton", "kariko", "edvard_moser"):
        assert name in SURNAMES


def test_duplicate_supplements_not_doubled():
    # These appear in both Docker's list and new-names.txt — must appear exactly once.
    for name in ("ramanujan", "visvesvaraya", "bhabha", "feynman",
                 "torvalds", "bardeen", "burnell", "moser"):
        assert SURNAMES.count(name) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/whimsy/test_wordlists.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.whimsy'`

- [ ] **Step 3: Port the word lists**

Fetch Docker's Apache-2.0-licensed name generator source:

```bash
curl -s https://raw.githubusercontent.com/docker-archive/docker-ce/master/components/engine/pkg/namesgenerator/names-generator.go -o /tmp/names-generator.go
```

Create `src/whimsy/wordlists.py`. Port the Go `left` array to an `ADJECTIVES` tuple and the Go `right` array to a `SURNAMES` tuple. **Preserve every per-entry comment verbatim**, converting `// comment` → `# comment`. File header:

```python
"""Word lists for whimsical name generation.

Adapted from Docker's names-generator.go
(https://github.com/docker-archive/docker-ce/blob/master/components/engine/pkg/namesgenerator/names-generator.go),
licensed under the Apache License 2.0. See LICENSE-NOTICE.md.
Supplementary surnames appended from docs/superpowers/specs/new-names.txt.
"""

ADJECTIVES: tuple[str, ...] = (
    "admiring",
    "adoring",
    # ... every entry from Docker's `left` array, in order ...
    "zen",
)

SURNAMES: tuple[str, ...] = (
    # Maria Gaetana Agnesi - Italian mathematician, philosopher, theologian and humanitarian. ... https://en.wikipedia.org/wiki/Maria_Gaetana_Agnesi
    "agnesi",
    # ... every entry from Docker's `right` array with its comment, in order ...
)
```

Then append every entry from `docs/superpowers/specs/new-names.txt` to the end of `SURNAMES`, keeping each entry's bio + Wikipedia comment (converted to `#` style), **except** these 8 duplicates already in Docker's list, which must be skipped: `ramanujan`, `visvesvaraya`, `bhabha`, `feynman`, `torvalds`, `bardeen`, `burnell`, `moser`.

Create `src/whimsy/__init__.py` (generator export lands in Task 2; for now):

```python
"""Whimsy — standalone docker-style whimsical name generator.

Self-contained: imports only the Python standard library. Designed to be
extractable into its own repository by copying this directory.
"""
```

Create `src/whimsy/LICENSE-NOTICE.md`:

```markdown
# License Notice

The word lists in `wordlists.py` are adapted from Docker's
[`names-generator.go`](https://github.com/docker-archive/docker-ce/blob/master/components/engine/pkg/namesgenerator/names-generator.go),
Copyright 2013-2018 Docker, Inc., licensed under the Apache License, Version 2.0
(http://www.apache.org/licenses/LICENSE-2.0).

Modifications: converted to Python, and supplemented with additional notable
scientists and mathematicians.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/whimsy/test_wordlists.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Verify standalone constraint**

Run: `python -c "import ast,sys; tree=ast.parse(open('src/whimsy/wordlists.py').read()); assert not [n for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom))], 'wordlists must not import anything'; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add src/whimsy tests/whimsy
git commit -m "feat(whimsy): add standalone word lists ported from Docker namesgenerator"
```

---

### Task 2: Whimsy generator + package README

**Files:**
- Create: `src/whimsy/generator.py`
- Modify: `src/whimsy/__init__.py`
- Create: `src/whimsy/README.md`
- Test: `tests/whimsy/test_generator.py`

**Interfaces:**
- Consumes: `ADJECTIVES`, `SURNAMES` from Task 1.
- Produces: `src.whimsy.generate_name(existing: set[str], rng: random.Random | None = None) -> str` — the package's only public API; Task 4 (`ProjectIdentityStore`) calls it.

- [ ] **Step 1: Write the failing test**

Create `tests/whimsy/test_generator.py`:

```python
"""Tests for the whimsy name generator. Imports only from src.whimsy."""
import random
import re

from src.whimsy import generate_name
from src.whimsy.wordlists import ADJECTIVES, SURNAMES

NAME_RE = re.compile(r"^[a-z]+_[a-z_]+\d*$")


def test_generates_adjective_surname_format():
    name = generate_name(set(), rng=random.Random(42))
    adjective, _, surname = name.partition("_")
    assert adjective in ADJECTIVES
    assert surname in SURNAMES
    assert NAME_RE.match(name)


def test_deterministic_with_seeded_rng():
    assert generate_name(set(), rng=random.Random(7)) == generate_name(set(), rng=random.Random(7))


def test_avoids_existing_names():
    rng = random.Random(1)
    existing = {generate_name(set(), rng=random.Random(1))}
    name = generate_name(existing, rng=rng)
    assert name not in existing


def test_numeric_suffix_when_pool_exhausted():
    # Simulate exhaustion: every base combo is taken.
    existing = {f"{a}_{s}" for a in ADJECTIVES for s in SURNAMES}
    name = generate_name(existing, rng=random.Random(3))
    assert name not in existing
    assert name[-1].isdigit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/whimsy/test_generator.py -q`
Expected: FAIL with `ImportError: cannot import name 'generate_name'`

- [ ] **Step 3: Write the implementation**

Create `src/whimsy/generator.py`:

```python
"""Docker-style whimsical name generation with collision avoidance."""
from __future__ import annotations

import random

from .wordlists import ADJECTIVES, SURNAMES

_MAX_ATTEMPTS = 20


def generate_name(existing: set[str], rng: random.Random | None = None) -> str:
    """Return a unique ``adjective_surname`` name not present in *existing*.

    Retries up to ``_MAX_ATTEMPTS`` random combos; if all collide, appends an
    incrementing numeric suffix (Docker's fallback behavior) until unique.

    Args:
        existing: Names already taken; the result is guaranteed not to be in it.
        rng: Optional random source for deterministic tests. Defaults to the
            module-level ``random`` generator.
    """
    rng = rng if rng is not None else random.Random()
    name = f"{rng.choice(ADJECTIVES)}_{rng.choice(SURNAMES)}"
    for _ in range(_MAX_ATTEMPTS):
        if name not in existing:
            return name
        name = f"{rng.choice(ADJECTIVES)}_{rng.choice(SURNAMES)}"
    suffix = 2
    while f"{name}{suffix}" in existing:
        suffix += 1
    return f"{name}{suffix}"
```

Replace `src/whimsy/__init__.py` content with:

```python
"""Whimsy — standalone docker-style whimsical name generator.

Self-contained: imports only the Python standard library. Designed to be
extractable into its own repository by copying this directory.

Public API: :func:`generate_name`.
"""
from .generator import generate_name

__all__ = ["generate_name"]
```

Create `src/whimsy/README.md`:

```markdown
# Whimsy

A standalone docker-style whimsical name generator (`admiring_agnesi`,
`clever_ramanujan`, ...). Zero dependencies beyond the Python standard library.

## Usage

​```python
from whimsy import generate_name  # or `from src.whimsy import generate_name` in-tree

name = generate_name(existing={"admiring_agnesi"})
​```

`generate_name(existing, rng=None)` returns an `adjective_surname` combination
guaranteed not to be in `existing`. After 20 colliding attempts it falls back
to appending a numeric suffix (like Docker's container namer).

## License

Word lists adapted from Docker's Apache-2.0-licensed `names-generator.go`;
see `LICENSE-NOTICE.md`.
```

(Remove the zero-width markers `​` around the fenced block when writing the file — use a normal triple-backtick fence.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/whimsy -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/whimsy tests/whimsy
git commit -m "feat(whimsy): add generate_name with collision retry and suffix fallback"
```

---

### Task 3: ProjectIdentityStore

**Files:**
- Create: `src/project_identity.py`
- Test: `tests/test_project_identity.py`

**Interfaces:**
- Consumes: `src.whimsy.generate_name(existing: set[str], rng=None) -> str`.
- Produces: `ProjectIdentityStore(db_path: Path | str)` with `resolve_guid(cwd: str | None) -> str | None`, `resolve_whimsical(cwd: str | None) -> str | None`, `close() -> None`. Task 4's resolver depends on these exact signatures.

- [ ] **Step 1: Write the failing test**

Create `tests/test_project_identity.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_project_identity.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.project_identity'`

- [ ] **Step 3: Write the implementation**

Create `src/project_identity.py`:

```python
"""Local-only project identity mapping: cwd -> guid -> whimsical name.

The ``project_identities`` table lives in the same SQLite file as the
session store but is intentionally invisible to the sync machinery — it is
never pushed to remote stores.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from .whimsy import generate_name

_CREATE_IDENTITIES = """
CREATE TABLE IF NOT EXISTS project_identities (
    cwd_key         TEXT PRIMARY KEY,
    guid            TEXT NOT NULL UNIQUE,
    whimsical_name  TEXT UNIQUE,
    created_at      TEXT NOT NULL
)
"""

_GUID_LENGTH = 12


def _normalize(cwd: str) -> str:
    """Case-insensitive identity key for a working directory."""
    return cwd.strip().casefold()


class ProjectIdentityStore:
    """Persists stable per-project identities keyed by normalized cwd."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        with closing(self._connect()) as conn, conn:
            conn.execute(_CREATE_IDENTITIES)

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self._db_path)

    def resolve_guid(self, cwd: str | None) -> str | None:
        """Return the stable guid for *cwd*, creating one on first sight.

        Returns None when cwd is empty/unavailable — no identity is fabricated.
        """
        if not cwd or not cwd.strip():
            return None
        key = _normalize(cwd)
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT guid FROM project_identities WHERE cwd_key = ?", (key,)
            ).fetchone()
            if row is not None:
                return row[0]
            guid = uuid.uuid4().hex[:_GUID_LENGTH]
            conn.execute(
                "INSERT INTO project_identities (cwd_key, guid, created_at) "
                "VALUES (?, ?, datetime('now'))",
                (key, guid),
            )
            return guid

    def resolve_whimsical(self, cwd: str | None) -> str | None:
        """Return the stable whimsical name for *cwd*, creating one on first sight."""
        guid = self.resolve_guid(cwd)
        if guid is None:
            return None
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT whimsical_name FROM project_identities WHERE guid = ?",
                (guid,),
            ).fetchone()
            if row is not None and row[0]:
                return row[0]
            taken = {
                r[0]
                for r in conn.execute(
                    "SELECT whimsical_name FROM project_identities "
                    "WHERE whimsical_name IS NOT NULL"
                )
            }
            name = generate_name(taken)
            conn.execute(
                "UPDATE project_identities SET whimsical_name = ? WHERE guid = ?",
                (name, guid),
            )
            return name

    def close(self) -> None:
        """Connections are per-call context managers; nothing to release."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_project_identity.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add src/project_identity.py tests/test_project_identity.py
git commit -m "feat: add ProjectIdentityStore for stable cwd->guid->whimsical mapping"
```

---

### Task 4: ProjectNameResolver (shared naming policy)

**Files:**
- Modify: `src/project_identity.py` (append the resolver class)
- Test: `tests/test_project_resolver.py`

**Interfaces:**
- Consumes: `ProjectIdentityStore.resolve_guid` / `.resolve_whimsical` from Task 3.
- Produces: `ProjectNameResolver(mode: str, identity_store: ProjectIdentityStore | None = None)` with `resolve(display_name: str | None, cwd: str | None) -> str | None` and module constant `PROJECT_NAME_MODES = ("yes", "no", "whimsical")`. Tasks 5–7 depend on these exact names.

- [ ] **Step 1: Write the failing test**

Create `tests/test_project_resolver.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_project_resolver.py -q`
Expected: FAIL with `ImportError: cannot import name 'PROJECT_NAME_MODES'`

- [ ] **Step 3: Write the implementation**

Append to `src/project_identity.py` (also add `import sys` to the imports at the top of the file):

```python
PROJECT_NAME_MODES = ("yes", "no", "whimsical")


class ProjectNameResolver:
    """Owns the tri-state project naming policy shared by all collectors.

    Collectors supply source-specific raw inputs (display name, cwd); the
    resolver decides what — if anything — goes into ``SessionRecord.project``.
    """

    def __init__(
        self, mode: str, identity_store: ProjectIdentityStore | None = None
    ) -> None:
        if mode not in PROJECT_NAME_MODES:
            raise ValueError(
                f"invalid project name mode {mode!r}; "
                f"expected one of {', '.join(PROJECT_NAME_MODES)}"
            )
        if mode in ("no", "whimsical") and identity_store is None:
            raise ValueError(f"mode {mode!r} requires an identity_store")
        self._mode = mode
        self._identity_store = identity_store
        self._warned = False

    def resolve(self, display_name: str | None, cwd: str | None) -> str | None:
        """Resolve the project value for one session record."""
        if self._mode == "yes":
            return display_name
        try:
            if self._mode == "no":
                return self._identity_store.resolve_guid(cwd)
            return self._identity_store.resolve_whimsical(cwd)
        except Exception as exc:
            if not self._warned:
                print(
                    f"Warning [project-identity]: {exc}; "
                    "project names will be omitted",
                    file=sys.stderr,
                )
                self._warned = True
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_project_resolver.py tests/test_project_identity.py -q`
Expected: PASS (20 tests)

- [ ] **Step 5: Commit**

```bash
git add src/project_identity.py tests/test_project_resolver.py
git commit -m "feat: add ProjectNameResolver owning the tri-state naming policy"
```

---

### Task 5: Config tri-state

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_config.py` (rewrite the `track_project_names` tests)

**Interfaces:**
- Consumes: `PROJECT_NAME_MODES` from Task 4.
- Produces: `Config.track_project_names: str` (default `"no"`); `Config.load()` warns + falls back to `"no"` on invalid TOML values (including old booleans). Task 7 (tracker wiring) relies on this.

- [ ] **Step 1: Update the tests**

In `tests/test_config.py`, replace the five `track_project_names` tests (`test_default_track_project_names_is_false`, `test_load_returns_defaults_when_no_toml`, `test_load_reads_toml`, `test_load_override_wins_over_toml`, and the assertion inside `test_load_invalid_toml_falls_back_to_defaults`) with:

```python
def test_default_track_project_names_is_no():
    cfg = Config()
    assert cfg.track_project_names == "no"


def test_load_returns_defaults_when_no_toml(tmp_path, monkeypatch):
    monkeypatch.setattr("src.config._TOML_PATH", tmp_path / "no_such.toml")
    cfg = Config.load()
    assert cfg.track_project_names == "no"


def test_load_reads_toml(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text('[tracking]\ntrack_project_names = "whimsical"\n')
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.track_project_names == "whimsical"


def test_load_override_wins_over_toml(tmp_path, monkeypatch):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text('[tracking]\ntrack_project_names = "yes"\n')
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load(track_project_names="no")
    assert cfg.track_project_names == "no"


def test_load_rejects_old_boolean_value(tmp_path, monkeypatch, capsys):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text("[tracking]\ntrack_project_names = true\n")
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.track_project_names == "no"
    assert "Warning" in capsys.readouterr().err


def test_load_rejects_unknown_string_value(tmp_path, monkeypatch, capsys):
    toml = tmp_path / ".tokentracer.toml"
    toml.write_text('[tracking]\ntrack_project_names = "maybe"\n')
    monkeypatch.setattr("src.config._TOML_PATH", toml)
    cfg = Config.load()
    assert cfg.track_project_names == "no"
    assert "Warning" in capsys.readouterr().err
```

In `test_load_invalid_toml_falls_back_to_defaults`, change `assert cfg.track_project_names is False` to `assert cfg.track_project_names == "no"`. In `test_write_toml_setting_creates_file` and `test_write_toml_setting_updates_existing`, change `write_toml_setting("track_project_names", True)` to `write_toml_setting("track_project_names", "whimsical")` and the assertions to `assert 'track_project_names = "whimsical"' in content` (also update the pre-seeded file line in the second test to `'[tracking]\ntrack_project_names = "no"\n'`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py -q`
Expected: FAIL (defaults are still `False`, TOML parsing is still boolean)

- [ ] **Step 3: Update `src/config.py`**

Change the `Config` field:

```python
    track_project_names: str = "no"  # "yes" | "no" | "whimsical"
```

In `Config.load()`, replace the `track_project_names` TOML handling with validation (import the modes tuple at top of file: `from .project_identity import PROJECT_NAME_MODES` — note: `project_identity` must not import `config`, and it doesn't):

```python
                if "track_project_names" in tracking:
                    raw_mode = tracking["track_project_names"]
                    if isinstance(raw_mode, str) and raw_mode in PROJECT_NAME_MODES:
                        base["track_project_names"] = raw_mode
                    else:
                        print(
                            f"Warning: invalid track_project_names {raw_mode!r} "
                            f"in ~/.tokentracer.toml; expected one of "
                            f"{', '.join(PROJECT_NAME_MODES)} — using 'no'",
                            file=sys.stderr,
                        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py tests/test_config_stores.py tests/test_context.py -q`
Expected: `tests/test_config.py` PASSES. `test_config_stores.py`/`test_context.py` may fail on lines writing `track_project_names = false`/`true` fixtures — update those fixture strings to `track_project_names = "no"` / `"yes"` respectively, then re-run.
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py tests/test_config_stores.py tests/test_context.py
git commit -m "feat!: track_project_names becomes tri-state string (yes/no/whimsical)"
```

---

### Task 6: Collector integration (resolver injection)

**Files:**
- Modify: `src/collectors/copilot_cli.py`
- Modify: `src/collectors/claude_cli.py`
- Test: `tests/test_cli_collector.py`, `tests/test_claude_cli_collector.py`

**Interfaces:**
- Consumes: `ProjectNameResolver.resolve(display_name, cwd) -> str | None` from Task 4.
- Produces: `CopilotCliCollector(copilot_home: Path, resolver: ProjectNameResolver | None = None)` and `ClaudeCliCollector(projects_dir: Path, resolver: ProjectNameResolver | None = None)`. `resolver=None` means project stays `None`. Task 7 wires these.

- [ ] **Step 1: Update collector tests**

In `tests/test_cli_collector.py`, add the import and a stub at the top:

```python
from src.project_identity import ProjectNameResolver


class StubResolver:
    """Records inputs; returns a canned value."""

    def __init__(self, result="RESOLVED"):
        self.result = result
        self.calls: list[tuple[str | None, str | None]] = []

    def resolve(self, display_name, cwd):
        self.calls.append((display_name, cwd))
        return self.result
```

Replace the three project tests:

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
    assert stub.calls == [("myrepo", "/work/x")]


def test_project_inputs_fall_back_to_cwd_basename(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/localproject", "",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    stub = StubResolver()
    list(CopilotCliCollector(home, resolver=stub).collect(date(2026, 6, 10)))
    assert stub.calls == [("localproject", "/work/localproject")]


def test_project_none_without_resolver(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/secret", "owner/secret",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    r = list(CopilotCliCollector(home).collect(date(2026, 6, 10)))[0]
    assert r.project is None


def test_end_to_end_with_real_resolver_yes_mode(tmp_path):
    home = _make_home(tmp_path)
    _add_session(home, "s1", "/work/x", "owner/myrepo",
                 "2026-06-10T12:00:00.000Z", "2026-06-10T12:30:00.000Z")
    _write_events(home, "s1", [
        _shutdown({"claude-sonnet-4-6": {"turns": 1, "input": 100, "output": 20}}),
    ])
    resolver = ProjectNameResolver("yes")
    r = list(CopilotCliCollector(home, resolver=resolver).collect(date(2026, 6, 10)))[0]
    assert r.project == "myrepo"
```

In `tests/test_claude_cli_collector.py`, apply the same pattern to its two project tests (the fixture JSONL entries contain a `cwd` field — check lines ~85-100 for the existing `_track_projects` tests):

```python
class StubResolver:
    def __init__(self, result="RESOLVED"):
        self.result = result
        self.calls: list[tuple[str | None, str | None]] = []

    def resolve(self, display_name, cwd):
        self.calls.append((display_name, cwd))
        return self.result


def test_project_inputs_from_cwd(tmp_path):
    # reuse the existing fixture-writing helper from the current
    # `track_project_names=True` test; it writes entries with cwd "/work/myproj"
    ...  # same fixture setup as the existing test at line ~92
    stub = StubResolver()
    r = list(ClaudeCliCollector(tmp_path, resolver=stub).collect(date(2026, 7, 3)))[0]
    assert r.project == "RESOLVED"
    assert stub.calls[0] == ("myproj", "/work/myproj")


def test_project_none_without_resolver(tmp_path):
    ...  # same fixture setup as the existing test at line ~100
    r = list(ClaudeCliCollector(tmp_path).collect(date(2026, 7, 3)))[0]
    assert r.project is None
```

(The `...` lines above mean: copy the fixture-creation lines verbatim from the two existing tests being replaced — they build a JSONL file under `tmp_path`; keep them unchanged. Match the actual cwd value used by the existing fixture — read the current test before editing and adjust the expected `(display_name, cwd)` tuple to the fixture's real cwd path.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli_collector.py tests/test_claude_cli_collector.py -q`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'resolver'`

- [ ] **Step 3: Update the collectors**

`src/collectors/copilot_cli.py` — replace the constructor and project-resolution block:

```python
    def __init__(self, copilot_home: Path, resolver=None) -> None:
        """Args:
            copilot_home: Root of the ~/.copilot data directory.
            resolver: Optional ProjectNameResolver; when None, records carry
                no project identity.
        """
        self._home = copilot_home
        self._resolver = resolver
```

and in `collect()`, replace the `if self._track_projects:` block with:

```python
            project: str | None = None
            if self._resolver is not None:
                repo: str = row["repository"] or ""
                cwd: str = row["cwd"] or ""
                display_name = repo.split("/")[-1] if repo else (Path(cwd).name or None)
                project = self._resolver.resolve(display_name, cwd or None)
```

`src/collectors/claude_cli.py` — replace the constructor:

```python
    def __init__(self, projects_dir: Path, resolver=None) -> None:
        """Args:
            projects_dir: Root of ~/.claude/projects.
            resolver: Optional ProjectNameResolver; when None, records carry
                no project identity.
        """
        self._dir = projects_dir
        self._resolver = resolver
```

In `_parse_session`, rename the loop-local variable `project` to `cwd_seen: str | None = None`, and replace the per-line block:

```python
            if cwd_seen is None and self._resolver is not None:
                cwd = entry.get("cwd")
                if cwd:
                    cwd_seen = cwd
```

then just before constructing the returned `SessionRecord`:

```python
        project: str | None = None
        if self._resolver is not None and cwd_seen:
            project = self._resolver.resolve(Path(cwd_seen).name or None, cwd_seen)
```

(Do not add a type annotation import for the resolver in either collector — accept it duck-typed to avoid a hard dependency direction from collectors to project_identity; the docstring documents the expected type.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_collector.py tests/test_claude_cli_collector.py -q`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/collectors tests/test_cli_collector.py tests/test_claude_cli_collector.py
git commit -m "feat!: collectors take a ProjectNameResolver instead of track_project_names"
```

---

### Task 7: tracker.py wiring (`--project-mode`, config set, pipeline)

**Files:**
- Modify: `tracker.py`
- Test: `tests/test_tracker_cli.py` (create)

**Interfaces:**
- Consumes: `PROJECT_NAME_MODES`, `ProjectIdentityStore`, `ProjectNameResolver` (Tasks 3–4), collector `resolver=` kwarg (Task 6), `Config.track_project_names: str` (Task 5).
- Produces: `collect --project-mode {yes,no,whimsical}` flag; `config set track_project_names <yes|no|whimsical>` validation.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tracker_cli.py`:

```python
from __future__ import annotations

import pytest

import tracker


def test_parser_accepts_project_mode():
    parser, _ = tracker._build_parser()
    args = parser.parse_args(["collect", "--project-mode", "whimsical"])
    assert args.project_mode == "whimsical"


def test_parser_rejects_invalid_project_mode():
    parser, _ = tracker._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["collect", "--project-mode", "true"])


def test_parser_has_no_track_projects_flags():
    parser, _ = tracker._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["collect", "--track-projects"])


def test_config_set_accepts_valid_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("src.config._TOML_PATH", tmp_path / ".tokentracer.toml")
    parser, _ = tracker._build_parser()
    args = parser.parse_args(["config", "set", "track_project_names", "whimsical"])
    assert tracker.cmd_config_set(args) == 0
    assert 'track_project_names = "whimsical"' in (tmp_path / ".tokentracer.toml").read_text()


def test_config_set_rejects_boolean_value(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("src.config._TOML_PATH", tmp_path / ".tokentracer.toml")
    parser, _ = tracker._build_parser()
    args = parser.parse_args(["config", "set", "track_project_names", "true"])
    assert tracker.cmd_config_set(args) == 1
    assert "whimsical" in capsys.readouterr().err
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracker_cli.py -q`
Expected: FAIL (`args.project_mode` missing; `--track-projects` still accepted; `config set ... true` currently succeeds)

- [ ] **Step 3: Update `tracker.py`**

Imports — add:

```python
from src.project_identity import PROJECT_NAME_MODES, ProjectIdentityStore, ProjectNameResolver
```

Remove `_parse_bool_arg`. Replace `_build_pipeline`:

```python
def _build_pipeline(cfg: Config) -> TrackerPipeline:
    paths = cfg.paths
    mode = cfg.track_project_names
    identity_store = (
        ProjectIdentityStore(cfg.db_path) if mode in ("no", "whimsical") else None
    )
    resolver = ProjectNameResolver(mode, identity_store)
    return (
        TrackerPipeline()
        .context(cfg.context)
        .add(CopilotCliCollector(paths.copilot_home, resolver=resolver))
        .add(ClaudeCliCollector(paths.claude_projects, resolver=resolver))
    )
```

In `cmd_collect`, replace the track resolution block and the pipeline call:

```python
    overrides = {}
    if args.project_mode is not None:
        overrides["track_project_names"] = args.project_mode
    cfg = Config.load(**overrides)
    cfg = Config(
        paths=cfg.paths,
        db_path=Path(args.db) if args.db else cfg.db_path,
        lookback_days=args.lookback,
        track_project_names=cfg.track_project_names,
        context=args.context if args.context else cfg.context,
        remote_stores=cfg.remote_stores,
    )

    since = date.today() - timedelta(days=cfg.lookback_days)
    pipeline = _build_pipeline(cfg)
```

In `_build_parser`, replace the mutually-exclusive group with:

```python
    p_collect.add_argument("--project-mode", dest="project_mode",
                           choices=list(PROJECT_NAME_MODES), default=None,
                           help="project naming: yes=real name, no=guid, "
                                "whimsical=masked name (override toml)")
```

(and delete `p_collect.set_defaults(track_projects=None)` plus the `track_group` lines).

In `cmd_config_set`, replace the boolean handling:

```python
def cmd_config_set(args) -> int:
    enum_keys = {"track_project_names": PROJECT_NAME_MODES}
    str_keys = {"context"}
    if args.key in enum_keys:
        value = args.value.strip().lower()
        if value not in enum_keys[args.key]:
            print(
                f"Config value for {args.key!r} must be one of: "
                f"{', '.join(enum_keys[args.key])}",
                file=sys.stderr,
            )
            return 1
    elif args.key in str_keys:
        value = args.value.strip()
        if not value:
            print("Config value for 'context' must be a non-empty string", file=sys.stderr)
            return 1
    else:
        supported = sorted(set(enum_keys) | str_keys)
        print(
            f"Unknown config key: {args.key!r}. Supported: {', '.join(supported)}",
            file=sys.stderr,
        )
        return 1
    write_toml_setting(args.key, value)
    print(f"Set {args.key} = {value} in ~/.tokentracer.toml")
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tracker_cli.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: ALL PASS. Fix any remaining tests still using the old boolean/collector API.

- [ ] **Step 6: Smoke-test the CLI**

```bash
python tracker.py collect --lookback 1 --db /tmp/smoke.db --project-mode whimsical
python tracker.py report --db /tmp/smoke.db --period day
```
Expected: collect prints a record count; report shows whimsical `adjective_surname` values in the Project column. Delete `/tmp/smoke.db` afterwards.

- [ ] **Step 7: Commit**

```bash
git add tracker.py tests/test_tracker_cli.py
git commit -m "feat!: wire tri-state --project-mode through collect/config CLI"
```

---

### Task 8: Sync exclusion regression test

**Files:**
- Test: `tests/test_sync_excludes_identities.py` (create)

**Interfaces:**
- Consumes: `SqliteStore` (existing), `ProjectIdentityStore` (Task 3).

- [ ] **Step 1: Write the test**

Create `tests/test_sync_excludes_identities.py`:

```python
"""project_identities is local-only: it must never reach any sync path."""
from __future__ import annotations

from src.models import SessionRecord
from src.project_identity import ProjectIdentityStore
from src.stores.sqlite import SqliteStore


def test_identity_rows_never_appear_in_unsynced(tmp_path):
    db = tmp_path / "usage.db"
    identity = ProjectIdentityStore(db)
    identity.resolve_whimsical("/work/secret-project")

    store = SqliteStore(db)
    store.upsert([SessionRecord(session_id="s1", source="copilot_cli",
                                model="m", date="2026-07-08")])

    pending = store.unsynced_for("supabase")
    assert [r.session_id for r in pending] == ["s1"]
    # No pending record leaks identity data
    for rec in pending:
        assert "secret-project" not in (rec.project or "")


def test_identity_table_coexists_with_sync_log(tmp_path):
    db = tmp_path / "usage.db"
    ProjectIdentityStore(db).resolve_guid("/work/x")
    store = SqliteStore(db)
    rec = SessionRecord(session_id="s1", source="copilot_cli",
                        model="m", date="2026-07-08")
    store.upsert([rec])
    store.mark_synced([rec], "supabase")
    assert store.unsynced_for("supabase") == []
    # Identity mapping survives untouched
    assert ProjectIdentityStore(db).resolve_guid("/work/x") is not None
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_sync_excludes_identities.py -q`
Expected: PASS immediately (exclusion holds by construction — this is a regression guard).

- [ ] **Step 3: Commit**

```bash
git add tests/test_sync_excludes_identities.py
git commit -m "test: guard that project_identities never enters sync paths"
```

---

### Task 9: Documentation updates

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`
- Modify: `docs/ARCHITECTURE.md`

- [ ] **Step 1: Update `README.md`**

Search for `track_project_names`, `--track-projects`, and `--no-track-projects` and update:
- Config examples → `track_project_names = "whimsical"` (show all three values with one-line meaning: `"yes"` real name, `"no"` stable guid, `"whimsical"` docker-style masked name).
- CLI examples → `python3 tracker.py collect --project-mode whimsical` and `python3 tracker.py config set track_project_names whimsical`.
- Add a short paragraph: masked modes keep a local-only `project_identities` table (cwd → guid → whimsical name, case-insensitive cwd matching) inside `usage.db`; it is never synced to remote stores, so remote data only ever sees the masked value in `sessions.project`.

- [ ] **Step 2: Update `CLAUDE.md`**

- In the Commands section, replace `config set track_project_names true` with `config set track_project_names whimsical   # yes | no | whimsical`.
- In the `src/` architecture map, add:
  ```
  project_identity.py    ProjectIdentityStore (local-only cwd→guid→whimsical table) + ProjectNameResolver (tri-state naming policy)
  whimsy/                Standalone docker-style name generator (stdlib-only, extractable to its own repo; do not import src/* from it)
  ```
- In the Config file paragraph, replace the bool description: `track_project_names` is a string, one of `"yes"` (real name), `"no"` (stable guid), `"whimsical"` (masked name); CLI override is `--project-mode`; invalid/boolean values warn and fall back to `"no"`.
- Note that `project_identities` is excluded from sync by construction.

- [ ] **Step 3: Update `docs/ARCHITECTURE.md`**

- Add `ProjectNameResolver`/`ProjectIdentityStore` to the collection data-flow description: collectors pass `(display_name, cwd)` to the shared resolver, which returns the value stored in `sessions.project` according to the tri-state mode.
- Add `src/whimsy/` as a standalone component (stdlib-only, one public function `generate_name`, designed for extraction).
- Note the `project_identities` table in the storage section: same `usage.db`, local-only, never referenced by `sync_log`/`unsynced_for`.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md docs/ARCHITECTURE.md
git commit -m "docs: document tri-state project naming and whimsy component"
```
