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
