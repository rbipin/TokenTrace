# src/config.py
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:
    tomllib = None  # type: ignore[assignment]

_TOML_PATH = Path.home() / ".tokentracer.toml"


@dataclass(frozen=True)
class Paths:
    copilot_home: Path = field(default_factory=lambda: Path.home() / ".copilot")
    claude_projects: Path = field(
        default_factory=lambda: Path.home() / ".claude" / "projects"
    )


@dataclass(frozen=True)
class Config:
    paths: Paths = field(default_factory=Paths)
    db_path: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[1] / "usage.db"
    )
    lookback_days: int = 3
    track_project_names: bool = False

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
            except Exception as exc:
                print(f"Warning: could not parse ~/.tokentracer.toml: {exc}", file=sys.stderr)
        base.update(overrides)
        return cls(**base)


def write_toml_setting(key: str, value: bool) -> None:
    """Merge one [tracking] key into ~/.tokentracer.toml (no external deps)."""
    existing: dict[str, dict] = {}
    if tomllib is not None and _TOML_PATH.exists():
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
