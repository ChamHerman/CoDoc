from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from codoc.indexer import build_index
from codoc.models import DEFAULT_IGNORE_GLOBS, IndexConfig


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codoc")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser(
        "index",
        help="Build Code-to-Docs LinkGraph index for a repository.",
    )
    index_parser.add_argument("repo_root", type=Path, help="Path to target repository")
    index_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output LinkGraph JSON path (defaults outside repository root)",
    )
    index_parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="Additional ignore glob patterns",
    )

    args = parser.parse_args(argv)
    if args.command == "index":
        cfg = IndexConfig(
            ignore_globs=DEFAULT_IGNORE_GLOBS + tuple(args.ignore),
        )
        _, written_path = build_index(
            args.repo_root,
            config=cfg,
            output_path=args.output,
        )
        print(str(written_path))
        return 0
    return 1
