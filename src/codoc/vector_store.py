from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import chromadb
from chromadb.config import Settings

from codoc.embeddings import EmbeddingProvider


@dataclass(frozen=True, slots=True)
class VectorEntry:
    entry_id: str
    content_hash: str
    text: str


@dataclass(frozen=True, slots=True)
class _CachedVector:
    content_hash: str
    vector: list[float]


class ChromaVectorStore:
    """File-based ChromaDB store keyed incrementally by content_hash."""

    CODE_COLLECTION = "code_chunks"
    DOCS_COLLECTION = "doc_sections"

    def __init__(self, persist_dir: Path) -> None:
        self.persist_dir = persist_dir.resolve()
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )

    def prune_stale(self, collection_name: str, valid_ids: set[str]) -> None:
        existing_names = {col.name for col in self._client.list_collections()}
        if collection_name not in existing_names:
            return
        collection = self._client.get_collection(name=collection_name)
        existing_ids = list(collection.get(include=[]).get("ids") or [])
        stale_ids = sorted(set(existing_ids) - valid_ids)
        if stale_ids:
            collection.delete(ids=stale_ids)

    def sync_embeddings(
        self,
        collection_name: str,
        entries: Sequence[VectorEntry],
        provider: EmbeddingProvider,
    ) -> list[list[float]]:
        collection = self._client.get_or_create_collection(name=collection_name)
        existing = collection.get(include=["metadatas", "embeddings"])
        existing_ids: list[str] = list(existing.get("ids") or [])
        existing_metas: list[object] = list(existing.get("metadatas") or [])
        raw_embeddings = existing.get("embeddings")
        existing_vecs: list[object] = (
            list(raw_embeddings) if raw_embeddings is not None else []
        )

        cached_by_id: dict[str, _CachedVector] = {}
        existing_dim: int | None = None
        for idx, item_id in enumerate(existing_ids):
            meta = existing_metas[idx] if idx < len(existing_metas) else None
            stored_hash = (
                str(meta.get("content_hash", "")) if isinstance(meta, dict) else ""
            )
            raw_vec = existing_vecs[idx] if idx < len(existing_vecs) else None
            if raw_vec is not None and hasattr(raw_vec, "__iter__"):
                vec = [float(x) for x in raw_vec]
                if vec:
                    if existing_dim is None:
                        existing_dim = len(vec)
                    cached_by_id[item_id] = _CachedVector(
                        content_hash=stored_hash,
                        vector=vec,
                    )

        desired_ids = {entry.entry_id for entry in entries}
        stale_ids = sorted(set(existing_ids) - desired_ids)
        if stale_ids:
            collection.delete(ids=stale_ids)

        aligned_vectors: list[list[float] | None] = [None] * len(entries)
        misses: list[tuple[int, VectorEntry]] = []

        for idx, entry in enumerate(entries):
            cached = cached_by_id.get(entry.entry_id)
            if cached is not None and cached.content_hash == entry.content_hash:
                aligned_vectors[idx] = cached.vector
            else:
                misses.append((idx, entry))

        if misses:
            miss_texts = [entry.text for _, entry in misses]
            new_vectors = provider.embed_texts(miss_texts)
            if len(new_vectors) != len(misses):
                raise ValueError(
                    f"Embedding provider returned {len(new_vectors)} vectors for {len(misses)} entries"
                )
            new_dim = len(new_vectors[0]) if new_vectors else None
            # ChromaDB locks collection dimensionality on first insert; recreate on dimension change.
            if (
                existing_dim is not None
                and new_dim is not None
                and existing_dim != new_dim
                and len(misses) < len(entries)
            ):
                self._client.delete_collection(name=collection_name)
                collection = self._client.get_or_create_collection(name=collection_name)
                all_vectors = provider.embed_texts([e.text for e in entries])
                if len(all_vectors) != len(entries):
                    raise ValueError(
                        f"Embedding provider returned {len(all_vectors)} vectors for {len(entries)} entries"
                    )
                reset_vecs: list[Sequence[float]] = [[float(x) for x in v] for v in all_vectors]
                collection.upsert(
                    ids=[e.entry_id for e in entries],
                    embeddings=reset_vecs,
                    metadatas=[{"content_hash": e.content_hash} for e in entries],
                )
                return [[float(x) for x in v] for v in reset_vecs]

            if (
                existing_dim is not None
                and new_dim is not None
                and existing_dim != new_dim
            ):
                self._client.delete_collection(name=collection_name)
                collection = self._client.get_or_create_collection(name=collection_name)

            upsert_ids: list[str] = []
            upsert_vecs: list[Sequence[float]] = []
            upsert_metas: list[dict[str, str | int | float | bool]] = []
            for (pos, entry), vec in zip(misses, new_vectors, strict=True):
                float_vec = [float(x) for x in vec]
                aligned_vectors[pos] = float_vec
                upsert_ids.append(entry.entry_id)
                upsert_vecs.append(float_vec)
                upsert_metas.append({"content_hash": entry.content_hash})

            collection.upsert(
                ids=upsert_ids,
                embeddings=upsert_vecs,
                metadatas=upsert_metas,  # type: ignore[arg-type]
            )

        return [vec if vec is not None else [] for vec in aligned_vectors]
