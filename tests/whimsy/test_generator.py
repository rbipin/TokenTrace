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


def test_existing_is_optional():
    name = generate_name(rng=random.Random(11))
    adjective, _, surname = name.partition("_")
    assert adjective in ADJECTIVES
    assert surname in SURNAMES
    assert generate_name() # module-level rng also works


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
