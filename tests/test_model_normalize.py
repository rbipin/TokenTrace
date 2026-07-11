from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.model_normalize import _load_aliases, normalize_model


def test_strips_date_suffix_from_claude_snapshot():
    assert normalize_model("claude-haiku-4-5-20251001", "claude_cli") == "claude-haiku-4-5"


def test_passes_through_alias_form_unchanged():
    assert normalize_model("claude-sonnet-4-6", "claude_cli") == "claude-sonnet-4-6"


def test_looks_up_cross_vendor_name():
    assert normalize_model("claude-sonnet-4.5", "copilot_cli") == "claude-sonnet-4-5"


def test_looks_up_cross_vendor_name_for_opus():
    assert normalize_model("claude-opus-4.8", "copilot_cli") == "claude-opus-4-8"


def test_looks_up_cross_vendor_name_for_haiku():
    assert normalize_model("claude-haiku-4.5", "copilot_cli") == "claude-haiku-4-5"


def test_passes_through_unrecognized_name():
    assert normalize_model("gpt-4o", "copilot_cli") == "gpt-4o"


def test_passes_through_unknown_sentinel():
    assert normalize_model("unknown", "claude_cli") == "unknown"


def test_passes_through_synthetic_sentinel():
    assert normalize_model("<synthetic>", "claude_cli") == "<synthetic>"


def test_regex_does_not_misfire_on_non_date_suffix():
    assert normalize_model("o1-preview", "copilot_cli") == "o1-preview"


def test_alias_lookup_applies_after_stripping_date_suffix():
    assert normalize_model("claude-sonnet-4.5-20250929", "copilot_cli") == "claude-sonnet-4-5"


def test_load_aliases_returns_empty_dict_when_file_missing():
    with (
        patch("src.model_normalize._USER_ALIASES_PATH", Path("/nonexistent/user/model_aliases.toml")),
        patch("src.model_normalize._BUNDLED_ALIASES_PATH", Path("/nonexistent/bundled/model_aliases.toml")),
    ):
        assert _load_aliases() == {}


def test_load_aliases_prefers_user_file_over_bundled(tmp_path):
    user_path = tmp_path / "model_aliases.toml"
    user_path.write_text('[copilot_cli]\n"foo" = "bar"\n', encoding="utf-8")
    with (
        patch("src.model_normalize._USER_ALIASES_PATH", user_path),
        patch("src.model_normalize._BUNDLED_ALIASES_PATH", Path("/nonexistent/bundled/model_aliases.toml")),
    ):
        assert _load_aliases() == {"copilot_cli": {"foo": "bar"}}


def test_load_aliases_falls_back_to_bundled_when_user_file_missing(tmp_path):
    bundled_path = tmp_path / "model_aliases.toml"
    bundled_path.write_text('[copilot_cli]\n"foo" = "bar"\n', encoding="utf-8")
    with (
        patch("src.model_normalize._USER_ALIASES_PATH", Path("/nonexistent/user/model_aliases.toml")),
        patch("src.model_normalize._BUNDLED_ALIASES_PATH", bundled_path),
    ):
        assert _load_aliases() == {"copilot_cli": {"foo": "bar"}}
