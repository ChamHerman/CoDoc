from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest
from types import SimpleNamespace
from typing import cast

from openai import OpenAI

from codoc.embeddings import (
    OpenAIEmbeddingProvider,
    format_chunk_for_embedding,
    format_section_for_embedding,
)
from codoc.indexer import build_index
from codoc.models import IndexConfig


class FakeEmbeddingProvider:
    """Deterministic keyword-projection embedder for offline semantic tests."""

    def __init__(self, topic_vectors: dict[str, list[float]]) -> None:
        self.topic_vectors = topic_vectors
        self.calls: list[list[str]] = []

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.lower()
            matched: list[float] | None = None
            for keyword, vec in self.topic_vectors.items():
                if keyword in lowered:
                    matched = list(vec)
                    break
            vectors.append(matched if matched is not None else [0.0, 0.0, 1.0])
        return vectors


def test_build_index_adds_top_k_embedding_edges_above_threshold(tmp_path: Path) -> None:
    repo = tmp_path / "semantic_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    (repo / "src" / "rewards.py").write_text(
        '''def compute_loyalty_rebate(points: int) -> float:
    """Compute customer rebate from loyalty points."""
    return points * 0.02


def tier_alpha(x: int) -> int:
    """Alpha bonus tier."""
    return x


def tier_beta(x: int) -> int:
    """Beta bonus tier."""
    return x


def tier_gamma(x: int) -> int:
    """Gamma bonus tier."""
    return x


def tier_delta(x: int) -> int:
    """Delta bonus tier."""
    return x


def tier_epsilon(x: int) -> int:
    """Epsilon bonus tier."""
    return x


def archive_logs(path: str) -> None:
    """Compress historical audit logs."""
    return None
''',
        encoding="utf-8",
    )

    (repo / "docs" / "rewards.md").write_text(
        """# Customer Perks

## Loyalty Savings

Customers receive automatic tier discounts when redeeming reward points.
""",
        encoding="utf-8",
    )

    # Cosine similarity vs Loyalty Savings vector [1.0, 0.0, 0.0]:
    # compute_loyalty_rebate -> 0.99 (kept #1)
    # tier_alpha..epsilon    -> 0.85, 0.80, 0.75, 0.70, 0.65 (top 5 total keeps 4 of these; 5th truncated by k=5)
    # archive_logs           -> 0.20 (below 0.50 threshold)
    provider = FakeEmbeddingProvider(
        topic_vectors={
            "loyalty savings": [1.0, 0.0, 0.0],
            "compute_loyalty_rebate": [0.99, 0.14, 0.0],
            "tier_alpha": [0.85, 0.53, 0.0],
            "tier_beta": [0.80, 0.60, 0.0],
            "tier_gamma": [0.75, 0.66, 0.0],
            "tier_delta": [0.70, 0.71, 0.0],
            "tier_epsilon": [0.65, 0.76, 0.0],
            "archive_logs": [0.20, 0.98, 0.0],
        }
    )

    out_path = tmp_path / "link_graph.json"
    cfg = IndexConfig(embedding_threshold=0.50, embedding_top_k=5)
    graph, _ = build_index(
        repo_root=repo,
        config=cfg,
        output_path=out_path,
        embedding_provider=provider,
    )

    loyalty_sec = next(
        s for s in graph.doc_sections if s.heading_path == "Customer Perks > Loyalty Savings"
    )
    loyalty_edges = [e for e in graph.edges if e.source_section_id == loyalty_sec.section_id]

    assert len(loyalty_edges) == 5
    assert all(e.link_type == "embedding" for e in loyalty_edges)
    assert all(e.score >= 0.50 for e in loyalty_edges)

    target_ids = [e.target_chunk_id for e in loyalty_edges]
    assert target_ids[0] == "src/rewards.py::compute_loyalty_rebate"
    assert "src/rewards.py::tier_epsilon" not in target_ids
    assert "src/rewards.py::archive_logs" not in target_ids

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert len(payload["edges"]) == 5
    assert len(provider.calls) == 2


def test_build_index_merges_overlapping_heuristic_and_embedding_edges_into_hybrid(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "hybrid_repo"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)

    (repo / "src" / "payments.py").write_text(
        '''def charge_card(amount_cents: int) -> bool:
    """Charge a customer payment card."""
    return amount_cents > 0


def refund_card(tx_id: str) -> bool:
    """Issue a refund for a settled card transaction."""
    return bool(tx_id)


def export_ledger() -> str:
    """Export nightly general ledger."""
    return ""
''',
        encoding="utf-8",
    )

    # Mentions charge_card explicitly (heuristic + embedding -> hybrid),
    # describes refund flow semantically (embedding-only),
    # and explicitly mentions export_ledger with orthogonal vector (heuristic-only).
    (repo / "docs" / "payments.md").write_text(
        """# Payments

## Card Operations

Use `charge_card` to capture funds or reverse settled charges when needed.

## Accounting

Call `export_ledger` at midnight.
""",
        encoding="utf-8",
    )

    provider = FakeEmbeddingProvider(
        topic_vectors={
            "card operations": [1.0, 0.0, 0.0],
            "charge_card": [0.95, 0.31, 0.0],
            "refund_card": [0.90, 0.44, 0.0],
            "accounting": [0.0, 1.0, 0.0],
            "export_ledger": [0.0, 0.0, 1.0],
        }
    )

    out_path = tmp_path / "hybrid.json"
    graph, _ = build_index(
        repo_root=repo,
        config=IndexConfig(embedding_threshold=0.50, embedding_top_k=5),
        output_path=out_path,
        embedding_provider=provider,
    )

    sections_by_path = {s.heading_path: s for s in graph.doc_sections}
    card_sec = sections_by_path["Payments > Card Operations"].section_id
    acct_sec = sections_by_path["Payments > Accounting"].section_id

    card_edges = {
        e.target_chunk_id: e for e in graph.edges if e.source_section_id == card_sec
    }
    assert set(card_edges) == {
        "src/payments.py::charge_card",
        "src/payments.py::refund_card",
    }

    hybrid_edge = card_edges["src/payments.py::charge_card"]
    assert hybrid_edge.link_type == "hybrid"
    assert any(ev.startswith("backtick:") for ev in hybrid_edge.evidence)
    assert any(ev.startswith("cosine:") for ev in hybrid_edge.evidence)
    assert hybrid_edge.score >= 0.85

    emb_only_edge = card_edges["src/payments.py::refund_card"]
    assert emb_only_edge.link_type == "embedding"

    acct_edges = [e for e in graph.edges if e.source_section_id == acct_sec]
    assert len(acct_edges) == 1
    assert acct_edges[0].link_type == "heuristic"
    assert acct_edges[0].target_chunk_id == "src/payments.py::export_ledger"

    persisted = json.loads(out_path.read_text(encoding="utf-8"))
    persisted_hybrid = [
        e
        for e in persisted["edges"]
        if e["source_section_id"] == card_sec
        and e["target_chunk_id"] == "src/payments.py::charge_card"
    ]
    assert len(persisted_hybrid) == 1
    assert persisted_hybrid[0]["link_type"] == "hybrid"


def test_openai_embedding_provider_batches_orders_and_respects_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:


    recorded_calls: list[tuple[str, list[str]]] = []

    class _StubEmbeddingsAPI:
        def create(self, *, model: str, input: list[str]) -> SimpleNamespace:
            recorded_calls.append((model, list(input)))
            # Return items reversed to verify index sorting
            items = [
                SimpleNamespace(index=idx, embedding=[float(idx + 1), 0.5])
                for idx in reversed(range(len(input)))
            ]
            return SimpleNamespace(data=items)

    stub_client = cast(OpenAI, SimpleNamespace(embeddings=_StubEmbeddingsAPI()))
    provider = OpenAIEmbeddingProvider(client=stub_client, batch_size=2)

    assert provider.model == "text-embedding-3-small"
    assert provider.embed_texts([]) == []
    assert recorded_calls == []

    vecs = provider.embed_texts(["alpha", "beta", "gamma"])
    assert len(recorded_calls) == 2
    assert recorded_calls[0] == ("text-embedding-3-small", ["alpha", "beta"])
    assert recorded_calls[1] == ("text-embedding-3-small", ["gamma"])
    assert vecs == [[1.0, 0.5], [2.0, 0.5], [1.0, 0.5]]
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    explicit_provider = OpenAIEmbeddingProvider(
        api_key="explicit-key",
        base_url="http://localhost:8000/v1",
    )
    client = explicit_provider._get_client()
    assert client.api_key == "explicit-key"
    assert str(client.base_url).rstrip("/") == "http://localhost:8000/v1"


def test_embedding_formatters_avoid_duplicating_headers_and_bound_length(
    tmp_path: Path,
) -> None:

    repo = tmp_path / "large_repo"
    repo.mkdir()
    huge_body = "x = 1\n" * 1000
    (repo / "big.py").write_text(
        f'def big_fn() -> None:\n    """Summary."""\n    {"    ".join(huge_body.splitlines(True))}\n',
        encoding="utf-8",
    )
    (repo / "README.md").write_text(
        "# Big Doc\n\n" + ("word " * 1500),
        encoding="utf-8",
    )
    graph, _ = build_index(repo_root=repo, output_path=tmp_path / "big.json")
    chunk_text = format_chunk_for_embedding(graph.code_chunks[0])
    sec_text = format_section_for_embedding(graph.doc_sections[0])
    assert chunk_text.count("def big_fn() -> None") == 1
    assert chunk_text.count("Summary.") == 1
    assert "x = 1" in chunk_text
    assert len(chunk_text) < 1600
    assert sec_text.startswith("Big Doc\nword")
    assert len(sec_text) < 2100


def test_build_index_raises_when_embedding_provider_returns_mismatched_count(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "bad_provider_repo"
    repo.mkdir()
    (repo / "mod.py").write_text("def fn_one() -> None:\n    pass\n", encoding="utf-8")
    (repo / "README.md").write_text("# Doc\n\nText.\n", encoding="utf-8")

    class _BrokenProvider:
        def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
            return []

    with pytest.raises(ValueError):
        build_index(
            repo_root=repo,
            output_path=tmp_path / "bad.json",
            embedding_provider=_BrokenProvider(),
        )
