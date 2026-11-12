from __future__ import annotations

from dataclasses import dataclass, field

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


def build_heuristic_edges(
    sections: list[DocSection],
    chunks: list[CodeChunk],
) -> list[LinkEdge]:
    by_qualified: dict[str, list[CodeChunk]] = {}
    by_full_module_symbol: dict[str, list[CodeChunk]] = {}
    by_short_name: dict[str, list[CodeChunk]] = {}

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

    edges: list[LinkEdge] = []
    for section in sections:
        matches: dict[str, _EdgeMatchAccumulator] = {}

        for ref in section.references:
            target_chunks: list[CodeChunk] = []
            score = 0.0

            if "." in ref.normalized:
                if ref.normalized in by_full_module_symbol:
                    target_chunks = by_full_module_symbol[ref.normalized]
                    score = 1.0
                elif ref.normalized in by_qualified:
                    target_chunks = by_qualified[ref.normalized]
                    score = 0.95
            elif ref.kind == "backtick":
                lowered = ref.normalized.lower()
                if lowered in AMBIGUOUS_COMMON_NAMES:
                    continue
                candidates = by_short_name.get(ref.normalized, [])
                if len(candidates) == 1:
                    target_chunks = candidates
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

    edges.sort(key=lambda e: (e.source_section_id, e.target_chunk_id))
    return edges
