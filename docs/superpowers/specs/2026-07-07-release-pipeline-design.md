# Release Pipeline Design — GitHub Actions CI + Tag-Triggered Release

**Date**: 2026-07-07
**Status**: Approved

## Goal

When a version tag (`v*`) is pushed, automatically test, build, and publish a
GitHub Release with an installable wheel and sdist attached, so users can
install `tokentracer` via `uv tool install` or `pip install` from GitHub.
No PyPI publishing.

## Decisions

- **Distribution**: GitHub Release with built wheel + sdist attached (no PyPI).
- **Trigger**: pushing a `v*` tag runs the full pipeline (test → build → release).
- **Versioning**: `pyproject.toml` version stays hand-maintained. The pipeline
  verifies the tag (`v0.2.0`) matches the `pyproject.toml` version (`0.2.0`)
  and fails on mismatch. No setuptools-scm.
- **Test gate**: `pytest` must pass before any build or release step runs.
- **Release notes**: GitHub auto-generated notes (`--generate-notes`).
- **Architecture**: two workflows — a reusable CI workflow plus a release
  workflow that calls it (approach B).

## Components

### 1. `.github/workflows/ci.yml`

- **Triggers**: `push` to `main`, `pull_request`, and `workflow_call`.
- **Job `test`** on `ubuntu-latest`:
  1. `actions/checkout`
  2. `actions/setup-python` with Python 3.11
  3. `pip install -r requirements.txt`
  4. `python -m pytest -q`
- Single OS, single Python version: the project is pure-Python stdlib and
  tests are hermetic (synthetic `Paths`), so a matrix is unnecessary (YAGNI).

### 2. `.github/workflows/release.yml`

- **Trigger**: `push` with `tags: ['v*']`.
- **Job `test`**: `uses: ./.github/workflows/ci.yml` (reuses CI via
  `workflow_call`).
- **Job `release`** (`needs: test`, `permissions: contents: write`) on
  `ubuntu-latest`:
  1. `actions/checkout`
  2. `actions/setup-python` with Python 3.11
  3. **Version check**: read `project.version` from `pyproject.toml` with
     stdlib `tomllib`; compare to `${GITHUB_REF_NAME#v}`; exit non-zero with a
     clear error message on mismatch.
  4. `pip install build` then `python -m build` → wheel + sdist in `dist/`.
  5. `gh release create "$GITHUB_REF_NAME" dist/* --generate-notes` using the
     built-in `GITHUB_TOKEN` (`GH_TOKEN` env).

### 3. README install section

Document installing a released version:

```bash
# uv
uv tool install https://github.com/rbipin/TokenTrace/releases/download/v0.1.0/tokentracer-0.1.0-py3-none-any.whl

# pip
pip install https://github.com/rbipin/TokenTrace/releases/download/v0.1.0/tokentracer-0.1.0-py3-none-any.whl

# from source at a tag
pip install git+https://github.com/rbipin/TokenTrace@v0.1.0
```

## Release procedure (maintainer)

1. Bump `version` in `pyproject.toml`, commit to `main`.
2. `git tag v<version> && git push origin v<version>`.
3. Pipeline tests, verifies version, builds, and publishes the release.

## Error handling

- **Test failure or version mismatch**: the workflow fails before
  `gh release create` runs — no partial or artifact-less releases are created.
- **Recovery**: fix the problem, delete the tag
  (`git push origin :refs/tags/vX.Y.Z; git tag -d vX.Y.Z`), retag, push again.
- **Duplicate tag re-push**: `gh release create` fails if the release already
  exists; delete the release and tag first.

## Testing / verification

1. Merge the workflows; confirm `ci.yml` runs green on the push to `main`.
2. Tag `v0.1.0` and push; confirm the release appears with wheel + sdist and
   auto-generated notes.
3. On a clean machine/venv, `uv tool install <wheel-url>` and run
   `tokentracer report` to confirm the console script works.

## Out of scope

- PyPI publishing
- OS/Python version matrix in CI
- Automated version bumping / changelog files
