"""The `report` subcommand: display usage aggregates."""
from __future__ import annotations

import argparse
from pathlib import Path

from src.config import Config
from src.report import UsageReporter


class ReportCommand:
    name = "report"
    help = "show usage report"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--period", choices=["all", "day", "month", "year"],
                            default="day")
        parser.add_argument("--model", action="append", dest="model",
                            help="filter to model(s) (repeatable)")
        parser.add_argument("--by-project", action="store_true",
                            help="group by project (requires project tracking enabled)")
        parser.add_argument("--summary", action="store_true",
                            help="aggregate by period+model instead of showing "
                                 "per-session rows")
        parser.add_argument("--detailed", action="store_true",
                            help="dump every row in the db with all columns "
                                 "and sync status (ignores --period)")
        parser.add_argument("--json", action="store_true",
                            help="output as JSON")

    def run(self, args: argparse.Namespace) -> int:
        cfg = Config.load()
        db_path = Path(args.db) if args.db else cfg.db_path
        reporter = UsageReporter(db_path)
        output = reporter.report(
            period=args.period,
            models=args.model or None,
            by_project=args.by_project,
            summary=args.summary,
            as_json=args.json,
            detailed=args.detailed,
        )
        print(output, end="")
        return 0
