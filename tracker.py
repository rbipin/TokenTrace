"""CLI entry point for the Copilot Usage Tracker.

Subcommands:
  collect   Scan local Copilot data and upsert daily activity (the scheduled job).
  report    Aggregate stored activity by day / month / year.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

from aitoken.collectors import CopilotCliCollector
from aitoken.config import Config
from aitoken.pipeline import TrackerPipeline
from aitoken.report import UsageReporter, format_table
from aitoken.store import UsageStore


def _build_pipeline(cfg: Config, since: date) -> TrackerPipeline:
    paths = cfg.paths
    return (
        TrackerPipeline()
        .add(CopilotCliCollector(paths.copilot_home))
        .since(since)
        .store(UsageStore(cfg.db_path))
    )


def cmd_collect(args: argparse.Namespace) -> int:
    cfg = Config(lookback_days=args.lookback)
    if args.db:
        cfg = Config(paths=cfg.paths, db_path=args.db, lookback_days=args.lookback)
    since = date.today() - timedelta(days=cfg.lookback_days)
    result = _build_pipeline(cfg, since).run()
    print(
        f"collected {result.records_written} daily rows from "
        f"{result.collectors_run} collectors since {since.isoformat()} "
        f"-> {cfg.db_path}"
    )
    for err in result.errors:
        print(f"  warning: {err}", file=sys.stderr)
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    cfg = Config()
    db_path = args.db or cfg.db_path
    reporter = UsageReporter(db_path)
    rows = reporter.report(period=args.period, sources=args.source, models=args.model)
    if args.json:
        print(json.dumps([row.__dict__ for row in rows], indent=2))
    elif not rows:
        print("no activity recorded yet")
    else:
        print(format_table(rows))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tracker", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="scan local Copilot data (scheduled job)")
    p_collect.add_argument("--lookback", type=int, default=3, help="days to re-scan (default 3)")
    p_collect.add_argument("--db", type=str, default=None, help="override database path")
    p_collect.set_defaults(func=cmd_collect)

    p_report = sub.add_parser("report", help="aggregate stored activity")
    p_report.add_argument("--period", choices=["day", "month", "year"], default="day")
    p_report.add_argument("--source", action="append", help="filter by source (repeatable)")
    p_report.add_argument("--model", action="append", help="filter by model (repeatable)")
    p_report.add_argument("--json", action="store_true", help="emit JSON")
    p_report.add_argument("--db", type=str, default=None, help="override database path")
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
