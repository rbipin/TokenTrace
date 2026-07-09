"""The `projects` subcommand: list local project identities."""
from __future__ import annotations

import argparse
from pathlib import Path

from src.config import Config
from src.project_identity import ProjectIdentityStore


class ProjectsCommand:
    name = "projects"
    help = "list local project identities (cwd -> guid -> whimsical)"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        pass

    def run(self, args: argparse.Namespace) -> int:
        cfg = Config.load()
        db_path = Path(args.db) if args.db else cfg.db_path
        store = ProjectIdentityStore(db_path)
        try:
            rows = store.list_identities()
        finally:
            store.close()

        if not rows:
            print("No project identities recorded yet. Run: python tracker.py collect")
            return 0

        header = f"{'Cwd':<50} {'Guid':<14} {'Whimsical':<24} Created"
        print(header)
        print("-" * len(header))
        for r in rows:
            print(
                f"{r['cwd_key']:<50} {r['guid']:<14} "
                f"{r['whimsical_name'] or '-':<24} {r['created_at']}"
            )
        return 0
