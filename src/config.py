"""Configuration dataclasses and TOML persistence for ai-token-tracer."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    tomllib = None  # type: ignore[assignment]

_TOML_PATH = Path.home() / ".tokentracer.toml"
_ENV_FILE_PATH = Path.home() / ".tokentracer.env"


def _load_env_file(path: Path | None = None) -> dict[str, str]:
    """Parse a KEY=VALUE env file (comments and blank lines ignored).

    Values may be wrapped in single or double quotes. Malformed lines are skipped.
    """
    env_path = path if path is not None else _ENV_FILE_PATH
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _expand_env_vars(params: dict) -> dict:
    """Expand ${VAR} placeholders in params dict values.

    Lookup order: os.environ first, then ~/.tokentracer.env.

    Args:
        params: Dictionary with string and non-string values.

    Returns:
        New dictionary with ${VAR} patterns replaced.

    Raises:
        ValueError: If a ${VAR} placeholder is not found in either source.
    """
    file_env = _load_env_file()
    result = {}
    for key, value in params.items():
        if isinstance(value, str):
            # Find all ${VAR} patterns
            def replace_var(match):
                var_name = match.group(1)
                if var_name in os.environ:
                    return os.environ[var_name]
                if var_name in file_env:
                    return file_env[var_name]
                raise ValueError(f"Missing env var '{var_name}'")

            result[key] = re.sub(r"\$\{([^}]+)\}", replace_var, value)
        else:
            # Pass through non-string values unchanged
            result[key] = value
    return result


@dataclass(frozen=True)
class StoreConfig:
    """Configuration for a remote store."""

    name: str
    class_path: str | None  # from "class" key; None means resolve via entry points
    params: dict  # remaining keys passed as kwargs to the constructor


@dataclass(frozen=True)
class Paths:
    """Filesystem paths for each supported AI tool's data directory."""

    copilot_home: Path = field(default_factory=lambda: Path.home() / ".copilot")
    claude_projects: Path = field(
        default_factory=lambda: Path.home() / ".claude" / "projects"
    )


@dataclass(frozen=True)
class Config:
    paths: Paths = field(default_factory=Paths)
    db_path: Path = field(
        default_factory=lambda: Path.home() / ".tokentracer" / "usage.db"
    )
    lookback_days: int = 3
    track_project_names: bool = False
    remote_stores: tuple[StoreConfig, ...] = field(default_factory=tuple)

    @classmethod
    def load(cls, **overrides) -> "Config":
        """Load from ~/.tokentracer.toml, then apply keyword overrides."""
        base: dict = {}
        if tomllib is not None and _TOML_PATH.exists():
            try:
                with open(_TOML_PATH, "rb") as fh:
                    data = tomllib.load(fh)
                tracking = data.get("tracking", {})
                if "track_project_names" in tracking:
                    base["track_project_names"] = bool(tracking["track_project_names"])
                stores_raw = data.get("stores", {})
                remote: list[StoreConfig] = []
                for store_name, store_vals in stores_raw.items():
                    if store_name == "sqlite":
                        continue  # sqlite is always built-in; ignore explicit section
                    if isinstance(store_vals, dict):
                        class_path = store_vals.get("class")
                        params = {k: v for k, v in store_vals.items() if k != "class"}
                    else:
                        class_path = None
                        params = {}
                    remote.append(StoreConfig(name=store_name, class_path=class_path, params=params))
                base["remote_stores"] = tuple(remote)
            except Exception as exc:
                print(f"Warning: could not parse ~/.tokentracer.toml: {exc}", file=sys.stderr)
        base.update(overrides)
        return cls(**base)


def write_toml_setting(key: str, value: bool) -> None:
    """Merge one [tracking] key into ~/.tokentracer.toml."""
    if tomllib is not None:
        _write_toml_311(key, value)
    else:
        _write_toml_legacy(key, value)


def _write_toml_311(key: str, value: bool) -> None:
    """Full parse-and-rewrite path (Python 3.11+)."""
    existing: dict[str, dict] = {}
    if _TOML_PATH.exists():
        try:
            with open(_TOML_PATH, "rb") as fh:
                raw = tomllib.load(fh)
            for section, vals in raw.items():
                if isinstance(vals, dict):
                    existing[section] = dict(vals)
        except Exception:
            pass
    existing.setdefault("tracking", {})[key] = value
    lines: list[str] = []
    for section, vals in existing.items():
        lines.append(f"[{section}]")
        for k, v in vals.items():
            lines.append(f"{k} = {str(v).lower()}")
        lines.append("")
    _TOML_PATH.write_text("\n".join(lines), encoding="utf-8")


def _write_toml_legacy(key: str, value: bool) -> None:
    """Line-patch fallback (Python < 3.11) — preserves existing sections."""
    val_str = str(value).lower()
    existing_lines = _TOML_PATH.read_text(encoding="utf-8").splitlines() if _TOML_PATH.exists() else []

    in_tracking = False
    key_found = False
    new_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if not key_found and in_tracking:
                new_lines.append(f"{key} = {val_str}")
                key_found = True
            in_tracking = stripped == "[tracking]"
        if in_tracking and stripped.startswith(f"{key}"):
            lhs = stripped.split("=")[0].strip()
            if lhs == key:
                line = f"{key} = {val_str}"
                key_found = True
        new_lines.append(line)

    if not key_found:
        if not in_tracking:
            new_lines.append("[tracking]")
        new_lines.append(f"{key} = {val_str}")

    _TOML_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
