"""The `collect` subcommand: gather usage from local logs into the store."""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

from src.collectors import ClaudeCliCollector, CopilotCliCollector
from src.commands.common import load_remote_stores, run_sync
from src.config import Config
from src.middleware import ModelNormalizeMiddleware
from src.pipeline import TrackerPipeline
from src.project_identity import (
    PROJECT_NAME_MODES,
    ProjectIdentityStore,
    ProjectNameResolver,
)
from src.stores.sqlite import SqliteStore


def _build_pipeline(cfg: Config) -> tuple[TrackerPipeline, ProjectIdentityStore | None]:
    """Build the collection pipeline plus the identity store it borrows (if any).

    The caller owns the returned store and must close it after the run.
    """
    paths = cfg.paths
    mode = cfg.track_project_names
    identity_store = (
        ProjectIdentityStore(cfg.db_path) if mode in ("no", "whimsical") else None
    )
    resolver = ProjectNameResolver(mode, identity_store)
    pipeline = (
        TrackerPipeline()
        .context(cfg.context)
        .add(CopilotCliCollector(paths.copilot_home, resolver=resolver))
        .add(ClaudeCliCollector(paths.claude_projects, resolver=resolver))
        .middlewares(ModelNormalizeMiddleware())
    )
    return pipeline, identity_store


def _build_stores(cfg: Config) -> list:
    return [SqliteStore(cfg.db_path), *load_remote_stores(cfg)]


class CollectCommand:
    name = "collect"
    help = "collect usage from local logs"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--lookback", type=int, default=3,
                            help="days of history to collect (default: 3)")
        parser.add_argument("--project-mode", dest="project_mode",
                            choices=list(PROJECT_NAME_MODES), default=None,
                            help="project naming: yes=real name, no=guid, "
                                 "whimsical=masked name (override toml)")
        parser.add_argument("--context", default=None,
                            help='usage context label, e.g. "work" or "personal" '
                                 "(override toml)")

    def run(self, args: argparse.Namespace) -> int:
        overrides = {}
        if args.project_mode is not None:
            overrides["track_project_names"] = args.project_mode
        cfg = Config.load(**overrides)
        cfg = Config(
            paths=cfg.paths,
            db_path=Path(args.db) if args.db else cfg.db_path,
            lookback_days=args.lookback,
            track_project_names=cfg.track_project_names,
            context=args.context if args.context else cfg.context,
            remote_stores=cfg.remote_stores,
        )

        since = date.today() - timedelta(days=cfg.lookback_days)
        pipeline, identity_store = _build_pipeline(cfg)
        stores = _build_stores(cfg)
        try:
            result = pipeline.since(since).stores(*stores).run()
        finally:
            if identity_store is not None:
                identity_store.close()

        for err in result.errors:
            print(f"Warning: {err}", file=sys.stderr)
        for err in result.stores_failed:
            print(f"Warning [store]: {err}", file=sys.stderr)

        stores[0].record_run()

        if len(stores) > 1:
            sync_result = run_sync(stores[0], stores[1:], dry_run=False)
            for store_name, info in sync_result.items():
                if info.get("pushed"):
                    print(f"Synced {info['pushed']} pending record(s) to {store_name}")

        print(
            f"Collected {result.records_written} session records "
            f"from {result.collectors_run} collectors "
            f"(since {since.isoformat()})"
        )
        return 0
