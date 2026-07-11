from __future__ import annotations

import re
import tomllib
from pathlib import Path


_BUNDLED_ALIASES_PATH = Path(__file__).parent / "model_aliases.toml"
_USER_ALIASES_PATH = Path.home() / ".tokentracer" / "model_aliases.toml"


def _load_aliases() -> dict[str, dict[str, str]]:
    """Load model aliases from the user's TOML file, falling back to the bundled one.

    ~/.tokentracer/model_aliases.toml (if present) takes precedence, so users can
    add/override aliases without waiting on a package release. Degrades gracefully
    to an empty alias table when neither file is present, so a missing/malformed
    alias TOML doesn't crash CLI startup.
    """
    path = _USER_ALIASES_PATH if _USER_ALIASES_PATH.exists() else _BUNDLED_ALIASES_PATH
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


_ALIASES = _load_aliases()
_DATE_SUFFIX_PATTERN = re.compile(r"^(.+)-(\d{8})$")


def normalize_model(raw: str, source: str) -> str:
    """Normalize a raw model name to its canonical form.

    Applied in order:
    1. Strip trailing -YYYYMMDD date suffix (regex)
    2. Look up (source, stripped) in alias table
    3. Passthrough the stripped name unchanged

    Args:
        raw: Raw model name as reported by source
        source: Source harness (e.g., "claude_cli", "copilot_cli")

    Returns:
        Canonical model name
    """
    # Step 1: Strip date suffix, if present
    match = _DATE_SUFFIX_PATTERN.match(raw)
    stripped = match.group(1) if match else raw

    # Step 2: Try alias lookup on the (possibly stripped) name
    if source in _ALIASES and stripped in _ALIASES[source]:
        return _ALIASES[source][stripped]

    # Step 3: Passthrough
    return stripped
