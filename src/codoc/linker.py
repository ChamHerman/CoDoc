from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Sequence

from codoc.models import CodeChunk, DocSection, LinkEdge

AMBIGUOUS_COMMON_NAMES: frozenset[str] = frozenset(
    {
        "get",
        "set",
        "run",
        "init",
        "config",
        "main",
        "setup",
        "create",
        "update",
        "delete",
        "list",
        "read",
        "write",
        "load",
        "save",
        "parse",
        "build",
        "check",
        "start",
        "stop",
        "open",
        "close",
        "data",
        "info",
        "value",
        "type",
        "id",
        "name",
        "item",
        "user",
        "client",
        "server",
        "app",
    }
)


@dataclass(slots=True)
class _EdgeMatchAccumulator:
    best_score: float = 0.0
    evidence: set[str] = field(default_factory=set)

    def add(self, score: float, evidence_item: str) -> None:
        if score > self.best_score:
            self.best_score = score
        self.evidence.add(evidence_item)


def _module_dotted_name(file_path: str) -> str:
    stem = file_path[:-3] if file_path.endswith(".py") else file_path
    dotted = stem.replace("/", ".")
    if dotted.endswith(".__init__"):
        dotted = dotted[: -len(".__init__")]
    return dotted

def _edge_sort_key(edge: LinkEdge) -> tuple[str, float, str]:
    return (edge.source_section_id, -edge.score, edge.target_chunk_id)

def _unique_match(
    index: dict[str, list[CodeChunk]],
    key: str,
) -> list[CodeChunk]:
    candidates = index.get(key, [])
    return candidates if len(candidates) == 1 else []




def build_heuristic_edges(
    sections: list[DocSection],
    chunks: list[CodeChunk],
) -> list[LinkEdge]:
    by_qualified: dict[str, list[CodeChunk]] = {}
    by_full_module_symbol: dict[str, list[CodeChunk]] = {}
    by_short_name: dict[str, list[CodeChunk]] = {}
    by_endpoint: dict[str, list[CodeChunk]] = {}
    by_schema_key: dict[str, list[CodeChunk]] = {}
    by_schema_qualified: dict[str, list[CodeChunk]] = {}
    by_cli_command: dict[str, list[CodeChunk]] = {}
    by_cli_option: dict[str, list[CodeChunk]] = {}

    for chunk in chunks:
        by_qualified.setdefault(chunk.qualified_name, []).append(chunk)
        mod = _module_dotted_name(chunk.file_path)
        by_full_module_symbol.setdefault(f"{mod}.{chunk.qualified_name}", []).append(
            chunk
        )
        if mod.startswith("src."):
            by_full_module_symbol.setdefault(
                f"{mod[4:]}.{chunk.qualified_name}", []
            ).append(chunk)
        leaf_mod = mod.rsplit(".", 1)[-1]
        by_full_module_symbol.setdefault(
            f"{leaf_mod}.{chunk.qualified_name}", []
        ).append(chunk)
        by_short_name.setdefault(chunk.symbol_name, []).append(chunk)
        if chunk.chunk_type == "endpoint":
            routes = chunk.metadata.get("routes")
            if isinstance(routes, list) and routes:
                for route in routes:
                    r_path = str(route.get("path", ""))
                    r_method = str(route.get("method", "")).upper()
                    if r_path:
                        by_endpoint.setdefault(r_path, []).append(chunk)
                        if r_method:
                            by_endpoint.setdefault(f"{r_method} {r_path}", []).append(
                                chunk
                            )
            else:
                route_path = chunk.metadata.get("route_path")
                methods = chunk.metadata.get("http_methods", ())
                if isinstance(route_path, str):
                    by_endpoint.setdefault(route_path, []).append(chunk)
                    for method in methods:
                        by_endpoint.setdefault(
                            f"{str(method).upper()} {route_path}", []
                        ).append(chunk)
        elif chunk.chunk_type == "schema":
            for key in chunk.metadata.get("config_keys", ()):
                key_str = str(key)
                by_schema_key.setdefault(key_str, []).append(chunk)
                by_schema_qualified.setdefault(
                    f"{chunk.qualified_name}.{key_str}", []
                ).append(chunk)
                by_schema_qualified.setdefault(
                    f"{mod}.{chunk.qualified_name}.{key_str}", []
                ).append(chunk)
                if mod.startswith("src."):
                    by_schema_qualified.setdefault(
                        f"{mod[4:]}.{chunk.qualified_name}.{key_str}", []
                    ).append(chunk)
                by_schema_qualified.setdefault(
                    f"{leaf_mod}.{chunk.qualified_name}.{key_str}", []
                ).append(chunk)
        elif chunk.chunk_type == "cli_command":
            cmd_name = chunk.metadata.get("command_name")
            if isinstance(cmd_name, str) and cmd_name:
                by_cli_command.setdefault(cmd_name, []).append(chunk)
            for sub in chunk.metadata.get("subcommands", ()):
                sub_str = str(sub)
                if sub_str and sub_str != cmd_name:
                    by_cli_command.setdefault(sub_str, []).append(chunk)
            for opt in chunk.metadata.get("cli_options", ()):
                by_cli_option.setdefault(str(opt), []).append(chunk)

    edges: list[LinkEdge] = []
    for section in sections:
        matches: dict[str, _EdgeMatchAccumulator] = {}

        for ref in section.references:
            target_chunks: list[CodeChunk] = []
            score = 0.0

            if ref.kind == "endpoint":
                target_chunks = by_endpoint.get(ref.normalized, [])
                score = 1.0 if " " in ref.normalized else 0.95
            elif ref.kind == "config_key":
                if ref.normalized.lower() in AMBIGUOUS_COMMON_NAMES:
                    continue
                target_chunks = _unique_match(by_schema_key, ref.normalized)
                score = 0.85
            elif ref.kind == "cli_command":
                if ref.normalized.startswith("-"):
                    target_chunks = _unique_match(by_cli_option, ref.normalized)
                    score = 0.85
                elif ref.normalized.lower() not in AMBIGUOUS_COMMON_NAMES:
                    target_chunks = _unique_match(by_cli_command, ref.normalized)
                    score = 0.95
            elif "." in ref.normalized:
                if ref.normalized in by_full_module_symbol:
                    target_chunks = by_full_module_symbol[ref.normalized]
                    score = 1.0
                elif ref.normalized in by_qualified:
                    target_chunks = by_qualified[ref.normalized]
                    score = 0.95
                elif ref.normalized in by_schema_qualified:
                    target_chunks = by_schema_qualified[ref.normalized]
                    score = 0.95
            elif ref.kind == "backtick":
                if ref.normalized.lower() in AMBIGUOUS_COMMON_NAMES:
                    continue
                target_chunks = (
                    _unique_match(by_short_name, ref.normalized)
                    or _unique_match(by_schema_key, ref.normalized)
                    or _unique_match(by_cli_command, ref.normalized)
                )
                score = 0.85
            for chunk in target_chunks:
                acc = matches.get(chunk.chunk_id)
                if acc is None:
                    acc = _EdgeMatchAccumulator()
                    matches[chunk.chunk_id] = acc
                acc.add(score, f"{ref.kind}:{ref.raw}")

        for chunk_id in sorted(matches):
            acc = matches[chunk_id]
            edges.append(
                LinkEdge(
                    source_section_id=section.section_id,
                    target_chunk_id=chunk_id,
                    link_type="heuristic",
                    score=acc.best_score,
                    evidence=tuple(sorted(acc.evidence)),
                )
            )

    edges.sort(key=_edge_sort_key)
    return edges


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    raw = dot / (norm_a * norm_b)
    return max(-1.0, min(1.0, raw))


def build_embedding_edges(
    sections: Sequence[DocSection],
    chunks: Sequence[CodeChunk],
    section_vectors: Sequence[Sequence[float]],
    chunk_vectors: Sequence[Sequence[float]],
    threshold: float = 0.50,
    top_k: int = 5,
) -> list[LinkEdge]:
    if top_k <= 0 or not sections or not chunks:
        return []
    edges: list[LinkEdge] = []
    for section, s_vec in zip(sections, section_vectors, strict=True):
        scored: list[tuple[float, str]] = []
        for chunk, c_vec in zip(chunks, chunk_vectors, strict=True):
            sim = cosine_similarity(s_vec, c_vec)
            if sim >= threshold:
                scored.append((round(sim, 6), chunk.chunk_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        for score, chunk_id in scored[:top_k]:
            edges.append(
                LinkEdge(
                    source_section_id=section.section_id,
                    target_chunk_id=chunk_id,
                    link_type="embedding",
                    score=score,
                    evidence=(f"cosine:{score:.4f}",),
                )
            )
    return edges


def merge_link_edges(
    heuristic_edges: Sequence[LinkEdge],
    embedding_edges: Sequence[LinkEdge],
) -> list[LinkEdge]:
    by_pair: dict[tuple[str, str], tuple[LinkEdge | None, LinkEdge | None]] = {}
    for h in heuristic_edges:
        key = (h.source_section_id, h.target_chunk_id)
        by_pair[key] = (h, None)
    for e in embedding_edges:
        key = (e.source_section_id, e.target_chunk_id)
        prev_h, _ = by_pair.get(key, (None, None))
        by_pair[key] = (prev_h, e)

    merged: list[LinkEdge] = []
    for (section_id, chunk_id), (h_edge, e_edge) in by_pair.items():
        if h_edge is not None and e_edge is not None:
            combined_score = round(
                min(1.0, max(h_edge.score, e_edge.score) + 0.1 * min(h_edge.score, e_edge.score)),
                6,
            )
            merged.append(
                LinkEdge(
                    source_section_id=section_id,
                    target_chunk_id=chunk_id,
                    link_type="hybrid",
                    score=combined_score,
                    evidence=tuple(sorted({*h_edge.evidence, *e_edge.evidence})),
                )
            )
        elif h_edge is not None:
            merged.append(h_edge)
        elif e_edge is not None:
            merged.append(e_edge)

    merged.sort(key=_edge_sort_key)
    return merged
