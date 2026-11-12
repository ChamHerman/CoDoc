from __future__ import annotations

import json
from pathlib import Path
import tempfile

from codoc.code_parser import parse_python_repo
from codoc.doc_parser import parse_markdown_repo
from codoc.embeddings import (
    EmbeddingProvider,
    format_chunk_for_embedding,
    format_section_for_embedding,
)
from codoc.linker import (
    build_embedding_edges,
    build_heuristic_edges,
    merge_link_edges,
)
from codoc.models import IndexConfig, LinkGraph, sha256_text


def default_output_path(repo_root: Path) -> Path:
    repo_hash = sha256_text(str(repo_root.resolve()))[:16]
    return Path(tempfile.gettempdir()) / "codoc" / repo_hash / "link_graph.json"


def build_index(
    repo_root: Path,
    config: IndexConfig | None = None,
    output_path: Path | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> tuple[LinkGraph, Path]:
    resolved_root = repo_root.resolve()
    cfg = config or IndexConfig()

    chunks = sorted(
        parse_python_repo(resolved_root, cfg.ignore_globs),
        key=lambda c: c.chunk_id,
    )
    sections = sorted(
        parse_markdown_repo(resolved_root, cfg.ignore_globs),
        key=lambda s: s.section_id,
    )
    heuristic_edges = build_heuristic_edges(sections, chunks)

    if embedding_provider is not None and chunks and sections:
        chunk_texts = [format_chunk_for_embedding(c) for c in chunks]
        section_texts = [format_section_for_embedding(s) for s in sections]
        chunk_vectors = embedding_provider.embed_texts(chunk_texts)
        section_vectors = embedding_provider.embed_texts(section_texts)
        embedding_edges = build_embedding_edges(
            sections=sections,
            chunks=chunks,
            section_vectors=section_vectors,
            chunk_vectors=chunk_vectors,
            threshold=cfg.embedding_threshold,
            top_k=cfg.embedding_top_k,
        )
        edges = merge_link_edges(heuristic_edges, embedding_edges)
    else:
        edges = heuristic_edges

    graph = LinkGraph(
        code_chunks=tuple(chunks),
        doc_sections=tuple(sections),
        edges=tuple(edges),
    )

    target_path = (
        output_path if output_path is not None else default_output_path(resolved_root)
    )
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps(graph.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return graph, target_path
