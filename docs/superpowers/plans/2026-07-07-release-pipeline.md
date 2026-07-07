# Release Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add GitHub Actions workflows so pushing a `v*` tag tests, builds, and publishes a GitHub Release with an installable wheel + sdist.

**Architecture:** Two workflows: a reusable CI workflow (`ci.yml`, runs pytest on push/PR and via `workflow_call`) and a release workflow (`release.yml`, tag-triggered, reuses CI, verifies the tag matches `pyproject.toml`'s version, builds with `python -m build`, and publishes via `gh release create --generate-notes`).

**Tech Stack:** GitHub Actions, Python 3.11, pytest, `build`, `gh` CLI (preinstalled on runners), stdlib `tomllib`.

**Spec:** `docs/superpowers/specs/2026-07-07-release-pipeline-design.md`

## Global Constraints

- Runtime is standard library only; pytest is the only dev dependency (`requirements.txt`).
- Python floor: `requires-python = ">=3.11"` (use Python 3.11 in CI).
- No PyPI publishing. No OS/Python matrix. No automated version bumping.
- Version stays hand-maintained in `pyproject.toml`; pipeline fails on tag/version mismatch.
- Do not add a `Co-authored-by: Copilot` trailer to commits.

---

### Task 1: CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: a reusable workflow at `./.github/workflows/ci.yml` exposing `workflow_call` (no inputs), with a job named `test`. Task 2's release workflow calls it with `uses: ./.github/workflows/ci.yml`.

- [ ] **Step 1: Confirm the test suite passes locally (baseline)**

Run: `python -m pytest -q`
Expected: all tests pass (e.g. `NN passed`). If this fails, stop — fix the suite before adding CI.

- [ ] **Step 2: Create the workflow file**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
  workflow_call:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run tests
        run: python -m pytest -q
```

- [ ] **Step 3: Validate YAML syntax**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('OK')"`
(If PyYAML is not installed: `pip install pyyaml` first — it is a local validation aid only, not a project dependency.)
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add pytest CI workflow (push/PR + workflow_call)"
```

---

### Task 2: Release workflow with version check

**Files:**
- Create: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `./.github/workflows/ci.yml` via `workflow_call` (job `test`) from Task 1.
- Produces: a tag-triggered (`v*`) workflow that publishes a GitHub Release named after the tag with `dist/*` attached. Task 3's README links follow the pattern `https://github.com/rbipin/TokenTrace/releases/download/v<X.Y.Z>/tokentracer-<X.Y.Z>-py3-none-any.whl`.

- [ ] **Step 1: Verify the version-check logic locally (expect PASS case)**

Run:

```bash
python - <<'PY'
import tomllib, pathlib
version = tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"]
tag = "v0.1.0"  # simulate GITHUB_REF_NAME
assert tag.removeprefix("v") == version, f"Tag {tag} != pyproject version {version}"
print(f"OK: {version}")
PY
```

Expected: `OK: 0.1.0`
(On Windows PowerShell, pipe the script via a here-string to `python -` instead of the heredoc.)

- [ ] **Step 2: Verify the version-check logic fails on mismatch (expect FAIL case)**

Same script with `tag = "v9.9.9"`.
Expected: `AssertionError: Tag v9.9.9 != pyproject version 0.1.0` (non-zero exit).

- [ ] **Step 3: Create the workflow file**

Create `.github/workflows/release.yml`:

```yaml
name: Release

on:
  push:
    tags: ["v*"]

jobs:
  test:
    uses: ./.github/workflows/ci.yml

  release:
    needs: test
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Verify tag matches pyproject.toml version
        run: |
          python - <<'PY'
          import os, sys, tomllib, pathlib
          version = tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"]
          tag = os.environ["GITHUB_REF_NAME"]
          if tag.removeprefix("v") != version:
              sys.exit(f"Tag {tag} does not match pyproject.toml version {version}. "
                       f"Bump pyproject.toml or retag, then push again.")
          print(f"Version check OK: {version}")
          PY
      - name: Build wheel and sdist
        run: |
          pip install build
          python -m build
      - name: Create GitHub Release
        env:
          GH_TOKEN: ${{ github.token }}
        run: gh release create "$GITHUB_REF_NAME" dist/* --generate-notes
```

- [ ] **Step 4: Validate YAML syntax**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml')); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Verify the build works locally**

Run:

```bash
pip install build
python -m build
```

Expected: `dist/tokentracer-0.1.0-py3-none-any.whl` and `dist/tokentracer-0.1.0.tar.gz` created. Then clean up: delete `dist/` and any `*.egg-info` directory (do not commit them).

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add tag-triggered release workflow (build + GitHub Release)"
```

---

### Task 3: README install section

**Files:**
- Modify: `README.md` (add an "Install" section near the top, after the intro; adjust to the README's existing heading style)

**Interfaces:**
- Consumes: release asset URL pattern from Task 2.

- [ ] **Step 1: Add the install section**

Insert into `README.md`:

```markdown
## Install

Install a released version straight from GitHub Releases (replace `0.1.0` with the latest version):

```bash
# uv
uv tool install https://github.com/rbipin/TokenTrace/releases/download/v0.1.0/tokentracer-0.1.0-py3-none-any.whl

# pip
pip install https://github.com/rbipin/TokenTrace/releases/download/v0.1.0/tokentracer-0.1.0-py3-none-any.whl

# from source at a tag
pip install git+https://github.com/rbipin/TokenTrace@v0.1.0
```

### Releasing (maintainers)

1. Bump `version` in `pyproject.toml` and commit to `main`.
2. `git tag v<version> && git push origin v<version>`.
3. CI tests, verifies the tag matches the version, builds, and publishes the GitHub Release automatically.
```

(Use a fence style that nests correctly in the actual README.)

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document install from GitHub Releases and release procedure"
```

---

### Task 4: End-to-end verification (after push to GitHub)

**Files:** none (operational verification)

**Interfaces:**
- Consumes: workflows from Tasks 1–2, docs from Task 3.

- [ ] **Step 1: Push main and confirm CI is green**

```bash
git push origin main
```

Then: `gh run watch` (or `gh run list --workflow=ci.yml --limit 1`) — expect `completed success`.

- [ ] **Step 2: Tag and push the release**

```bash
git tag v0.1.0
git push origin v0.1.0
```

- [ ] **Step 3: Confirm the release**

Run: `gh release view v0.1.0`
Expected: release exists with auto-generated notes and assets `tokentracer-0.1.0-py3-none-any.whl` + `tokentracer-0.1.0.tar.gz`.

- [ ] **Step 4: Confirm the wheel installs and runs**

In a clean venv (or via uv):

```bash
uv tool install https://github.com/rbipin/TokenTrace/releases/download/v0.1.0/tokentracer-0.1.0-py3-none-any.whl
tokentracer report
```

Expected: install succeeds; `tokentracer report` runs (creates/uses `~/.tokentracer/usage.db`).

- [ ] **Step 5: Recovery drill (documented, only if a release fails)**

If tests fail or the version check fails: no release is created. Fix, then:

```bash
git push origin :refs/tags/v0.1.0
git tag -d v0.1.0
git tag v0.1.0
git push origin v0.1.0
```

If a release already exists for the tag: `gh release delete v0.1.0 --yes` first.
