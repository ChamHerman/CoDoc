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
from codoc.models import CodeChunk, IndexConfig, LinkGraph, sha256_text
from codoc.vector_store import ChromaVectorStore, VectorEntry


def default_state_dir(repo_root: Path) -> Path:
    repo_hash = sha256_text(str(repo_root.resolve()))[:16]
    return Path(tempfile.gettempdir()) / "codoc" / repo_hash


def default_output_path(repo_root: Path) -> Path:
    return default_state_dir(repo_root) / "link_graph.json"


def default_chroma_dir(repo_root: Path) -> Path:
    return default_state_dir(repo_root) / "chroma"


def _dedupe_chunks(raw_chunks: list[CodeChunk]) -> list[CodeChunk]:
    by_id: dict[str, CodeChunk] = {}
    for chunk in raw_chunks:
        by_id[chunk.chunk_id] = chunk
    return sorted(by_id.values(), key=lambda c: c.chunk_id)


def _resolve_external_chroma_dir(repo_root: Path, chroma_dir: Path | None) -> Path:
    resolved = (
        chroma_dir.resolve()
        if chroma_dir is not None
        else default_chroma_dir(repo_root)
    )
    if resolved.is_relative_to(repo_root):
        raise ValueError(
            f"ChromaDB cache directory must be outside repository root: {resolved}"
        )
    return resolved


def build_index(
    repo_root: Path,
    config: IndexConfig | None = None,
    output_path: Path | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    chroma_dir: Path | None = None,
) -> tuple[LinkGraph, Path]:
    resolved_root = repo_root.resolve()
    cfg = config or IndexConfig()

    chunks = _dedupe_chunks(parse_python_repo(resolved_root, cfg.ignore_globs))
    sections = sorted(
        parse_markdown_repo(resolved_root, cfg.ignore_globs),
        key=lambda s: s.section_id,
    )
    heuristic_edges = build_heuristic_edges(sections, chunks)

    resolved_chroma = _resolve_external_chroma_dir(resolved_root, chroma_dir)
    if embedding_provider is not None:
        store = ChromaVectorStore(resolved_chroma)
        chunk_vectors = store.sync_embeddings(
            ChromaVectorStore.CODE_COLLECTION,
            [
                VectorEntry(
                    entry_id=c.chunk_id,
                    content_hash=c.content_hash,
                    text=format_chunk_for_embedding(c),
                )
                for c in chunks
            ],
            embedding_provider,
        )
        section_vectors = store.sync_embeddings(
            ChromaVectorStore.DOCS_COLLECTION,
            [
                VectorEntry(
                    entry_id=s.section_id,
                    content_hash=s.content_hash,
                    text=format_section_for_embedding(s),
                )
                for s in sections
            ],
            embedding_provider,
        )
        if chunks and sections:
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
    else:
        if resolved_chroma.exists():
            store = ChromaVectorStore(resolved_chroma)
            store.prune_stale(
                ChromaVectorStore.CODE_COLLECTION,
                {c.chunk_id for c in chunks},
            )
            store.prune_stale(
                ChromaVectorStore.DOCS_COLLECTION,
                {s.section_id for s in sections},
            )
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
