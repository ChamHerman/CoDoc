from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence

import chromadb
from chromadb.api import ClientAPI
from chromadb.config import Settings
import pytest

from codoc.indexer import build_index
from codoc.models import IndexConfig


class CountingFakeEmbeddingProvider:
    """Deterministic embedder recording every text sent for embedding."""

    def __init__(self, topic_vectors: dict[str, list[float]]) -> None:
        self.topic_vectors = topic_vectors
        self.embedded_texts: list[str] = []
        self.call_count: int = 0

    def reset_stats(self) -> None:
        self.embedded_texts.clear()
        self.call_count = 0

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.call_count += 1
        vectors: list[list[float]] = []
        for text in texts:
            self.embedded_texts.append(text)
            lowered = text.lower()
            matched: list[float] | None = None
            for keyword, vec in self.topic_vectors.items():
                if keyword in lowered:
                    matched = list(vec)
                    break
            vectors.append(matched if matched is not None else [0.0, 0.0, 1.0])
        return vectors


def _open_chroma_client(chroma_dir: Path) -> ClientAPI:
    return chromadb.PersistentClient(
        path=str(chroma_dir),
        settings=Settings(anonymized_telemetry=False),
    )


def test_build_index_persists_chromadb_and_skips_reembedding_unchanged_items(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "cached_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    (repo / "src" / "billing.py").write_text(
        '''def process_refund(order_id: str) -> bool:
    """Issue a refund for an order."""
    return bool(order_id)


def send_receipt(email: str) -> None:
    """Email invoice receipt to customer."""
    return None
''',
        encoding="utf-8",
    )
    (repo / "docs" / "billing.md").write_text(
        """# Billing

## Refunds

Reverse a payment cleanly when a customer cancels an order.
""",
        encoding="utf-8",
    )

    provider = CountingFakeEmbeddingProvider(
        topic_vectors={
            "refunds": [1.0, 0.0, 0.0],
            "process_refund": [0.96, 0.28, 0.0],
            "send_receipt": [0.10, 0.99, 0.0],
        }
    )

    chroma_dir = tmp_path / "cache" / "chroma"
    out_path = tmp_path / "cache" / "link_graph.json"
    cfg = IndexConfig(embedding_threshold=0.50, embedding_top_k=5)

    first_graph, _ = build_index(
        repo_root=repo,
        config=cfg,
        output_path=out_path,
        embedding_provider=provider,
        chroma_dir=chroma_dir,
    )

    assert chroma_dir.exists()
    assert not any(repo.rglob("chroma*"))
    assert len(provider.embedded_texts) == 4  # 2 CodeChunks + 2 DocSections
    assert {e.target_chunk_id for e in first_graph.edges} == {
        "src/billing.py::process_refund"
    }

    client = _open_chroma_client(chroma_dir)
    chunk_stored = client.get_collection("code_chunks").get(
        include=["metadatas", "embeddings"]
    )
    assert set(chunk_stored["ids"]) == {
        "src/billing.py::process_refund",
        "src/billing.py::send_receipt",
    }
    assert all("content_hash" in (m or {}) for m in (chunk_stored["metadatas"] or []))

    doc_stored = client.get_collection("doc_sections").get(
        include=["metadatas", "embeddings"]
    )
    assert set(doc_stored["ids"]) == {
        "docs/billing.md#Billing",
        "docs/billing.md#Billing > Refunds",
    }
    assert all("content_hash" in (m or {}) for m in (doc_stored["metadatas"] or []))

    # Second run with no changes: 0 calls to embedding provider, identical graph
    provider.reset_stats()
    second_graph, _ = build_index(
        repo_root=repo,
        config=cfg,
        output_path=out_path,
        embedding_provider=provider,
        chroma_dir=chroma_dir,
    )

    assert provider.call_count == 0
    assert provider.embedded_texts == []
    assert second_graph.to_dict() == first_graph.to_dict()


def test_build_index_reembeds_only_changed_items_and_updates_edges(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "partial_edit_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    py_file = repo / "src" / "jobs.py"
    py_file.write_text(
        '''def export_csv() -> str:
    """Export table rows as CSV."""
    return ""


def sync_ledger() -> None:
    """Reconcile accounting ledger."""
    return None
''',
        encoding="utf-8",
    )
    (repo / "docs" / "jobs.md").write_text(
        """## Ledger Sync

Reconcile accounting ledger nightly.
""",
        encoding="utf-8",
    )

    provider = CountingFakeEmbeddingProvider(
        topic_vectors={
            "ledger sync": [1.0, 0.0, 0.0],
            "sync_ledger": [0.95, 0.31, 0.0],
            "reconcile_v2": [0.99, 0.14, 0.0],
            "export_csv": [0.10, 0.99, 0.0],
        }
    )

    chroma_dir = tmp_path / "chroma"
    out_path = tmp_path / "link_graph.json"
    cfg = IndexConfig(embedding_threshold=0.50, embedding_top_k=5)

    build_index(
        repo_root=repo,
        config=cfg,
        output_path=out_path,
        embedding_provider=provider,
        chroma_dir=chroma_dir,
    )
    assert len(provider.embedded_texts) == 3

    provider.reset_stats()
    py_file.write_text(
        '''def export_csv() -> str:
    """Export reconcile_v2 ledger entries as CSV."""
    return "v2"


def sync_ledger() -> None:
    """Reconcile accounting ledger."""
    return None
''',
        encoding="utf-8",
    )

    updated_graph, _ = build_index(
        repo_root=repo,
        config=cfg,
        output_path=out_path,
        embedding_provider=provider,
        chroma_dir=chroma_dir,
    )

    assert provider.call_count == 1
    assert len(provider.embedded_texts) == 1
    assert "export_csv" in provider.embedded_texts[0]

    edge_targets = {e.target_chunk_id for e in updated_graph.edges}
    assert edge_targets == {
        "src/jobs.py::export_csv",
        "src/jobs.py::sync_ledger",
    }
    persisted = json.loads(out_path.read_text(encoding="utf-8"))
    assert {e["target_chunk_id"] for e in persisted["edges"]} == edge_targets


def test_build_index_prunes_deleted_chunks_and_sections_from_chromadb_and_json(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "prune_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    py_file = repo / "src" / "api.py"
    py_file.write_text(
        '''def keep_me() -> None:
    pass


def remove_me() -> None:
    pass
''',
        encoding="utf-8",
    )
    md_file = repo / "docs" / "api.md"
    md_file.write_text(
        """## Keep Section

Call `keep_me`.

## Remove Section

Call `remove_me`.
""",
        encoding="utf-8",
    )

    provider = CountingFakeEmbeddingProvider(
        topic_vectors={
            "keep": [1.0, 0.0, 0.0],
            "remove": [0.0, 1.0, 0.0],
        }
    )
    chroma_dir = tmp_path / "chroma"
    out_path = tmp_path / "link_graph.json"

    build_index(
        repo_root=repo,
        output_path=out_path,
        embedding_provider=provider,
        chroma_dir=chroma_dir,
    )

    provider.reset_stats()
    py_file.write_text("def keep_me() -> None:\n    pass\n", encoding="utf-8")
    md_file.write_text("## Keep Section\n\nCall `keep_me`.\n", encoding="utf-8")

    pruned_graph, _ = build_index(
        repo_root=repo,
        output_path=out_path,
        embedding_provider=provider,
        chroma_dir=chroma_dir,
    )

    assert provider.call_count == 0
    assert [c.chunk_id for c in pruned_graph.code_chunks] == ["src/api.py::keep_me"]
    assert [s.section_id for s in pruned_graph.doc_sections] == [
        "docs/api.md#Keep Section"
    ]
    persisted = json.loads(out_path.read_text(encoding="utf-8"))
    assert [c["chunk_id"] for c in persisted["code_chunks"]] == ["src/api.py::keep_me"]
    assert [s["section_id"] for s in persisted["doc_sections"]] == [
        "docs/api.md#Keep Section"
    ]

    client = _open_chroma_client(chroma_dir)
    assert client.get_collection("code_chunks").get()["ids"] == ["src/api.py::keep_me"]
    assert client.get_collection("doc_sections").get()["ids"] == [
        "docs/api.md#Keep Section"
    ]

    # Deleting remaining files with heuristic-only run still prunes ChromaDB collections
    py_file.unlink()
    md_file.unlink()
    empty_graph, _ = build_index(
        repo_root=repo,
        output_path=out_path,
        embedding_provider=None,
        chroma_dir=chroma_dir,
    )
    assert empty_graph.code_chunks == ()
    assert empty_graph.doc_sections == ()
    assert empty_graph.edges == ()
    assert client.get_collection("code_chunks").get()["ids"] == []
    assert client.get_collection("doc_sections").get()["ids"] == []


def test_build_index_rejects_chroma_dir_inside_repo_and_handles_overloads_and_cli(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "guarded_repo"
    repo.mkdir()
    (repo / "mod.py").write_text(
        """from typing import overload

@overload
def parse_val(x: int) -> int: ...

@overload
def parse_val(x: str) -> str: ...

def parse_val(x: int | str) -> int | str:
    return x
""",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Docs\n\nUse `parse_val`.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="outside repository root"):
        build_index(repo_root=repo, chroma_dir=repo / ".chroma")

    chroma_dir = tmp_path / "external_chroma"
    out_path = tmp_path / "out.json"
    provider = CountingFakeEmbeddingProvider({"parse_val": [1.0, 0.0]})
    graph, _ = build_index(
        repo_root=repo,
        output_path=out_path,
        embedding_provider=provider,
        chroma_dir=chroma_dir,
    )
    assert [c.chunk_id for c in graph.code_chunks] == ["mod.py::parse_val"]

    res = subprocess.run(
        [
            sys.executable,
            "-m",
            "codoc",
            "index",
            str(repo),
            "--output",
            str(out_path),
            "--chroma-dir",
            str(chroma_dir),
            "--no-embeddings",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
