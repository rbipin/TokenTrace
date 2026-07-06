from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from src.collectors import CopilotCliCollector, ClaudeCliCollector
from src.config import Config, write_toml_setting
from src.pipeline import TrackerPipeline
from src.report import UsageReporter
from src.store import UsageStore


def _parse_bool_arg(val: str) -> bool:
    return val.lower() in ("1", "true", "yes")


def _build_pipeline(cfg: Config, track_project_names: bool) -> TrackerPipeline:
    paths = cfg.paths
    return (
        TrackerPipeline()
        .add(CopilotCliCollector(paths.copilot_home, track_project_names=track_project_names))
        .add(ClaudeCliCollector(paths.claude_projects, track_project_names=track_project_names))
    )


def cmd_collect(args) -> int:
    # Resolve track_project_names: CLI flag > toml > default
    if args.track_projects is True:
        track = True
    elif args.track_projects is False:
        track = False
    else:
        track = None  # not specified — use toml/default

    cfg = Config.load(**({"track_project_names": track} if track is not None else {}))
    cfg = Config(
        paths=cfg.paths,
        db_path=Path(args.db) if args.db else cfg.db_path,
        lookback_days=args.lookback,
        track_project_names=cfg.track_project_names,
    )

    since = date.today() - timedelta(days=cfg.lookback_days)
    pipeline = _build_pipeline(cfg, cfg.track_project_names)
    result = pipeline.since(since).store(UsageStore(cfg.db_path)).run()

    for err in result.errors:
        print(f"Warning: {err}", file=sys.stderr)

    print(
        f"Collected {result.records_written} session records "
        f"from {result.collectors_run} collectors "
        f"(since {since.isoformat()})"
    )
    return 0


def cmd_report(args) -> int:
    cfg = Config.load()
    db_path = Path(args.db) if args.db else cfg.db_path
    reporter = UsageReporter(db_path)
    output = reporter.report(
        period=args.period,
        models=args.model or None,
        by_project=args.by_project,
        summary=args.summary,
        as_json=args.json,
    )
    print(output, end="")
    return 0


def cmd_config_set(args) -> int:
    supported = {"track_project_names"}
    if args.key not in supported:
        print(
            f"Unknown config key: {args.key!r}. Supported: {', '.join(supported)}",
            file=sys.stderr,
        )
        return 1
    bool_val = _parse_bool_arg(args.value)
    write_toml_setting(args.key, bool_val)
    print(f"Set {args.key} = {bool_val} in ~/.tokentracer.toml")
    return 0


def _build_parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    parser = argparse.ArgumentParser(prog="tracker", description="AI token tracker")
    parser.add_argument("--db", default=None, help="path to usage.db")
    sub = parser.add_subparsers(dest="cmd")

    # collect
    p_collect = sub.add_parser("collect", help="collect usage from local logs")
    p_collect.add_argument("--lookback", type=int, default=3,
                           help="days of history to collect (default: 3)")
    p_collect.set_defaults(track_projects=None)
    track_group = p_collect.add_mutually_exclusive_group()
    track_group.add_argument("--track-projects", dest="track_projects",
                             action="store_const", const=True,
                             help="store project names (override toml)")
    track_group.add_argument("--no-track-projects", dest="track_projects",
                             action="store_const", const=False,
                             help="suppress project names (override toml)")

    # report
    p_report = sub.add_parser("report", help="show usage report")
    p_report.add_argument("--period", choices=["all", "day", "month", "year"], default="day")
    p_report.add_argument("--model", action="append", dest="model",
                          help="filter to model(s) (repeatable)")
    p_report.add_argument("--by-project", action="store_true",
                          help="group by project (requires project tracking enabled)")
    p_report.add_argument("--summary", action="store_true",
                          help="aggregate by period+model instead of showing per-session rows")
    p_report.add_argument("--json", action="store_true",
                          help="output as JSON")

    # config
    p_config = sub.add_parser("config", help="manage configuration")
    config_sub = p_config.add_subparsers(dest="config_cmd")
    p_config_set = config_sub.add_parser("set", help="set a config value")
    p_config_set.add_argument("key")
    p_config_set.add_argument("value")

    return parser, p_config


def main() -> None:
    parser, p_config = _build_parser()
    args = parser.parse_args()

    if args.cmd == "collect":
        sys.exit(cmd_collect(args))
    elif args.cmd == "report":
        sys.exit(cmd_report(args))
    elif args.cmd == "config":
        if args.config_cmd == "set":
            sys.exit(cmd_config_set(args))
        else:
            p_config.print_help()
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
