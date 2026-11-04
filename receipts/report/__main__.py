from __future__ import annotations

import argparse
from pathlib import Path

from . import render_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a Receipts run as a static HTML report.")
    parser.add_argument("run_dir", type=Path, help="path to a run directory")
    parser.add_argument("--db", type=Path, default=None, help="SQLite audit database path")
    args = parser.parse_args()
    print(render_report(args.run_dir, args.db))


if __name__ == "__main__":
    main()
