from __future__ import annotations

from codoc.change_detector import (
    detect_stale_sections,
    resolve_suspect_sections,
)
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


class DummyVerifier:
    def __init__(self) -> None:
        self.suspects: list[SuspectSection] = []

    def verify_staleness(self, suspect: SuspectSection) -> StalenessVerdict:
        self.suspects.append(suspect)
        return StalenessVerdict(
            section_id=suspect.section.section_id,
            is_stale=True,
            diagnosis="Stale section",
            change_complexity="simple",
            confidence=0.9,
            suspect_section=suspect,
        )


def _make_chunk(chunk_id: str) -> CodeChunk:
    file_path, qname = chunk_id.split("::", 1)
    return CodeChunk(
        chunk_id=chunk_id,
        file_path=file_path,
        qualified_name=qname,
        symbol_name=qname.split(".")[-1],
        chunk_type="function",
        signature=f"def {qname}(): ...",
        docstring=None,
        start_line=1,
        end_line=5,
        raw_source=f"def {qname}():\n    return 1\n",
        content_hash="h",
    )


def test_multi_delta_bundling_into_single_suspect_section() -> None:
    chunk_a = _make_chunk("src/math.py::add")
    chunk_b = _make_chunk("src/math.py::multiply")

    sec = DocSection(
        section_id="docs/math.md#Math Operations",
        file_path="docs/math.md",
        heading_path="Math Operations",
        start_line=1,
        end_line=10,
        raw_content="Math operations documentation.",
        content_hash="dochash",
    )

    edge_a = LinkEdge(
        source_section_id=sec.section_id,
        target_chunk_id=chunk_a.chunk_id,
        link_type="heuristic",
        score=0.8,
    )
    edge_b = LinkEdge(
        source_section_id=sec.section_id,
        target_chunk_id=chunk_b.chunk_id,
        link_type="heuristic",
        score=0.95,
    )

    graph = LinkGraph(
        version="1.0",
        code_chunks=(chunk_a, chunk_b),
        doc_sections=(sec,),
        edges=(edge_a, edge_b),
    )

    delta_a = CodeDelta(delta_type="modified", old_chunk=chunk_a, new_chunk=chunk_a)
    delta_b = CodeDelta(delta_type="modified", old_chunk=chunk_b, new_chunk=chunk_b)

    suspects = resolve_suspect_sections([delta_a, delta_b], graph)
    assert len(suspects) == 1
    suspect = suspects[0]
    assert suspect.section.section_id == sec.section_id
    assert len(suspect.deltas) == 2
    assert set(d.chunk_id for d in suspect.deltas) == {chunk_a.chunk_id, chunk_b.chunk_id}
    assert suspect.score == 0.95


def test_same_pr_doc_resolution_uses_head_markdown() -> None:
    chunk = _make_chunk("src/auth.py::login")
    base_sec = DocSection(
        section_id="docs/auth.md#Login API",
        file_path="docs/auth.md",
        heading_path="Login API",
        start_line=1,
        end_line=5,
        raw_content="# Login API\nOld login instructions.\n",
        content_hash="basehash",
    )
    edge = LinkEdge(
        source_section_id=base_sec.section_id,
        target_chunk_id=chunk.chunk_id,
        link_type="heuristic",
        score=0.9,
    )
    graph = LinkGraph(
        version="1.0",
        code_chunks=(chunk,),
        doc_sections=(base_sec,),
        edges=(edge,),
    )
    delta = CodeDelta(delta_type="modified", old_chunk=chunk, new_chunk=chunk)

    head_doc = "# Login API\nUpdated login instructions reflecting new parameters.\n"
    head_files = {"docs/auth.md": head_doc}

    suspects = resolve_suspect_sections([delta], graph, head_files=head_files)
    assert len(suspects) == 1
    assert "Updated login instructions" in suspects[0].section.raw_content


def test_deleted_doc_section_in_head_is_omitted() -> None:
    chunk = _make_chunk("src/legacy.py::deprecated_tool")
    base_sec = DocSection(
        section_id="docs/legacy.md#Deprecated Tool",
        file_path="docs/legacy.md",
        heading_path="Deprecated Tool",
        start_line=1,
        end_line=5,
        raw_content="# Deprecated Tool\nLegacy tool docs.\n",
        content_hash="oldhash",
    )
    edge = LinkEdge(
        source_section_id=base_sec.section_id,
        target_chunk_id=chunk.chunk_id,
        link_type="heuristic",
        score=0.9,
    )
    graph = LinkGraph(
        version="1.0",
        code_chunks=(chunk,),
        doc_sections=(base_sec,),
        edges=(edge,),
    )
    delta = CodeDelta(delta_type="modified", old_chunk=chunk, new_chunk=chunk)

    # In PR head, docs/legacy.md removed the Deprecated Tool section
    head_doc = "# Legacy Overview\nOnly general notes here.\n"
    head_files = {"docs/legacy.md": head_doc}

    suspects = resolve_suspect_sections([delta], graph, head_files=head_files)
    assert len(suspects) == 0


def test_max_suspects_budget_capping_and_truncation_metadata() -> None:
    chunks: list[CodeChunk] = []
    sections: list[DocSection] = []
    edges: list[LinkEdge] = []
    deltas: list[CodeDelta] = []

    for i in range(30):
        chunk = _make_chunk(f"src/mod_{i}.py::fn_{i}")
        sec = DocSection(
            section_id=f"docs/doc_{i}.md#Heading",
            file_path=f"docs/doc_{i}.md",
            heading_path="Heading",
            start_line=1,
            end_line=5,
            raw_content=f"Doc {i}",
            content_hash=f"h_{i}",
        )
        score = 0.5 + (i * 0.01)
        edge = LinkEdge(
            source_section_id=sec.section_id,
            target_chunk_id=chunk.chunk_id,
            link_type="heuristic",
            score=score,
        )
        delta = CodeDelta(delta_type="modified", old_chunk=chunk, new_chunk=chunk)

        chunks.append(chunk)
        sections.append(sec)
        edges.append(edge)
        deltas.append(delta)
    graph = LinkGraph(
        version="1.0",
        code_chunks=tuple(chunks),
        doc_sections=tuple(sections),
        edges=tuple(edges),
    )

    metadata: dict[str, object] = {}
    suspects = resolve_suspect_sections(
        deltas=deltas,
        link_graph=graph,
        max_suspects=5,
        metadata_out=metadata,
    )

    assert len(suspects) == 5
    assert metadata["truncated"] is True
    assert metadata["total_suspects"] == 30
    assert metadata["max_suspects"] == 5
    # Verify ordered by descending score
    scores = [s.score for s in suspects]
    assert scores == sorted(scores, reverse=True)


def test_pipeline_same_pr_doc_resolution_and_budget_truncation() -> None:
    diff_text = """--- a/src/service.py
+++ b/src/service.py
@@ -1,2 +1,2 @@
 def serve() -> None:
-    start_v1()
+    start_v2()
--- a/docs/service.md
+++ b/docs/service.md
@@ -1,3 +1,3 @@
 # Service
 ## Run
-Call `serve` to launch v1.
+Call `serve` to launch v2.
"""
    base_service = "def serve() -> None:\n    start_v1()\n"
    head_service = "def serve() -> None:\n    start_v2()\n"
    base_doc = "# Service\n## Run\nCall `serve` to launch v1.\n"
    head_doc = "# Service\n## Run\nCall `serve` to launch v2.\n"

    chunk = _make_chunk("src/service.py::serve")
    sec = DocSection(
        section_id="docs/service.md#Service > Run",
        file_path="docs/service.md",
        heading_path="Service > Run",
        start_line=1,
        end_line=3,
        raw_content=base_doc,
        content_hash="bh",
    )
    edge = LinkEdge(
        source_section_id=sec.section_id,
        target_chunk_id=chunk.chunk_id,
        link_type="heuristic",
        score=0.9,
    )
    graph = LinkGraph(
        version="1.0",
        code_chunks=(chunk,),
        doc_sections=(sec,),
        edges=(edge,),
    )

    diff_ctx = DiffContext(
        unified_diff=diff_text,
        base_files={"src/service.py": base_service, "docs/service.md": base_doc},
        head_files={"src/service.py": head_service, "docs/service.md": head_doc},
    )

    class SamePrDocVerifier:
        def __init__(self) -> None:
            self.observed_doc_contents: list[str] = []

        def verify_staleness(self, suspect: SuspectSection) -> StalenessVerdict:
            self.observed_doc_contents.append(suspect.section.raw_content)
            # Since doc was already updated in head to v2, it is not stale!
            is_stale = "v2" not in suspect.section.raw_content
            return StalenessVerdict(
                section_id=suspect.section.section_id,
                is_stale=is_stale,
                diagnosis="Accurate" if not is_stale else "Stale",
                change_complexity="simple",
                confidence=1.0,
            )

    verifier = SamePrDocVerifier()
    metadata: dict[str, object] = {}
    stale_verdicts = detect_stale_sections(
        diff_context=diff_ctx,
        link_graph=graph,
        llm_client=verifier,
        metadata_out=metadata,
    )

    assert len(verifier.observed_doc_contents) == 1
    assert "launch v2" in verifier.observed_doc_contents[0]
    # Because doc was updated in same PR, it was evaluated as accurate, so no stale sections detected
    assert len(stale_verdicts) == 0
    assert metadata.get("truncated") is False


def test_pipeline_multi_delta_bundling_and_budget_truncation() -> None:
    diff_text = """--- a/src/math.py
+++ b/src/math.py
@@ -1,5 +1,5 @@
 def add(a: int, b: int) -> int:
-    return a + b
+    return a + b + 1
 
 def multiply(a: int, b: int) -> int:
-    return a * b
+    return a * b * 2
--- a/src/other.py
+++ b/src/other.py
@@ -1,2 +1,2 @@
 def extra():
-    pass
+    return True
"""
    chunk_add = _make_chunk("src/math.py::add")
    chunk_mul = _make_chunk("src/math.py::multiply")
    chunk_extra = _make_chunk("src/other.py::extra")

    sec_math = DocSection(
        section_id="docs/math.md#Math Operations",
        file_path="docs/math.md",
        heading_path="Math Operations",
        start_line=1,
        end_line=5,
        raw_content="Math ops.",
        content_hash="h1",
    )
    sec_other = DocSection(
        section_id="docs/other.md#Other",
        file_path="docs/other.md",
        heading_path="Other",
        start_line=1,
        end_line=5,
        raw_content="Other ops.",
        content_hash="h2",
    )

    edge_add = LinkEdge(source_section_id=sec_math.section_id, target_chunk_id=chunk_add.chunk_id, link_type="heuristic", score=0.8)
    edge_mul = LinkEdge(source_section_id=sec_math.section_id, target_chunk_id=chunk_mul.chunk_id, link_type="heuristic", score=0.9)
    edge_extra = LinkEdge(source_section_id=sec_other.section_id, target_chunk_id=chunk_extra.chunk_id, link_type="heuristic", score=0.7)

    graph = LinkGraph(
        version="1.0",
        code_chunks=(chunk_add, chunk_mul, chunk_extra),
        doc_sections=(sec_math, sec_other),
        edges=(edge_add, edge_mul, edge_extra),
    )

    base_math = "def add(a: int, b: int) -> int:\n    return a + b\n\ndef multiply(a: int, b: int) -> int:\n    return a * b\n"
    head_math = "def add(a: int, b: int) -> int:\n    return a + b + 1\n\ndef multiply(a: int, b: int) -> int:\n    return a * b * 2\n"
    base_other = "def extra():\n    pass\n"
    head_other = "def extra():\n    return True\n"

    diff_ctx = DiffContext(
        unified_diff=diff_text,
        base_files={"src/math.py": base_math, "src/other.py": base_other},
        head_files={"src/math.py": head_math, "src/other.py": head_other},
    )

    verifier = DummyVerifier()
    metadata: dict[str, object] = {}
    # Test budget cap = 1 to verify truncation in pipeline
    config = ChangeDetectorConfig(max_suspects=1)
    verdicts = detect_stale_sections(
        diff_context=diff_ctx,
        link_graph=graph,
        llm_client=verifier,
        config=config,
        metadata_out=metadata,
    )

    # math section had 2 deltas bundled together and highest score (0.9 vs 0.7)
    assert len(verifier.suspects) == 1
    bundled_suspect = verifier.suspects[0]
    assert bundled_suspect.section.section_id == sec_math.section_id
    assert len(bundled_suspect.deltas) == 2
    assert metadata["truncated"] is True
    assert metadata["total_suspects"] == 2
    assert metadata["max_suspects"] == 1
    assert len(verdicts) == 1
