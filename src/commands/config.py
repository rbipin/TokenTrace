"""The `config` subcommand: manage ~/.tokentracer.toml settings."""
from __future__ import annotations

import argparse
import sys

from src.config import write_toml_setting
from src.project_identity import PROJECT_NAME_MODES


class ConfigCommand:
    name = "config"
    help = "manage configuration"

    def __init__(self) -> None:
        self._parser: argparse.ArgumentParser | None = None

    def configure(self, parser: argparse.ArgumentParser) -> None:
        self._parser = parser
        config_sub = parser.add_subparsers(dest="config_cmd")
        p_set = config_sub.add_parser("set", help="set a config value")
        p_set.add_argument("key")
        p_set.add_argument("value")

    def run(self, args: argparse.Namespace) -> int:
        if args.config_cmd == "set":
            return self._set(args)
        if self._parser is not None:
            self._parser.print_help()
        return 1

    def _set(self, args: argparse.Namespace) -> int:
        enum_keys = {"track_project_names": PROJECT_NAME_MODES}
        str_keys = {"context"}
        if args.key in enum_keys:
            value = args.value.strip().lower()
            if value not in enum_keys[args.key]:
                print(
                    f"Config value for {args.key!r} must be one of: "
                    f"{', '.join(enum_keys[args.key])}",
                    file=sys.stderr,
                )
                return 1
        elif args.key in str_keys:
            value = args.value.strip()
            if not value:
                print("Config value for 'context' must be a non-empty string",
                      file=sys.stderr)
                return 1
        else:
            supported = sorted(set(enum_keys) | str_keys)
            print(
                f"Unknown config key: {args.key!r}. Supported: {', '.join(supported)}",
                file=sys.stderr,
            )
            return 1
        write_toml_setting(args.key, value)
        print(f"Set {args.key} = {value} in ~/.tokentracer.toml")
        return 0
