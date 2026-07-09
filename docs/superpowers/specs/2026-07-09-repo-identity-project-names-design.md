# Repo-Identity Project Names — Design

**Date**: 2026-07-09
**Status**: Approved

## Problem

Collectors currently identify projects by the full working-directory path:

- The `project_identities` table keys rows by the full normalized cwd (e.g.
  `c:\repo\me\tokentrace`), which makes `tokentracer projects` output noisy.
- Display names are derived from the last path segment, so forks or
  same-named repos are indistinguishable, and two clones of the same repo
  are treated as two different projects.

## Goal

Identify projects by **git repo identity** (`owner/repo`, e.g.
`rbipin/TokenTrace`) when available, falling back to the cwd folder name.
The full path no longer appears as an identity key or display name.

Decisions made during brainstorming:

- Concern is display/cleanliness (full paths are local-only already; privacy
  is not the driver).
- Key by repo identity, folder name as fallback — two clones of one repo
  collapse into one project; that is desired.
- Claude sessions resolve repo identity by reading `<cwd>/.git/config`
  (read-only, best-effort).
- `yes` mode displays the full slug (`rbipin/TokenTrace`), not just the repo
  name.
- Existing identities are migrated in place, preserving guids and whimsical
  names.

## Design

### 1. Repo identity resolution — `src/repo_identity.py` (new)

One public function:

```python
def resolve_repo_slug(cwd: str) -> str | None
```

- Walks from `cwd` upward looking for `.git` (directory, or worktree pointer
  file containing `gitdir: <path>` — followed to the real git dir).
- Parses `config` with stdlib `configparser` and reads
  `[remote "origin"] url`.
- Normalizes the URL to `owner/repo`: supports
  `https://github.com/owner/repo.git`, `git@host:owner/repo.git`
  (scp-style), and `ssh://` forms; strips a trailing `.git`. Host-agnostic —
  takes the last two path segments, so GitLab/Bitbucket work too.
- Returns `None` when the path does not exist, no `.git` is found, there is
  no origin remote, or the URL is unparseable. Never raises; strictly
  read-only with respect to the repo.
- Module-level LRU cache keyed by normalized cwd so a collection run with
  many sessions in one project reads the config once.

### 2. Collector changes

Both collectors compute one `project_key` per session and pass it to
`ProjectNameResolver.resolve(...)` as **both** display name and identity key
(they are now the same value; the tri-state naming policy still decides
whether the stored project is the raw slug, a guid, or a whimsical name):

- **Copilot** (`src/collectors/copilot_cli.py`):
  `project_key = repository column` if non-empty (already `owner/repo`,
  no file I/O), else `resolve_repo_slug(cwd)`, else `Path(cwd).name`,
  else `None`.
- **Claude** (`src/collectors/claude_cli.py`):
  `project_key = resolve_repo_slug(cwd_seen)`, else
  `Path(cwd_seen).name`, else `None`.

`ProjectNameResolver.resolve(display_name, key)` keeps its signature;
collectors now pass `(project_key, project_key)` instead of
`(folder_name, full_cwd)`. `ProjectIdentityStore` continues to normalize
keys with `casefold()`.

Resulting behavior per mode:

- `yes` → project shows `rbipin/TokenTrace` (or folder name fallback).
- `no` / `whimsical` → stable guid / whimsical name derived from the slug
  key, so all clones of the same repo map to one project identity.

### 3. Identity store migration — `ProjectIdentityStore.__init__`

One-time, idempotent re-key of existing `project_identities` rows:

- A row needs migration if its `cwd_key` looks like a path: contains `\`,
  contains `:`, starts with `/`, or contains more than one `/`. New keys
  never match this test — slug keys contain exactly one interior `/`
  (`owner/repo`) and folder-name keys contain no separators at all.
- For each such row: `new_key = resolve_repo_slug(old_key)` if the path
  still exists and is a repo, else the folder name of `old_key`;
  casefolded.
- If `new_key` is unclaimed → `UPDATE` the row in place (guid and whimsical
  name preserved).
- If `new_key` already exists (two clones of one repo) → keep the row with
  the earlier `created_at`, delete the other. Historical session rows
  stamped with the losing guid keep it — sessions store the resolved value,
  not a foreign key, so nothing breaks.
- Runs inside the existing table-creation connection; failure warns to
  stderr and leaves rows untouched (collection still works with old
  identities).

Note: past sessions collected under `yes` mode stored folder names; new
sessions store slugs. This display drift in historical rows is accepted.

### 4. Testing

- `tests/test_repo_identity.py` — tmp_path fixtures with fake `.git/config`
  files: https URL, ssh URL, scp-style URL, trailing `.git`, worktree
  `gitdir:` pointer, no origin remote, no `.git` at all, nonexistent path,
  walk-up from subdirectory.
- Collector tests (`test_claude_cli_collector.py`, copilot equivalent) —
  slug used when repo resolvable; folder-name fallback otherwise.
- `test_project_identity.py` — migration: path→slug re-key preserves
  guid/whimsical, collision merge keeps older row, second run is a no-op,
  non-path keys untouched.

### Documentation

Update `CLAUDE.md`: add `repo_identity.py` to the architecture table and
describe the new key semantics (repo slug, folder fallback, migration).

## Out of scope

- Privacy changes to what is persisted locally (full paths in old backups,
  etc.).
- Any new collector surfaces.
- Changing the tri-state naming policy or sync behavior.
