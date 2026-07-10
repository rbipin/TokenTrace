from __future__ import annotations

import re
import tomllib
from pathlib import Path


_ALIASES_PATH = Path(__file__).parent / "model_aliases.toml"


def _load_aliases() -> dict[str, dict[str, str]]:
    """Load model aliases from the TOML file.

    Degrades gracefully to an empty alias table when the file is missing,
    so a missing/malformed alias TOML doesn't crash CLI startup.
    """
    if not _ALIASES_PATH.exists():
        return {}
    with open(_ALIASES_PATH, "rb") as f:
        return tomllib.load(f)


_ALIASES = _load_aliases()
_DATE_SUFFIX_PATTERN = re.compile(r"^(.+)-(\d{8})$")


def normalize_model(raw: str, source: str) -> str:
    """Normalize a raw model name to its canonical form.

    Applied in order:
    1. Strip trailing -YYYYMMDD date suffix (regex)
    2. Look up (source, raw) in alias table
    3. Passthrough unchanged

    Args:
        raw: Raw model name as reported by source
        source: Source harness (e.g., "claude_cli", "copilot_cli")

    Returns:
        Canonical model name
    """
    # Step 1: Try to strip date suffix
    match = _DATE_SUFFIX_PATTERN.match(raw)
    if match:
        return match.group(1)

    # Step 2: Try alias lookup
    if source in _ALIASES:
        if raw in _ALIASES[source]:
            return _ALIASES[source][raw]

    # Step 3: Passthrough
    return raw
