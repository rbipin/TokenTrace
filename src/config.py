"""Configuration dataclasses and TOML persistence for ai-token-tracer."""
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
    """Filesystem paths for each supported AI tool's data directory."""

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
    """Merge one [tracking] key into ~/.tokentracer.toml (no external deps).

    On Python 3.11+ uses tomllib to parse then rewrites all sections safely.
    On older Python falls back to a line-level patch so no existing sections
    are destroyed.
    """
    val_str = str(value).lower()

    if tomllib is not None:
        # Full parse-and-rewrite path (Python 3.11+)
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
    else:
        # Line-patch fallback (Python < 3.11): avoids destroying existing content
        if _TOML_PATH.exists():
            existing_lines = _TOML_PATH.read_text(encoding="utf-8").splitlines()
        else:
            existing_lines = []

        in_tracking = False
        key_found = False
        new_lines: list[str] = []
        for line in existing_lines:
            stripped = line.strip()
            if stripped.startswith("["):
                if key_found is False and in_tracking:
                    # leaving [tracking] without having found the key — insert it
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
