"""The `sync` subcommand: push unsynced records to remote stores."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.commands.common import load_remote_stores
from src.config import Config
from src.stores.sqlite import SqliteStore


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
            store.close()
            continue
        try:
            if pending:
                store.upsert(pending)
                sqlite_store.mark_synced(pending, store.name)
            result[store.name] = {"pushed": len(pending), "failed": False}
        except Exception as exc:
            print(f"Warning [{store.name}]: {exc}", file=sys.stderr)
            result[store.name] = {"pushed": 0, "failed": True, "error": str(exc)}
        finally:
            try:
                store.close()
            except Exception:
                pass
    return result


class SyncCommand:
    name = "sync"
    help = "push unsynced records to remote stores"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--dry-run", action="store_true",
                            help="show pending counts without pushing")

    def run(self, args: argparse.Namespace) -> int:
        cfg = Config.load()
        db_path = Path(args.db) if args.db else cfg.db_path

        if not cfg.remote_stores:
            print("No remote stores configured. Add [stores.X] sections to "
                  "~/.tokentracer/.tokentracer.toml")
            return 0

        sqlite_store = SqliteStore(db_path)
        remote_stores = load_remote_stores(cfg)

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
