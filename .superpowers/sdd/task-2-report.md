# Task 2 Report — Whimsy generator + package README

## TDD evidence

### RED
Command: `python -m pytest tests/whimsy/test_generator.py -q`

Output:
```text
ImportError: cannot import name 'generate_name' from 'src.whimsy'
```

### GREEN
Command: `python -m pytest tests/whimsy -q`

Output:
```text
8 passed in 0.04s
```

Command: `python -m pytest -q`

Output:
```text
111 passed, 5 warnings in 4.46s
```

## Files changed

- `src/whimsy/__init__.py`
- `src/whimsy/generator.py`
- `src/whimsy/README.md`
- `tests/whimsy/test_generator.py`

## Self-review findings

- `src.whimsy` now exports only `generate_name`.
- `generator.py` uses only stdlib plus local word lists.
- README uses a normal fenced code block and matches the brief text.
- No issues found.

## Commit

- `1f46902` — `feat(whimsy): add generate_name with collision retry and suffix fallback`
## 2026-07-08 Fix whimsy candidate loop
- Changed `generate_name` to check exactly 20 random candidates before suffix fallback.
- Tests: `python -m pytest tests/whimsy/test_generator.py -q` and `python -m pytest tests/whimsy -q`.
- Summary: 4 passed; 8 passed.
