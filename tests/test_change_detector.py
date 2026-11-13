from __future__ import annotations

from pathlib import Path
from codoc.change_detector import (
    StalenessVerifier,
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


class MockStalenessVerifier:
    def __init__(self, verdicts: dict[str, StalenessVerdict] | None = None) -> None:
        self.verdicts = verdicts or {}
        self.calls: list[SuspectSection] = []

    def verify_staleness(self, suspect: SuspectSection) -> StalenessVerdict:
        self.calls.append(suspect)
        sec_id = suspect.section.section_id
        if sec_id in self.verdicts:
            return self.verdicts[sec_id]
        return StalenessVerdict(
            section_id=sec_id,
            is_stale=True,
            diagnosis="Code modified without doc update",
            change_complexity="simple",
            confidence=0.9,
            suspect_section=suspect,
        )


def _make_dummy_chunk(chunk_id: str) -> CodeChunk:
    file_path, qname = chunk_id.split("::", 1)
    return CodeChunk(
        chunk_id=chunk_id,
        file_path=file_path,
        qualified_name=qname,
        symbol_name=qname.split(".")[-1],
        chunk_type="function",
        signature=f"def {qname}(): ...",
        docstring="Docstring.",
        start_line=1,
        end_line=5,
        raw_source=f"def {qname}():\n    return 1\n",
        content_hash="hash1",
    )


def _make_dummy_section(section_id: str) -> DocSection:
    file_path = section_id.split("::", 1)[0]
    return DocSection(
        section_id=section_id,
        file_path=file_path,
        heading_path="Usage > Details",
        start_line=1,
        end_line=10,
        raw_content="Call `calculate_discount` to get price.",
        content_hash="dochash1",
    )


def test_resolve_suspect_sections_matches_deltas_via_link_graph() -> None:
    chunk1 = _make_dummy_chunk("src/calc.py::add")
    chunk2 = _make_dummy_chunk("src/calc.py::sub")
    sec1 = _make_dummy_section("docs/calc.md::0")
    sec2 = _make_dummy_section("docs/calc.md::1")

    edge1 = LinkEdge(
        source_section_id="docs/calc.md::0",
        target_chunk_id="src/calc.py::add",
        link_type="heuristic",
        score=0.9,
        evidence=("exact match",),
    )
    edge2 = LinkEdge(
        source_section_id="docs/calc.md::1",
        target_chunk_id="src/calc.py::sub",
        link_type="heuristic",
        score=0.85,
        evidence=("exact match",),
    )

    graph = LinkGraph(
        version="1.0",
        code_chunks=(chunk1, chunk2),
        doc_sections=(sec1, sec2),
        edges=(edge1, edge2),
    )

    delta = CodeDelta(
        delta_type="modified",
        old_chunk=chunk1,
        new_chunk=chunk1,
    )

    suspects = resolve_suspect_sections([delta], graph)
    assert len(suspects) == 1
    assert suspects[0].section.section_id == "docs/calc.md::0"
    assert suspects[0].score == 0.9
    assert suspects[0].deltas == (delta,)


def test_detect_stale_sections_end_to_end_tracer_bullet() -> None:
    diff_text = """--- a/src/billing.py
+++ b/src/billing.py
@@ -1,2 +1,2 @@
 def calculate_discount(amount: float, rate: float = 0.1) -> float:
-    return amount * (1.0 - rate)
+    return amount * (1.0 - rate * 2.0)
"""
    base_src = """def calculate_discount(amount: float, rate: float = 0.1) -> float:
    return amount * (1.0 - rate)
"""
    head_src = """def calculate_discount(amount: float, rate: float = 0.1) -> float:
    return amount * (1.0 - rate * 2.0)
"""
    doc_content = """# Billing
## Discounts
Call `calculate_discount` with 10% rate for default discount.
"""
    chunk = _make_dummy_chunk("src/billing.py::calculate_discount")
    section = DocSection(
        section_id="docs/billing.md::1",
        file_path="docs/billing.md",
        heading_path="Billing > Discounts",
        start_line=1,
        end_line=4,
        raw_content=doc_content,
        content_hash="hash_doc",
    )
    edge = LinkEdge(
        source_section_id="docs/billing.md::1",
        target_chunk_id="src/billing.py::calculate_discount",
        link_type="heuristic",
        score=0.95,
        evidence=("exact reference `calculate_discount`",),
    )
    graph = LinkGraph(
        version="1.0",
        code_chunks=(chunk,),
        doc_sections=(section,),
        edges=(edge,),
    )

    diff_ctx = DiffContext(
        unified_diff=diff_text,
        base_files={"src/billing.py": base_src},
        head_files={"src/billing.py": head_src, "docs/billing.md": doc_content},
    )

    expected_verdict = StalenessVerdict(
        section_id="docs/billing.md::1",
        is_stale=True,
        diagnosis="Discount calculation doubled rate multiplier without documentation reflection.",
        change_complexity="simple",
        confidence=0.98,
    )
    verifier = MockStalenessVerifier(verdicts={"docs/billing.md::1": expected_verdict})

    verdicts = detect_stale_sections(diff_ctx, graph, verifier)

    assert len(verdicts) == 1
    assert verdicts[0].section_id == "docs/billing.md::1"
    assert verdicts[0].is_stale is True
    assert "doubled rate" in verdicts[0].diagnosis
    assert len(verifier.calls) == 1
    assert verifier.calls[0].section.section_id == "docs/billing.md::1"


def test_detect_stale_sections_skips_comment_only_diff() -> None:
    diff_text = """--- a/src/billing.py
+++ b/src/billing.py
@@ -1,2 +1,3 @@
 def calculate_discount(amount: float, rate: float = 0.1) -> float:
+    # Just adding an internal explanation
     return amount * (1.0 - rate)
"""
    base_src = """def calculate_discount(amount: float, rate: float = 0.1) -> float:
    return amount * (1.0 - rate)
"""
    head_src = """def calculate_discount(amount: float, rate: float = 0.1) -> float:
    # Just adding an internal explanation
    return amount * (1.0 - rate)
"""
    chunk = _make_dummy_chunk("src/billing.py::calculate_discount")
    section = _make_dummy_section("docs/billing.md::1")
    edge = LinkEdge(
        source_section_id="docs/billing.md::1",
        target_chunk_id="src/billing.py::calculate_discount",
        link_type="heuristic",
        score=0.95,
    )
    graph = LinkGraph(
        version="1.0",
        code_chunks=(chunk,),
        doc_sections=(section,),
        edges=(edge,),
    )
    diff_ctx = DiffContext(
        unified_diff=diff_text,
        base_files={"src/billing.py": base_src},
        head_files={"src/billing.py": head_src},
    )
    verifier = MockStalenessVerifier()

    verdicts = detect_stale_sections(diff_ctx, graph, verifier)

    assert len(verdicts) == 0
    assert len(verifier.calls) == 0
