from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Sequence

from codoc.embeddings import EmbeddingProvider, OpenAIEmbeddingProvider
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
        "--chroma-dir",
        type=Path,
        default=None,
        help="ChromaDB cache directory path (must be outside repository root)",
    )
    index_parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="Additional ignore glob patterns",
    )
    index_parser.add_argument(
        "--embedding-model",
        default="text-embedding-3-small",
        help="Embedding model name (default: text-embedding-3-small)",
    )
    index_parser.add_argument(
        "--embedding-base-url",
        default=None,
        help="Optional OpenAI-compatible base URL for embeddings",
    )
    index_parser.add_argument(
        "--embedding-api-key",
        default=None,
        help="Embedding API key (defaults to EMBEDDING_API_KEY or OPENAI_API_KEY)",
    )
    index_parser.add_argument(
        "--embedding-threshold",
        type=float,
        default=0.50,
        help="Minimum cosine similarity threshold (default: 0.50)",
    )
    index_parser.add_argument(
        "--embedding-top-k",
        type=int,
        default=5,
        help="Max embedding edges per DocSection (default: 5)",
    )
    index_parser.add_argument(
        "--no-embeddings",
        action="store_true",
        help="Run heuristic linking only without calling an embedding provider",
    )

    args = parser.parse_args(argv)
    if args.command == "index":
        cfg = IndexConfig(
            ignore_globs=DEFAULT_IGNORE_GLOBS + tuple(args.ignore),
            embedding_threshold=args.embedding_threshold,
            embedding_top_k=args.embedding_top_k,
        )
        api_key = (
            args.embedding_api_key
            if args.embedding_api_key is not None
            else (
                os.environ.get("EMBEDDING_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
            )
        )
        provider: EmbeddingProvider | None = None
        if not args.no_embeddings and (
            api_key is not None or args.embedding_base_url is not None
        ):
            provider = OpenAIEmbeddingProvider(
                api_key=api_key,
                model=args.embedding_model,
                base_url=args.embedding_base_url,
            )

        _, written_path = build_index(
            args.repo_root,
            config=cfg,
            output_path=args.output,
            embedding_provider=provider,
            chroma_dir=args.chroma_dir,
        )
        print(str(written_path))
        return 0
    return 1
