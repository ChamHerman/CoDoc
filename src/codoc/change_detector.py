from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Protocol

from codoc.diff_parser import extract_code_deltas, filter_code_deltas, load_file_content
from codoc.doc_parser import parse_markdown_text
from codoc.models import (
    ChangeDetectorConfig,
    CodeChunk,
    CodeDelta,
    DiffContext,
    DocSection,
    LinkEdge,
    LinkGraph,
    StalenessVerdict,
    SuspectSection,
)


class StalenessVerifier(Protocol):
    def verify_staleness(self, suspect: SuspectSection) -> StalenessVerdict:
        ...


def is_public_chunk(chunk: CodeChunk) -> bool:
    return chunk.is_public


def find_unlinked_new_symbols(
    deltas: list[CodeDelta],
    link_graph: LinkGraph,
) -> list[CodeChunk]:
    linked_ids = {edge.target_chunk_id for edge in link_graph.edges}
    unlinked: list[CodeChunk] = []
    seen: set[str] = set()

    for delta in deltas:
        if delta.delta_type == "added" and delta.new_chunk is not None:
            chunk = delta.new_chunk
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            if chunk.is_public and chunk.chunk_id not in linked_ids:
                unlinked.append(chunk)

    return unlinked


def _resolve_head_section(
    original_sec: DocSection,
    head_files: dict[str, str] | None,
    repo_root: Path | None,
) -> DocSection:
    rel_path = original_sec.file_path
    content = load_file_content(
        original_sec.file_path, head_files or {}, repo_root
    )
    if not content:
        return original_sec

    head_sections = parse_markdown_text(content, rel_path)
    for head_sec in head_sections:
        if (
            head_sec.heading_path == original_sec.heading_path
            or head_sec.section_id == original_sec.section_id
        ):
            # ponytail: heading_path or section_id match; upgrade to AST fuzzy section alignment when doc diffing lands
            return DocSection(
                section_id=original_sec.section_id,
                file_path=head_sec.file_path,
                heading_path=head_sec.heading_path,
                start_line=head_sec.start_line,
                end_line=head_sec.end_line,
                raw_content=head_sec.raw_content,
                content_hash=head_sec.content_hash,
                references=head_sec.references,
            )
    return original_sec

def resolve_suspect_sections(
    deltas: list[CodeDelta],
    link_graph: LinkGraph,
    max_suspects: int = 25,
    head_files: dict[str, str] | None = None,
    repo_root: Path | None = None,
) -> list[SuspectSection]:
    if not deltas:
        return []

    sections_by_id = {s.section_id: s for s in link_graph.doc_sections}
    edges_by_chunk_id: dict[str, list[LinkEdge]] = defaultdict(list)
    for edge in link_graph.edges:
        edges_by_chunk_id[edge.target_chunk_id].append(edge)

    suspect_deltas: dict[str, list[CodeDelta]] = defaultdict(list)
    suspect_scores: dict[str, float] = defaultdict(float)
    suspect_evidence: dict[str, set[str]] = defaultdict(set)

    for delta in deltas:
        candidate_ids: set[str] = set()
        if delta.chunk_id:
            candidate_ids.add(delta.chunk_id)
        if delta.old_chunk:
            candidate_ids.add(delta.old_chunk.chunk_id)
        if delta.new_chunk:
            candidate_ids.add(delta.new_chunk.chunk_id)

        matched_edges: list[LinkEdge] = []
        for cid in candidate_ids:
            matched_edges.extend(edges_by_chunk_id.get(cid, []))

        for edge in matched_edges:
            sec_id = edge.source_section_id
            if sec_id not in sections_by_id:
                continue
            if delta not in suspect_deltas[sec_id]:
                suspect_deltas[sec_id].append(delta)
            if edge.score > suspect_scores[sec_id]:
                suspect_scores[sec_id] = edge.score
            for ev in edge.evidence:
                suspect_evidence[sec_id].add(ev)

    suspects: list[SuspectSection] = []
    for sec_id, d_list in suspect_deltas.items():
        base_sec = sections_by_id[sec_id]
        head_sec = _resolve_head_section(base_sec, head_files, repo_root)
        suspects.append(
            SuspectSection(
                section=head_sec,
                deltas=tuple(d_list),
                score=suspect_scores[sec_id],
                evidence=tuple(sorted(suspect_evidence[sec_id])),
            )
        )

    # Order candidates by descending link score
    suspects.sort(key=lambda s: s.score, reverse=True)
    return suspects[:max_suspects]


def detect_stale_sections(
    diff_context: DiffContext,
    link_graph: LinkGraph,
    llm_client: StalenessVerifier,
    config: ChangeDetectorConfig | None = None,
    unlinked_symbols_out: list[CodeChunk] | None = None,
) -> list[StalenessVerdict]:
    cfg = config or ChangeDetectorConfig()
    deltas = extract_code_deltas(diff_context, ignore_globs=cfg.ignore_globs)
    if unlinked_symbols_out is not None:
        unlinked_symbols_out.extend(find_unlinked_new_symbols(deltas, link_graph))

    filtered = filter_code_deltas(deltas)
    meaningful_deltas = [r.delta for r in filtered if r.status == "meaningful"]

    suspects = resolve_suspect_sections(
        deltas=meaningful_deltas,
        link_graph=link_graph,
        max_suspects=cfg.max_suspects,
        head_files=diff_context.head_files,
        repo_root=diff_context.repo_root,
    )

    verdicts: list[StalenessVerdict] = []
    for suspect in suspects:
        verdict = llm_client.verify_staleness(suspect)
        if verdict.is_stale:
            verdicts.append(verdict)

    return verdicts
