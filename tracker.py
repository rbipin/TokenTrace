from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from src.collectors import CopilotCliCollector, ClaudeCliCollector
from src.config import Config, write_toml_setting
from src.pipeline import TrackerPipeline
from src.report import UsageReporter
from src.stores.sqlite import SqliteStore
from src.stores.registry import instantiate_store


def _parse_bool_arg(val: str) -> bool:
    return val.lower() in ("1", "true", "yes")


def _build_pipeline(cfg: Config, track_project_names: bool) -> TrackerPipeline:
    paths = cfg.paths
    return (
        TrackerPipeline()
        .add(CopilotCliCollector(paths.copilot_home, track_project_names=track_project_names))
        .add(ClaudeCliCollector(paths.claude_projects, track_project_names=track_project_names))
    )


def _build_stores(cfg: Config) -> list:
    stores = [SqliteStore(cfg.db_path)]
    for sc in cfg.remote_stores:
        try:
            stores.append(instantiate_store(sc.name, sc.params, sc.class_path))
        except Exception as exc:
            print(f"Warning: could not load store {sc.name!r}: {exc}", file=sys.stderr)
    return stores


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
        remote_stores=cfg.remote_stores,
    )

    since = date.today() - timedelta(days=cfg.lookback_days)
    pipeline = _build_pipeline(cfg, cfg.track_project_names)
    stores = _build_stores(cfg)
    result = pipeline.since(since).stores(*stores).run()

    for err in result.errors:
        print(f"Warning: {err}", file=sys.stderr)
    for err in result.stores_failed:
        print(f"Warning [store]: {err}", file=sys.stderr)

    print(
        f"Collected {result.records_written} session records "
        f"from {result.collectors_run} collectors "
        f"(since {since.isoformat()})"
    )
    return 0


def _run_sync(
    sqlite_store,
    remote_stores: list,
    dry_run: bool,
) -> dict:
    """Core sync logic — separated for testability.

    Returns a dict: {store_name: {"pushed": N, "failed": bool} | {"pending": N}}
    """
    result = {}
    for store in remote_stores:
        pending = sqlite_store.unsynced_for(store.name)
        if dry_run:
            result[store.name] = {"pending": len(pending)}
            continue
        try:
            if pending:
                store.upsert(pending)
                sqlite_store.mark_synced(pending, store.name)
            store.close()
            result[store.name] = {"pushed": len(pending), "failed": False}
        except Exception as exc:
            print(f"Warning [{store.name}]: {exc}", file=sys.stderr)
            result[store.name] = {"pushed": 0, "failed": True, "error": str(exc)}
    return result


def cmd_sync(args) -> int:
    cfg = Config.load()
    db_path = Path(args.db) if args.db else cfg.db_path

    if not cfg.remote_stores:
        print("No remote stores configured. Add [stores.X] sections to ~/.tokentracer.toml")
        return 0

    sqlite_store = SqliteStore(db_path)
    remote_stores = []
    for sc in cfg.remote_stores:
        try:
            remote_stores.append(instantiate_store(sc.name, sc.params, sc.class_path))
        except Exception as exc:
            print(f"Warning: could not load store {sc.name!r}: {exc}", file=sys.stderr)

    if not remote_stores:
        print("No remote stores could be loaded.")
        return 1

    label = "(dry run) " if args.dry_run else ""
    print(f"Syncing {len(remote_stores)} store(s)... {label}")
    result = _run_sync(sqlite_store, remote_stores, dry_run=args.dry_run)

    for store_name, info in result.items():
        if args.dry_run:
            print(f"  {store_name:<12} {info['pending']} pending")
        elif info["failed"]:
            unsynced = len(sqlite_store.unsynced_for(store_name))
            print(f"  {store_name:<12} failed ({unsynced} records pending)")
        else:
            print(f"  {store_name:<12} {info['pushed']} records pushed")

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

    # sync
    p_sync = sub.add_parser("sync", help="push unsynced records to remote stores")
    p_sync.add_argument("--dry-run", action="store_true",
                        help="show pending counts without pushing")

    return parser, p_config


def main() -> None:
    parser, p_config = _build_parser()
    args = parser.parse_args()

    if args.cmd == "collect":
        sys.exit(cmd_collect(args))
    elif args.cmd == "report":
        sys.exit(cmd_report(args))
    elif args.cmd == "sync":
        sys.exit(cmd_sync(args))
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
