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
